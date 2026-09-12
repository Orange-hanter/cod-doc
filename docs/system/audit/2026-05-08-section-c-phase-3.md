---
type: audit-report
scope: paperclip-adoption / Section C (Phase 3 — Extensions)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-b-phase-2.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/06-atomic-checkout.md
  - ../../../proposals/07-routines.md
  - ../../../proposals/08-status-taxonomy.md
  - ../../../proposals/11-agents-md.md
  - ../../../AGENTS.md
---

# Section C — Closure Report (Phase 3: Extensions)

> **Purpose.** Record the closure of 7 tasks of Section C
> (PCA-200/201/210/211/220/221/230) and describe findings → backlog.

## 1. TL;DR

- **PCA-220 + 221** — TaskStatus 7-state taxonomy (proposal 08).
  An additive extension of the enum (legacy pending/in-progress/done are preserved).
  The state machine `task_status_machine.py` with `validate_transition` is integrated
  into `task_service.update_status` in **warn-mode** (proposal 06 §89: Phase 1
  permissive, Phase 2 enforce). `strict=True` opt-in.
- **PCA-200 + 201** — atomic checkout (proposal 06).
  Migration 0014 + `checkout_service.py` + 2 MCP tools. `CheckoutConflictError`
  on 409, idempotent for the same agent. Activity events on checkout/release.
- **PCA-210 + 211** — routines (proposal 07).
  Migration 0014 (merged), `routine_service.py` + 7 MCP tools.
  One real check (`approval_stale` — closes Section B finding F3),
  4 noop placeholders for daemon wiring.
- **PCA-230** — `AGENTS.md` (proposal 11). 12 sections at the root +
  an updated PR-template (Model used + DoD).
- **Test coverage:** 41 new unit tests. Full suite — **970 passed**
  (was 929).
- **6 findings** (G1-G6) → backlog in Section F (PCA-918..923).

## 2. Section C deliverables

