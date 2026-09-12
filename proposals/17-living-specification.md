# 17 — Living Specification: ADR ↔ Tasks ↔ Code ↔ Docs drift detector

> Category: 🟡 Adaptation · Risk: medium · Dependencies: 07-routines, ADR-system (a73dcbb), OBI code-ref
> · **Note (2026-09-02):** the external part (cross-repo structure/scenario
> contour) moves to [proposal 24](24-structure-contracts-scenarios.md);
> the `adr_drift` routine in cod-doc stays complementary.

## Context: drift between "how it should be" and "how it is"

cod-doc already has **3 levels of specification**:
1. **ADR** (`adr_*` tools) — a fixed architectural decision (Context / Decision / Alternatives / Consequences).
2. **Plan + Tasks** (`plan_*`, `task_*`) — work decomposition.
3. **Code** — the real implementation.

Current pain: **no one checks** that these three levels agree. Typical drifts:
- ADR-001 says "we use Snowball Protocol for context" — but half of `agent_*-tools` read the whole MASTER.md without level-filtering.
- Task `COD-456` is marked `acceptance: update docs/system/DATA_MODEL.md` — but a month after `done` the docs are stale.
- ADR-005 is declared superseded, but the code still uses the old abstraction.

## Current state of cod-doc

- ADR-system (a73dcbb, ADR-001..008): web-pages list/detail/new/graph, CLI, migrator, MCP tools (`adr_create`, `adr_supersede`, `adr_deprecate`, `adr_link_task`).
- 7-state TaskStatus (proposal 08), atomic checkout (proposal 06), run-id audit (proposal 04).
- Code-ref parser (OBI-020/021, OBI-030/040) — links files ↔ entities ↔ FTS5.
- `07-routines` (PCA-211) — cron-style health checks on a schedule.
- **Missing:** a rule that **on an ongoing basis** checks the consistency of ADR ↔ tasks ↔ code ↔ docs.

## Proposal

Extend routines (proposal 07) with a new type `routine.kind='adr_drift'`. Create `cod_doc/services/adr_drift_detector.py`:

### 4.1. What is checked

| Check | Source | Drift = |
|---|---|---|
| ADR-claim vs code | `adr_create` field `claim_code_refs` vs `commit_link_service.search_by_adr` | ADR-001 lists 3 modules that must use Snowball Protocol. If a new `cod_doc/agent/*.py` does not call `context_get(level=...)` — drift. |
| ADR-superseded chain | `adr_supersede` linked ADR | Old `ADR-NNN` declared superseded → new `ADR-NNN`. If the code still imports a module from the old ADR — drift. |
| Task acceptance vs doc actuality | `task.acceptance` vs `doc.body` sha | Task COD-789 acceptance: "update docs/system/DATA_MODEL.md". If the last commit in this file was >7 days ago, and the task is `done` — drift. |
| ADR-graph reachability | `adr_graph` API | An ADR in status `ACCEPTED`, but has no linked task and no `adr_link_task` for 30 days → orphan. |
| Doc hash vs source | `update_master_hashes` (existing) | Already works; integrate into the same routine. |

### 4.2. Severity

| Severity | Condition | Action |
|---|---|---|
| 🔴 BLOCKER | ADR-superseded chain broken in code | `approval_request(approval_type='adr_drift', severity='blocker', ...)` |
| 🟡 WARNING | Task done >7 days, and doc not updated | `activity_log.emit(event='adr_drift_warning', ...)` + Telegram notification (opt.) |
| 🟢 INFO | Orphan ADR (no task for 30 days) | `audit_*` collects into `docs/system/audit/adr-drift-<date>.md` |

### 4.3. Routine registration

```python
# cod_doc/services/adr_drift_detector.py
class ADRSpec(Protocol):
    def check_claim_vs_code(self, adr: ADR) -> list[DriftIssue]: ...
    def check_superseded_chain(self) -> list[DriftIssue]: ...
    def check_task_acceptance(self) -> list[DriftIssue]: ...

# In routines:
routine_register(
    name="adr_drift_daily",
    schedule="0 6 * * *",  # every day at 6 am
    handler="cod_doc.services.adr_drift_detector:run_daily",
    severity_threshold="warning",  # below this severity stays silent
)
```

### 4.4. MCP surface (opt.)

```
adr_drift_check(project?, since_days=7) -> list[DriftIssue]
adr_drift_register_check(spec_name, spec_body_yaml)  # custom checks
```

## Effect

- **Audit on autopilot.** Once a day — a report "what diverged between ADR and code".
- **Blocking drifts do not pass silently.** If ADR-002 is superseded, and `agent_service.py` imports `LegacyAgentAdapter` — CI / pre-commit catches it.
- **Binding ADR to live documentation.** Each ADR has evidence: "here is the code that implements it; here are the tasks that closed it; here are the docs that explain it".

## Dependencies

| Proposal / component | Needed for |
|---|---|
| `07-routines` (PCA-211) | cron-style health check infrastructure |
| ADR-system (a73dcbb) | source of ADRs and their statuses |
| `09-activity-log` (PCA-912) | where to write `adr_drift_warning` |
| `12-approvals` (PCA-121) | channel for blockers |
| OBI-020/021 (code-ref) | link ADR ↔ files ↔ tasks |

## Structure

```
cod_doc/services/
├── adr_drift_detector.py        # core
├── drift_specs/                 # catalog of specs
│   ├── claim_vs_code.py
│   ├── superseded_chain.py
│   ├── task_acceptance.py
│   └── orphan_adr.py
tests/services/
└── test_adr_drift_detector.py
cod_doc/mcp/tools/
└── adr_drift_tools.py           # MCP surface (opt.)
```

## Risks and mitigation

| Risk | Mitigation |
|---|---|
| False positives in `claim_vs_code` | Specs are written manually, not auto-extracted from ADR-body. The ADR-author explicitly lists `claim_code_refs: ["cod_doc/agent/..."]` |
| Routine starts slowing the project (long git operations) | Rate-limit: routine runs no more than once / 6 hours; manual `adr_drift_check` for ad-hoc |
| Severity inflation (everything becomes "blocker") | `severity_threshold` in routine; first 2 weeks — WARN, then raise |
| Drift between specs and reality | a spec is code, covered by pytest |

## Acceptance criteria

1. `adr_drift_daily` routine is registered, runs on schedule, does not crash on an empty DB.
2. `check_superseded_chain` catches a real test-case (fixture: `ADR-NNN` superseded → `ADR-NNN`, code imports the old abstraction).
3. `check_task_acceptance` writes to `activity_log` for tasks with status=done >7 days.
4. MCP `adr_drift_check` returns a structured list of issues with severity.
5. `docs/system/audit/<date>-adr-drift.md` is generated automatically when `INFO`-severity issues >0.

## Alternatives

- **A weekly manual review of all ADRs** — does not scale, after a month you give up.
- **Parse ADR text via LLM** — fragile, expensive, not deterministic.
- **Strict typed links (ADR-claim → function-name)** — already partially there via OBI; extend gradually.

## Sources

- Real pain: cod-doc itself (`ADR-NNN` superseded → `ADR-NNN`, but `arch/architecture.md` still mentions the old structure).
- Paperclip [`routines/`](https://github.com/paperclipai/paperclip) — the cron-style health check pattern.
