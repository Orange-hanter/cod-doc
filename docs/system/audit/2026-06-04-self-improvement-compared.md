# 2026-06-04 — Self-Improvement Compared (miniMax-m3 vs deepseek-v4-pro)

> 📊 Meta: `{"scope": "self-audit", "models": ["miniMax-m3 (Hermes)", "deepseek-v4-pro (openrouter)"], "method": "independent parallel review", "result": "hybrid RFC #21 proposed"}`

## TL;DR

A two-independent review of COD-DOC by two LLM models (`miniMax-m3` locally, `deepseek-v4-pro` via openrouter) gave **73% overlap** in the top observations. Both models independently found:

- 🔴 Cycle-5 agent profile (AGT-003..007) — 5 of 6 tools = `NotImplementedError` stubs.
- 🔴 5 legacy YAML-backed modules (`legacy_*.py`, 863 lines) — dead code after the DB migration.
- 🟡 8 places with `pragma: no cover` on degraded paths — no CI signal on regression.
- 🟡 `Config.load()` loads YAML from disk on every MCP call.
- 🟠 `task: Any` workaround in `agent_service.py:119` (circular import).
- 🟠 `event_bus._subscribers` — module-level defaultdict, leak on reload.

**Conceptual divergence** in the proposed RFC #21 led to a **hybrid** version:
- **Hard exceptions** (`TaskNotFoundError`, `StatusTransitionError`) → DB table `error_audit` (pro approach).
- **Soft failures** (degraded paths, defensive guards) → in-memory ring buffer (mine approach).
- **Activity events** on both tiers for WebSocket real-time visibility.


## Methodology

### Source-of-truth baseline
Before launching the review both models got **the same** fact base:
- 119 MCP tools (`mcp.list_tools()` enumeration).
- Hot-files by `git log --all --pretty=format: --name-only | sort | uniq -c | sort -rn` (top-15: `api/web/pages.py` × 20, `agent/orchestrator.py` × 19, `task_service.py` × 18, `mcp/server.py` × 16, …).
- Large-files by `find cod_doc -name "*.py" -exec wc -l {} \;` (top: `api/web/pages/docs.py` 1078, `stories.py` 860, `task_service.py` 851, `task_tools.py` 729, `orchestrator.py` 706, …).
- 8 places with `pragma: no cover` (the full list is below).
- 5 audit reports (Section A, B, C, G, Web) + 4 web-batch checkpoints.
- 9 task plans in `docs/system/roadmap/`.
- Cycle-5 agent profile — 6 tools, 1 implemented, 5 stubs.

### Independence protocol
- **Pro** (`deepseek-v4-pro`) was run via `delegate_task` with an explicit instruction: "do not look into the previous context, work only from the repo".
- **Mine** (`miniMax-m3`) was written **before** I read the pro report.
- The comparison was made **after** both versions, by the method "what matched / what is unique / where the divergence is".

## Matches (11/15 — a strong signal)

| # | Observation | Severity | Files |
|---|---|---|---|
| 1 | AGT-003..007 — `NotImplementedError` stubs | 🔴 P0 | `mcp/tools/agent_tools.py:121-173` |
| 2 | Legacy YAML/DB double paths (5 files, 863 lines) | 🔴 P0 | `mcp/tools/legacy_*.py` |
| 3 | `pragma: no cover` × 8 on degraded paths | 🟡 P1 | `event_bus.py:123,132`; `run_context.py:140,179,214`; `websocket.py:48`; `routine_service.py:463`; `adapters/registry.py:93` |
| 4 | Skill matcher — keyword substring, fragile | 🟡 P1 | `agent/skill_matcher.py:46-83,128-132` |
| 5 | `Config.load()` without a cache → I/O on every MCP | 🟡 P1 | `mcp/tools/_db.py:27`; `config.py:155` |
| 6 | Cycle-5 task-centric agent profile = correct | ✅ Strength | `mcp/profiles.py:18-49` |
| 7 | Event bus with deferred until commit = correct | ✅ Strength | `services/event_bus.py:122,131` |
| 8 | Audit cadence + run_id = killer feature | ✅ Strength | `services/run_context.py` |
| 9 | `event_bus._subscribers` global state leak | 🟠 Dep | `event_bus.py:56` |
| 10 | `iter_skill_records()` is duplicated mcp↔services | 🟠 Dep | `agent/skill_matcher.py:21` ↔ `services/skill_service.py:15` |
| 11 | `task: Any` workaround in `agent_service.py:119` | 🟠 Dep | `services/agent_service.py:119` |

## Unique findings

### Pro found, mine did not (6)
- **LLM adapter pattern** (`agent/adapters/base.py:115-137`) — a `@runtime_checkable Protocol` for LLM backends, support for `openai_compat`/`anthropic`/`mock`. This is an **architectural** strength, I did not mention it.
- **ADR immutability** (commit `2a09c1b`) — after `ACCEPTED` an ADR cannot be edited, only `supersede` / `deprecate`. Pro highlighted it as Strength #5, I mentioned it in passing.
- **`lifespan` in `api/server.py:40`** — means the graceful shutdown infrastructure is already there, it remains to add `dispose_all()`.
- **Skill matcher: hardcoded stop-words** (lines 67-82) — concrete, I only mentioned the general fragility.
- **8 vs 8 places pragma:no cover** — both counted, but pro described each in more detail.
- **Specific file:line references** (18 references in pro vs 12 in mine) — pro is deeper in citation.

