---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-09-02
last_updated: 2026-09-06
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-m4-proof-of-value.md
  - ../audit/2026-09-02-sprint-m4-proof-of-value.md
  - ../releases/2026-08-30-sprint-m4.md
---

# Sprint M5 — "A gate you can trust + symbiosis in the field"

> **Principle (owner decision 2026-08-30, extended to M5).** Code is written
> by an AI agent — deadlines and windows are not planned. A sprint is an
> **ordered queue of work** with contracts and an exit criterion. Order
> matters, dates do not.
>
> **Not source of truth.** Task statuses are in the DB (plan `adoption-2026-08`);
> priorities are in [ROADMAP.md](ROADMAP.md). Each queue task has a task_doc in
> the DB with the key **`contract`** — scope, boundaries, acceptance,
> verification, risk. Read it before starting the task:
> `task_doc_get(<ID>, key="contract")`.

## 0. Ground truth at sprint start (reconciled 2026-09-02)

The reconciliation was done by running and querying the live DB, not by
paraphrasing past reports.

| Measurement | Value |
|---|---|
| Local run | **1 failed, 1629 passed** (748 s) |
| CI on main | **10 runs out of 10 — failure**, from 2026-05-06 to 2026-09-02. success — zero |

> **Update 2026-09-03 — queue items 1–4 are closed.** The first green CI run
> in the branch's history: [run 33765619088](https://github.com/Orange-hanter/cod-doc/actions/runs/33765619088),
> `conclusion: success`, all 7 jobs — including `Docker build`, for months
> `skipped`. The run is 1639 passed. Tasks ADO-070 (`bcb32f2`), ADO-069, ADO-066,
> ADO-068 (`3de0fd5`) → done, evidence in task_doc `verification`.
>
> Fixing the gate revealed two more layers that were invisible while the job
> was dying on alembic in under a minute and a half (the "Risks" section
> predicted exactly this):
> **(a)** the run was not hermetic — the workflow itself sets
> `COD_DOC_API_KEY`, and `Config` reads env by the `COD_DOC_` prefix, so the
> "key not configured" tests failed only in CI; plus `timeout-minutes: 10`
> with a ~11-minute run killed the job before tracebacks were printed;
> **(b)** three tests spawned an MCP server with a hardcoded `.venv/bin/python`,
> and FastAPI 0.141 wrapped routes in `_IncludedRouter` without `.path` — so
> `_real_web_routes()` silently returned an empty set and the advisory job
> "Web routes drift" was green in vain. A false green over a red gate is a
> separate lesson of the sprint.
| Drift cod-doc | 131 in_sync, 1 edited_in_place (`CLAUDE.md`), 0 missing |
| Plan `adoption-2026-08` | 66 done / 82 |
| Project tasks | 265 total · 245 done · 15 pending (before this planning) |
| `revision.run_id` | 2004 rows, NULL — **2004 (100%)** |
| `activity_event.run_id` | 326 rows, NULL — **326 (100%)** |
| `audit_log` | **0 rows, 0 writers** |
| `~/.cod-doc/config.yaml` | test values + 2 pytest directories; `cod-doc` not registered |

### What this reconciliation changed in the worldview

M4 closed on the claim "gates are green". The claim is only true for the
local run. **Real CI was never green in the branch's history** — and,
therefore, the "gates green" item in the DoD of sprints M1…M4 was verified by
nothing but a laptop.

Four findings of this planning add up to one story:

- **ADO-070** — CI red for 4 months (`alembic` not on the runner's PATH).
- **ADO-069** — the only red test locally: a negative case lagged behind the
  v2 adapter that arrived in the same commit (SYM-009).
- **ADO-066** — `capabilities` / `tool_search` / `tools_diff` fail under a
  live MCP server; the tests call them synchronously and so do not see it.
- **ADO-068** — tests write to the real `~/.cod-doc/config.yaml`; regression
  of F5.

The story: **the test environment and production diverged in both
directions.** Tests do not see production defects (ADO-066) and at the same
time write to production state (ADO-068); the gate that was supposed to
catch this does not work (ADO-070) and is not checked (ADO-069). This is the
same class as F7 (`doc export` was corrupting documents) — a defect visible
only in real use, found in an hour of manual work against 1630 green tests.

## 1. Queue (order = priority)

The contract of each task is in the DB, `task_doc key="contract"`.

| № | Task | Priority | Essence |
|---|---|---|---|
| 1 | **ADO-070** | critical | Make CI a gate: `alembic` not on PATH, the first green run in 4 months |
| 2 | **ADO-069** | critical | The only red test locally (v2 case lagged behind SYM-009) |
| 3 | **ADO-068** | critical | Tests write to the real config; `CONFIG_DIR` frozen at import; regression of F5 |
| 4 | **ADO-066** | critical | `capabilities`/`tool_search`/`tools_diff` fail under a live server |
| 5 | **ADO-067** | high | `task_update` (description/acceptance/priority) in MCP and CLI |
| 6 | **ADO-065** | high | Field-run analysis of E5-C: cost/latency by role |
| 7 | **SYM-010** | medium | `ctx drift --changed-files` → Orakul PR comment (RFC 22, phase 4a) |
| 8 | **ADO-044** | medium | Mutation provenance: run_id + audit_log + actor_kind — ADR "implement or lift" |

**№1–2 go as a pair:** together they give the first green CI run. Without it,
the "gate is green" wording in the DoD of the other tasks means nothing.

**№1–6 — the body of the sprint.** №7–8 — the tail: taken if the queue
reaches them.

### Out of scope for M5 (by design)

- **ADO-042** (SQL from mcp/tools → repositories), **ADO-045** (DATA_MODEL ↔
  7-state), **ADO-046** (server_default), **ADO-047** (FTS5 Postgres),
  **ADO-048** (checkout fields in the domain), **ADO-049** (optimistic
  locking web), **ADO-050** (DocumentStatus state machine) — section D
  backlog.
- **ADO-014** (project-bootstrap doc) — low, opportunistic.
- **SYM-011** (cross-project) — low, on demand.
- **STB-023** (`activity_subscribe`) — kept closed intentionally.
- **Track B** — rejected (ADO-056), do not reopen.

## 2. Backlog consolidation (done 2026-09-02)

- **ADO-043** (M8, dead `audit_log`) and **ADO-051** (M18, `actor_kind`
  startswith heuristic) → `cancelled`, folded into **ADO-044** "mutation
  provenance". Three findings — one contract, one decision, one ADR. The
  finding texts are preserved in the description of ADO-044. Precedent for
  gluing: ADO-040 (M4+M5+M15), ADO-045 (M10+M11).
- **ADO-066, ADO-067, ADO-068, ADO-069, ADO-070** were created — all with
  measurements, not with "seems broken" wordings.
- Each queue task is assigned a `task_doc key="contract"`.

Result: 15 pending → 18 pending, but the composition is different — four
critical defects found by measurement, instead of ten scattered findings a
year old.

**The new task_doc key is `contract`.** Previously there were `design`
(before), `verification` (after), `acceptance` (decision). `contract` fixes
the boundaries of the work before the start: what is included, what is NOT
included, how it is proven. Introduced by this sprint.

## 3. Exit criterion

> **Sprint closed 2026-09-06.** All six items are fulfilled; the queue was
> walked through completely, including the tail №7–8. Breakdown —
> [M5 audit report](../audit/2026-09-06-sprint-m5-trustworthy-gate.md).

1. **Green CI run on main** — with a link to the run id. This is the main
   criterion: without it the other items are unprovable.
2. `capabilities` responds through a live MCP server.
3. The real `~/.cod-doc/config.yaml` is not changed by a test run; `cod-doc`
   is registered.
4. Backlog grooming is doable via MCP and CLI without scripts in the service
   layer.
5. E5-C analysis — an artifact with a table by role, findings F1–F5 are
   resolved.
6. M5 audit report (active, in the DB), ROADMAP updated.

## 4. Execution order

1. Setup: this sprint doc + M4 audit report + ROADMAP edits — one commit.
2. №1 → №8 strictly in order; each task `task_checkout` → `task_complete`
   with `commit_sha`. A bug is closed only after a red run BEFORE the fix
   (AGENTS.md rule).
3. Before starting a task — read its `contract`; if reality diverged from
   the contract, edit the contract, do not silently change the scope.
4. Final: gates (now including CI), audit report, ROADMAP, commit.

## 5. Risks

- **A green CI will reveal a new layer of defects** invisible locally: CI
  installs ruff without a pin and brings new rules (9×RUF036,
  redundant-cast — noted 2026-08-26). This is not a reason to postpone: this
  is exactly what the gate is supposed to catch. If the volume is large —
  record as a list and prioritize separately.
- **ADO-068 does not reproduce offhand**: the full run of 2026-09-02 did not
  change the mtime of the real config. The trigger is conditional; the first
  step of the task is to find the write path, not to fix blindly.
- **SYM-010 depends on someone else's PR flow** (Orakul). Empty — the task
  is moved, the sprint is not blocked. The same risk SYM-009 had in M4.

## 6. Definition of Done

- [ ] Each queue task went through `task_checkout` → `task_complete` with `commit_sha`.
- [x] **Green CI run on main** — not a local run, but a run id. *(run 33765619088, `conclusion: success`, 2026-09-03; all 7 jobs)*
- [ ] Each bug closed with an attached red run BEFORE the fix.
- [ ] Edits of tracked `.md` are imported in the same commits; the final
      `doc drift --all` is 100% in_sync.
- [ ] `ruff` + `mypy` + `pytest` green locally; ratchet did not grow.
- [ ] M5 audit report — status active, in the DB, with links to commits.