| # | Deliverable | File / artifact | Status |
|---|------------|------------------|--------|
| D1 | Section C audit-report | `docs/system/audit/2026-05-08-section-c-phase-3.md` | ✅ |
| D2 | Migration `0014_task_checkout_and_routine` | `cod_doc/infra/migrations/versions/20260508_0014_*.py` | ✅ |
| D3 | TaskStatus enum extension | `cod_doc/domain/entities.py` | ✅ |
| D4 | State machine | `cod_doc/services/task_status_machine.py` | ✅ |
| D5 | Checkout service + MCP | `cod_doc/services/checkout_service.py`, `mcp/tools/checkout_tools.py` | ✅ |
| D6 | Routine model + service + MCP | `cod_doc/infra/models/routines.py`, `services/routine_service.py`, `mcp/tools/routine_tools.py` | ✅ |
| D7 | TaskModel checkout fields | `cod_doc/infra/models/plans.py` | ✅ |
| D8 | AGENTS.md + PR template | `AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md` | ✅ |
| D9 | revert flow uses force=True | `cod_doc/services/revision_service.py` | ✅ |
| D10 | Server registration | `cod_doc/mcp/server.py` (2 new modules) | ✅ |
| D11 | Tests (41) | `tests/services/test_{task_status_machine,checkout,routine}_service.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-220 (migration)** — `TaskStatus` contains the canonical 7-state +
      legacy aliases; no breaking changes for existing task rows
      (stored as-is).
- [x] **PCA-221 (feature)** — `validate_transition` with a matrix; +1
      pragmatic deviation (in_progress → todo) is documented in the code.
      Integration into `update_status` via warn-mode by default.
- [x] **PCA-200 (feature)** — `task_checkout` and `task_release` MCP tools;
      idempotent for the same agent; conflict raises with an advisory "never retry";
      `release_stale` for cleanup.
- [x] **PCA-201 (refactor)** — `update_status` accepts `via_checkout` /
      `strict` / `force`; the revert flow wires force=True. Full enforcement
      is deferred to Phase 2 (see the F-task PCA-922).
- [x] **PCA-210 (migration)** — `routine` + `routine_run` tables with all
      the fields of proposal 07 (trigger / cron / on_finding / concurrency / catch_up);
      the round-trip up→down→up works.
- [x] **PCA-211 (feature)** — 7 MCP tools; `concurrency=skip` is respected;
      `routine.fired` / `routine.found_issue` events are emitted; the `approval_stale`
      check works end-to-end (closes F3 from the Section B audit).
- [x] **PCA-230 (docs)** — `AGENTS.md` 12 sections; the PR-template contains
      `Model used` and Definition of Done.

## 4. Findings (→ backlog)

### G1 — Checkout enforcement is deferred (warn-mode by default) *(high)*

**What is now:** `task_service.update_status` accepts any invalid
transition silently (warn-mode). Per proposal 06 §89 this is correct for
Phase 1, but Phase 2 enforcement is not formalized as a task.

**Consequence:** an agent can do `pending → done` or
`backlog → in_progress` bypassing checkout, and the state machine will not stop it.

**Recommendation:** formalize Phase 2 enforcement as a task. It requires:
(a) a grep of all existing callers of `update_status`, (b) adding
`strict=True` where safe, (c) switching the default after the migration.

### G2 — Scheduler daemon is not implemented *(high)*

**What is now:** `routine_service.run_now()` works on demand (manual
trigger). The cron schedule (`routine.cron`, `trigger='cron'`) is stored in
the DB, but there is no process that reads these rows and calls `run_now`
on a schedule.

**Consequence:** `approval_stale` (closing F3) is triggered only
on a manual call to `routine_run_now`. Similarly `doc_drift` /
`task_stale` / `link_integrity` — wait for a cron loop.

**Recommendation:** implement `cod_doc/services/routine_scheduler.py`
with `croniter` + a tick loop (or `apscheduler`). Launch via the CLI command
`cod-doc routine daemon`. Out of scope of this Section C — a separate F-task.

### G3 — 4 of 5 checks in the catalog — noop placeholders *(high)*

**What is now:** `CHECK_CATALOG` contains 5 names, but only `approval_stale`
does real work. `stale_refs` / `link_integrity` / `doc_drift` /
`task_stale` return `{"findings": [], "note": "noop"}`.

**Consequence:** a routine with these names is created, but in fact
checks nothing. Visibility is zero.

**Recommendation:** wrap the existing MCP tools (`check_stale_refs`,
`link.verify`, `doc.drift`, `task.stale`) into check functions. Each —
a separate F-task (4 small PRs).

### G4 — Write-tools (task_complete, task_set_blocker) do not check checkout *(medium)*

**What is now:** PCA-201 acceptance says "all mutating task tools
require a valid active checkout", but `task_complete`,
`task_set_blocker`, `task_clear_blocker`, `task_log_progress` so far
check nothing. Only `update_status` optionally validates the
transition, not ownership.

**Recommendation:** add a helper `_warn_no_checkout(session, task_id,
agent)` to `cod_doc/mcp/tools/_db.py` + call it from 4 write-tools
(warn-mode). Phase 2 — enforce.

### G5 — The Routine `update_existing_task` policy is not implemented *(medium)*

**What is now:** `on_finding='create_task'` creates a new task on every
finding (rather, would create, if there were coding — right now even
this branch is not in `run_now`). The policy `update_existing_task` (needed for
recurring drifts on the same docs, so as not to breed duplicates) —
proposal 07 §91 calls it a must-have.

**Recommendation:** implement signature-deduplication: a hash of
`(check_name, scope_kind, scope_id, finding_kind)` → if an open
task with such a signature exists, add a comment instead of a new
task. F-task.

### G6 — `task_status_machine` is not integrated into the legacy `next_pending_task` *(low)*

**What is now:** the legacy MCP tool `next_pending_task` returns a task with
status='pending' (legacy string). New callers expecting 'todo' may
not find tasks. `normalise()` is not used in the legacy slice.

**Recommendation:** wrap the SELECT in `next_pending_task` so that it matches
both variants (`status IN ('pending', 'todo')`); or deprecate the tool itself
(already planned in PCA-411).

## 5. Metrics

| Metric | Before Section C | After | Δ |
|---------|-------------:|------:|--:|
| Tables in schema | 22 | 24 | +2 |
| Migrations | 13 | 14 | +1 |
| Service modules | 28 | 31 | +3 |
| MCP write-tools | 41 | 50 | +9 (checkout=2, routine=7) |
| `tests/` total | 929 | 970 | +41 |
| Section C done tasks | 0 | 7 | +7 |
| Total Section A+B+C done | 28 | 35 | +7 |

## 6. What was not included (out of scope)

- **Web UI** for checkout / routines (timeline, approval inbox, kanban
  by new statuses) — separate tasks.
- **CLI** for `cod-doc checkout / routine` commands — no.
- **Custom routines from the UI** (proposal 07 Q5) — only built-in ones.
- **Cron daemon** (see G2) — a separate task.
- **Pre-commit hook** on required PR sections (proposal 11 Q3) — no.

## 7. Next step

Section A (Phase 1) ✅, B (Phase 2) ✅, C (Phase 3) ✅ are closed.

Open directions:
- **Section D (Phase 4 — Adapter, PCA-300..302)** — 3 tasks; LLMAdapter
  Protocol + openai_compat / claude_native + AdapterRegistry. Depends
  only on Section A (closed). Small.
- **Section E (UX & Migration, PCA-400..422)** — 7 tasks. Web UI / import
  improvements / link redesign. Low risk, high visibility.
- **Section F backlog** — replenished with findings F1-F6 (Section B) +
  G1-G6 (Section C). If we choose a "consolidation cycle" —
  the F-bucket can be cleared before opening Phase 4/5.

Findings G1-G6 are filed as PCA-918..923 in Section F.

Recommendation: open Section D (compact, 3 tasks) or Section F
(a consolidation cycle on the accumulated findings) before Section E.