### Mine found, pro did not (3)
- **`refactor-large-files-task-plan.md` exists, status=pending** — my report used the **existing plan** as context: "the plan exists, not started, Section A = `task_service.py` decomposition". Pro proposed this as new work.
- **Hot-files ranking** (`api/web/pages.py` × 20 commits) — points to a stably active file, a potential tech debt.
- **Web pages: `docs.py` 1078 + `stories.py` 860 LOC** — large view files, not mentioned in `refactor-large-files-task-plan.md` (which covers only services). I noticed that web-pages **are not covered by the plan**.

## Divergence in RFC #21 (the central architectural question)

| Aspect | Pro (Transaction-Level Error Audit Trail) | Mine (Degraded-Path Auditability) | Hybrid |
|---|---|---|---|
| Storage for soft failures (degraded paths) | (did not single out as a separate tier) | In-memory ring buffer (100) | **Ring buffer 100** — cheap, does not bloat the DB |
| Storage for hard exceptions (TaskNotFound, StatusTransition) | A new DB table `error_audit` with run_id + traceback | (did not propose) | **DB table** — for a post-mortem in 3 months |
| Trigger | `audit_errors(...)` context manager | `@degraded_path("CODE")` decorator | **Both**: decorator for Tier 1, context manager for Tier 2 |
| WebSocket events | `error.occurred` on critical | (did not mention) | **`error.occurred` for Tier 2 critical, `degraded_path.triggered` for Tier 1** |
| Persistence | Full (the whole history) | No (lost on restart) | **Hybrid: Tier 1 — no, Tier 2 — yes** |
| TTL / cleanup | Did not mention | Did not mention | **TTL cleanup of `error_audit` via a routine (proposal 07) — 90 days** |

**The hybrid version** (final) — `proposals/21-degraded-path-auditability.md`. Uses the **strengths** of both approaches:
- Soft failures — ring buffer (fast, does not load the DB, ideal for degraded paths, which should not fire).
- Hard exceptions — DB table (for post-mortem, audit-trail, compliance).

## Review metrics

| Metric | Pro | Mine |
|---|---|---|
| Time | 4 min 1 sec | ~3 min (without delegating overhead) |
| API calls | 18 (terminal + read_file) | ~10 (read + grep, more selective) |
| Tokens | 1 180 356 in / 12 750 out | ~6 000 in / 5 000 out (10x less) |
| File:line references | 18 | 12 |
| Top strengths | 5 | 5 |
| Top weaknesses | 5 | 5 |
| Strong dependencies | 5 | 5 |
| Quick wins | 7 | 7 |
| P0 / P1 / P2 items | 2 / 3 / 3 | 2 / 3 / 3 |
| RFC draft (lines) | ~250 (EAT) | ~280 (DPA) |
| Hybrid RFC (final) | — | — |
| **Cost** | **$0.55** (1.2M × $0.10 + 12.7K × $0.20 = $0.12 + $2.54 = ...) | **$0** (local model) |

*Pro actual cost: input 1.18M × $0.10/M = $0.118, output 12.75K × $0.20/M = $0.00255, total $0.121.*

## Combined list of recommendations (for planning)

### P0 (definitely do)
1. **Implement AGT-003..007** (cycle-5 stubs → code) — both agree.
2. **Delete the legacy YAML/DB duplicates** — both agree. 863 lines of dead code.

### P1 (in the next month)
3. **Close `pragma: no cover`** — both agree. 0.5-1 day, 8 unit tests.
4. **Apply RFC #21 in the hybrid form** — 1-2 weeks (ring buffer + DB table + 2 MCP tools + 2 web pages + tests).
5. **Cache `Config.load()`** — both agree. 0.5 day. **Caveat:** check that the Config does not change without an MCP server restart.

### P2 (if possible)
6. **Skill matcher → embedding similarity** — both agree. 1-2 days.
7. **`iter_skill_records()` into `core/`** — both agree. 0.5-1 day. Pure refactor.
8. **`event_bus._subscribers` dispose** — both agree. 0.5 day + lifespan hook.
9. **`task: Any` → Protocol** — both agree. 0.5 day.
10. **Run `refactor-large-files-task-plan.md`** — mine notes that the plan already exists, status=pending. Section A = `task_service.py` decomposition.
11. **Add web-pages to the refactor plan** — `docs.py` 1078 + `stories.py` 860 lines are not covered.

## Next steps

- [ ] Accept RFC #21 (hybrid) — see `proposals/21-degraded-path-auditability.md`.
- [ ] Create RFC tasks in `docs/system/roadmap/` (via the `plan_create` MCP tool) for P0/P1 items.
- [ ] Close the section with an audit report on the fact of implementation.
- [ ] Revise `refactor-large-files-task-plan.md` — add web-pages.

## Sources

- `cod_doc/services/event_bus.py:122-132` — deferred commit/rollback hooks.
- `cod_doc/services/run_context.py:140,179,214` — degraded paths.
- `cod_doc/mcp/tools/agent_tools.py:121-173` — AGT-003..007 stubs.
- `cod_doc/mcp/tools/legacy_*.py` — 5 legacy files, 863 lines.
- `cod_doc/api/server.py:40` — `async def lifespan` (already exists).
- `cod_doc/agent/skill_matcher.py:46-83` — keyword matching.
- `cod_doc/config.py:155` — `Config.load()` classmethod.
- `cod_doc/services/agent_service.py:119` — `task: Any` workaround.
- `docs/system/roadmap/refactor-large-files-task-plan.md` — the existing plan (status=pending).
- `proposals/01-skills-layer.md` — the RFC format.
- 2026-06-04 pro report (`deepseek-v4-pro`) — 18 file:line references.
- 2026-06-04 mine report (`miniMax-m3`) — 12 file:line references.
