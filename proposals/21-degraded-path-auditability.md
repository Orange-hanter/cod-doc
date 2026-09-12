# 21 — Degraded-Path Auditability + Error Audit Trail (hybrid)

> Category: 🟡 Adaptation · Risk: medium · Dependencies: proposal 04 (run-id), proposal 09 (activity log), proposal 17 (Living Specification)

## Context: what two reviews showed

On 2026-06-04 a **parallel** review of COD-DOC was conducted by two independent models (`miniMax-m3` and `deepseek-v4-pro`). Both found the same central problem — **visibility of degraded paths and errors in production**:

- In the code there are **8 places** with `# pragma: no cover` (degraded path / defensive guard).
- Degraded paths **never execute** in tests, but are critical for production (event_bus deferred commit, run_context project resolution, websocket cleanup).
- Service-level exceptions (`TaskNotFoundError`, `StatusTransitionError`, `TaskBlockedError`) are **not** written structurally — only HTTP 500 / MCP error response.
- `activity_log` (proposal 09) writes **successful** events, but not errors.

**Matched in both reports:**
- 5/5 top weaknesses (cycle-5 stubs, legacy YAML/DB duplicates, pragma:no cover, skill matcher fragility, Config.load I/O).
- 11/15 top observations including: AGT-003..007 stubs, 5 legacy_*.py files (863 lines of dead code), `task: Any` workaround in `agent_service.py:119`, `event_bus._subscribers` global state leak, `Config.load()` without a cache.

**Divergence in the approach to RFC #21:**

| Approach | Pro | Mine | Compromise |
|---|---|---|---|
| Storage for hard errors (TaskNotFound, StatusTransition) | DB-table `error_audit` | (did not propose) | **DB-table** — for post-mortem |
| Storage for soft failures (degraded paths, defensive guards) | (did not propose) | In-memory ring buffer 100 | **Ring buffer** — cheap, does not bloat the DB |
| Trigger | Decorator / context manager | `@degraded_path("CODE")` | **Both** |
| Visibility | Web UI + MCP | WebSocket via activity event | **Both** |

This RFC is a **hybrid** of two approaches: different storage tiers for different classes of errors.

## Current state

Found 8 places with `pragma: no cover`:

| File:line | Code (new) | Severity | Purpose |
|---|---|---|---|
| `event_bus.py:123` | `EVENT_FLUSH_FAILED` | warning | deferred event flush after commit |
| `event_bus.py:132` | `EVENT_DROP_FAILED` | warning | deferred event drop after rollback |
| `run_context.py:140` | `RUN_CONTEXT_DB_MISSING` | warning | orchestrator did not find the project DB |
| `run_context.py:179` | `RUN_START_DEGRADED` | warning | run_scope started degraded |
| `run_context.py:214` | `RUN_FINALIZE_DEGRADED` | warning | run_scope finished degraded |
| `websocket.py:48` | `WS_CLEANUP_FAILED` | warning | WS connection cleanup |
| `routine_service.py:463` | `ROUTINE_DEFENSIVE_GUARD` | info | routine defensive guard |
| `adapters/registry.py:93` | `PLUGIN_LOAD_FAILED` | info | best-effort plugin loading |

**Existing infrastructure:**
- `cod_doc/services/activity_service.py` — `emit/list/get` (proposal 09).
- `cod_doc/infra/models/traces.py` — `ToolTraceModel` (successful tool calls).
- `cod_doc/services/run_context.py` — `run_scope` with contextvar `run_id`.

## Proposal

### 5.1. Two storage tiers

**Tier 1: In-memory ring buffer for soft failures (degraded paths).**

```python
# cod_doc/services/degraded_audit.py
@dataclass(slots=True)
class DegradedPathRecord:
    code: str          # "EVENT_FLUSH_FAILED", "RUN_CONTEXT_DB_MISSING", ...
    location: str      # "cod_doc/services/event_bus.py:123"
    severity: str      # "info" | "warning"
    context: dict      # {project, run_id, agent_id, ...}
    ts: float

class DegradedPathAudit:
    """Process-local ring buffer (capacity 100) + counter by code.
    
    Lost on restart — this is intentional: it is a **runtime metric**,
    not an audit log. Does not bloat the DB.
    """
    def record(self, code: str, location: str, **context) -> None: ...
    def stats(self) -> dict[str, int]: ...         # code → count (accumulating)
    def recent(self, limit: int = 50) -> list[DegradedPathRecord]: ...
    def reset(self) -> None: ...                   # for tests
```

**Tier 2: DB-table `error_audit` for hard exceptions.**

```sql
CREATE TABLE error_audit (
    row_id          INTEGER PRIMARY KEY,
    run_id          TEXT,                        -- FK to agent_run (proposal 04)
    project_id      INTEGER REFERENCES project(row_id),
    task_id         TEXT,                        -- nullable
    error_code      TEXT NOT NULL,               -- "TASK_NOT_FOUND", "TRANSITION_DENIED", "DB_DEGRADED"
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    message         TEXT NOT NULL,
    context_json    TEXT,                        -- JSON blob
    traceback       TEXT,                        -- stack trace, truncated to 4KB
    created         TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_error_audit_run_id ON error_audit(run_id);
CREATE INDEX idx_error_audit_project ON error_audit(project_id, created);
```

```python
# cod_doc/services/error_audit_service.py
class ErrorAuditService:
    def record(
        self,
        session: Session,
        *,
        error_code: str,
        severity: str,
        message: str,
        project_id: int | None = None,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
        traceback: str | None = None,
    ) -> ErrorAuditRecord: ...
```

### 5.2. Decorators / context managers

**Soft (Tier 1):**
```python
@degraded_path("EVENT_FLUSH_FAILED")
def _flush_pending_events(session: Session) -> None: ...

@contextmanager
def degraded_path(code: str, *, severity: str = "warning", **context):
    """Catches exceptions, records a degraded-path event, **re-raises**."""
    try:
        yield
    except Exception as exc:
        DegradedPathAudit().record(
            code=code,
            location=f"{__name__}:{sys._getframe(1).f_code.co_name}",
            severity=severity,
            error_type=type(exc).__name__,
            error_msg=str(exc),
            **context,
        )
        raise
```

**Hard (Tier 2):**
```python
@contextmanager
def audit_errors(
    session: Session,
    *,
    error_code_prefix: str = "SVC",
    project_id: int | None = None,
    task_id: str | None = None,
):
    """Wraps a service call — catches exceptions, writes an error_audit row, re-raises."""
    try:
        yield
    except Exception as exc:
        # traceback truncated to 4KB
        tb = traceback.format_exc()[:4096]
        ErrorAuditService.record(
            session, error_code=f"{error_code_prefix}_{type(exc).__name__}",
            severity="error", message=str(exc),
            project_id=project_id, task_id=task_id, traceback=tb,
        )
        raise
```

### 5.3. Application to existing points

**Tier 1 (degraded paths) — wrap 8 places:**

| File:line | Wrap as |
|---|---|
| `event_bus.py:123` | `@degraded_path("EVENT_FLUSH_FAILED")` |
| `event_bus.py:132` | `@degraded_path("EVENT_DROP_FAILED")` |
| `run_context.py:140` | `with degraded_path("RUN_CONTEXT_DB_MISSING", project_path=project_path):` |
| `run_context.py:179` | `with degraded_path("RUN_START_DEGRADED", run_id=run_id):` |
| `run_context.py:214` | `with degraded_path("RUN_FINALIZE_DEGRADED", run_id=run_id):` |
| `websocket.py:48` | `@degraded_path("WS_CLEANUP_FAILED")` |
| `routine_service.py:463` | `@degraded_path("ROUTINE_DEFENSIVE_GUARD", severity="info")` |
| `adapters/registry.py:93` | `@degraded_path("PLUGIN_LOAD_FAILED", severity="info")` |

**Tier 2 (hard exceptions) — wrap service-layer raises:**

| File | Change |
|---|---|
| `task_service.py` (20+ places) | `with audit_errors(session, error_code_prefix="TASK", project_id=..., task_id=...):` |
| `adr_service.py` | `with audit_errors(session, error_code_prefix="ADR"):` |
| `doc_service.py` | `with audit_errors(session, error_code_prefix="DOC"):` |
| `plan_service/` | `with audit_errors(session, error_code_prefix="PLAN"):` |
| `infra/repositories/base.py: get()/add()` | Wrap DB-raises |

### 5.4. MCP surface

```
# Tier 1 (in-memory, cheap calls)
degraded_audit_stats() -> {code: count}
degraded_audit_recent(limit=50, code?) -> list[DegradedPathRecord]
degraded_audit_reset() -> None  # admin only

# Tier 2 (DB-backed, for post-mortem)
error_audit_list(project?, run_id?, task_id?, severity?, limit=50, offset=0) -> list[ErrorAuditRecord]
error_audit_get(row_id) -> ErrorAuditRecord
error_audit_count_by_severity(project, since_days=7) -> {severity: count}
```

### 5.5. Web UI

- `/admin/health` — a widget with degraded_audit stats (counter + last trigger).
- `/p/<project>/errors` — an error_audit table with a filter-bar (by severity, error_code, task_id, date).
- WebSocket event `error.occurred` on `severity='critical'`.

### 5.6. Activity events

Every degraded-path / hard-error trigger emits an activity event:
```python
activity_service.emit(
    kind="degraded_path.triggered",  # or "error.occurred"
    project=project_id,
    payload={"code": code, "location": location, "severity": severity, "context": context},
    run_id=get_current_run_id(),
)
```

WebSocket subscribers receive these events in real-time → the live UI shows a red indicator.

## Effect

| Metric | Before | After |
|---|---|---|
| Visibility of a degraded path | 0 (silent) | Real-time counter + last 100 in-memory |
| Post-mortem in 3 months | Impossible (no persistence) | DB-table `error_audit` |
| MTTD (mean time to detect) | Hours (tail -f logs) | Minutes (`degraded_audit_stats()`) |
| CI signal on a degraded-path regression | None | Tests for each of the 8 codes |
| WebSocket "stale state" debugging | Unclear | `error_audit` shows `EVENT_FLUSH_FAILED` |
| Code coverage | `# pragma: no cover` × 8 | 100% (degraded paths are tested) |
| Storage growth | Zero | Minimal: in-memory ring buffer + 1 DB-table for hard errors |

## Dependencies

| Proposal / component | Needed for |
|---|---|
| `04-run-id` (proposal 04) | `run_id` in contextvar, propagated into context |
| `09-activity-log` (proposal 09) | `activity_service.emit(kind='error.occurred')` |
| `17-Living-Specification` (hackathon-track 17) | Drift detector relies on degraded_audit stats |
| `event_bus._subscribers` dispose (from analysis) | Correct shutdown, otherwise ring buffer is lost strangely |

## Structure

```
cod_doc/services/
├── degraded_audit.py                # Tier 1: in-memory ring buffer + decorator
├── error_audit_service.py           # Tier 2: DB-backed ErrorAuditService + audit_errors()
cod_doc/infra/models/
├── degraded_path.py                 # (opt.) DB-model for degraded path, if we decide Tier 1 also persists
├── error_audit.py                   # SQLAlchemy model
cod_doc/infra/migrations/versions/
└── <rev>_add_error_audit.py         # alembic
cod_doc/mcp/tools/
├── degraded_audit_tools.py          # MCP surface Tier 1
└── error_audit_tools.py             # MCP surface Tier 2
cod_doc/api/
└── routes_errors.py                 # /p/<project>/errors web page
cod_doc/templates/web/
└── errors.html                      # filter-bar table
tests/services/
├── test_degraded_audit.py           # 8 unit-tests (one per code)
├── test_error_audit_service.py      # commit-rollback isolation
└── test_audit_errors_decorator.py   # re-raise check
```

## Risks and mitigation

| Risk | Mitigation |
|---|---|
| Ring buffer overflows (capacity 100) in a storm | FIFO eviction + a `dropped: N` counter in stats |
| `audit_errors` itself crashes on write → recursive error | Wrap in `try/except` with `logging.exception()` as last resort |
| `degraded_path` decorator masks a bug (wrapper instead of fix) | Comment in code: "# degraded path: record the trigger, then fix the root cause" |
| DB-table `error_audit` bloats | TTL-cleanup: `DELETE FROM error_audit WHERE created < datetime('now', '-90 days')` via routine (proposal 07) |
| Web UI on `/admin/health` discloses security info | Through the existing `task_role` check; degraded_audit counter — open, hard errors — admin-only |
| False sense of security (tests for degraded paths) | Tests check **the trigger**, not the absence. If a degraded path is not needed — delete, do not test. |

## Acceptance Criteria

### Tier 1 (in-memory)
- [ ] `cod_doc/services/degraded_audit.py` exists, covered by unit tests.
- [ ] 8 places with `pragma: no cover` are wrapped in `@degraded_path(...)` or `with degraded_path(...):`.
- [ ] `tests/services/test_degraded_audit.py` — a test for each of the 8 codes.
- [ ] `degraded_audit_stats()` via MCP returns `{code: count}` after a trigger.
- [ ] `degraded_audit_recent(limit=10)` returns the last 10 records in FIFO order.
- [ ] Ring buffer capacity 100: 200 triggers → `dropped: 100` in stats.
- [ ] `activity_log` contains `kind='degraded_path.triggered'` on a trigger.
- [ ] WebSocket receives a degraded_path event in real-time.

### Tier 2 (DB)
- [ ] `alembic upgrade head` creates the `error_audit` table with indexes.
- [ ] `ErrorAuditService.record()` writes a row on call.
- [ ] `audit_errors(...)` context manager writes a row + re-raises (caller still sees the exception).
- [ ] `error_audit_list(project, severity='critical')` returns the correct list.
- [ ] Web UI `/p/<project>/errors` shows a table with filtering.
- [ ] Test for commit-rollback isolation: an error inside a transaction → rollback → `error_audit` is written in a **separate** transaction (not rolled back together).
- [ ] TTL-cleanup via routine: `DELETE FROM error_audit WHERE created < now-90d`.

## Alternatives

1. **Only logging (current state)** — solves "see", but not "count" and not "persist".
2. **Only DB-table (proposal pro)** — solves persistence, but bloats the DB on degraded paths that don't deserve attention.
3. **Only ring buffer (proposal mine)** — solves runtime-visibility, but does not give a post-mortem in 3 months.
4. **Sentry / APM** — external dependency, not suitable for self-hosted.
5. **Extend activity_log (proposal 09)** — cheaper, but activity log does not store traceback and is not meant for long-term error storage.

## Sources

- `cod_doc/services/event_bus.py:122-132` — deferred commit/rollback hooks.
- `cod_doc/services/run_context.py:140,179,214` — degraded paths.
- `cod_doc/services/task_service.py` (851 lines, 20+ raises) — service exceptions.
- `cod_doc/infra/models/traces.py` — `ToolTraceModel` (pattern for a SQLAlchemy model).
- `cod_doc/agent/adapters/base.py:115-137` — `@runtime_checkable Protocol` (pattern for type-safe wrappers).
- 2026-06-04 self-improvement comparison (miniMax-m3 vs deepseek-v4-pro) — audit report `2026-06-04-self-improvement-compared.md`.
- Paperclip `wake-payload pattern` — the pattern "agent gets structured error context, not a text log".
