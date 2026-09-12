---
type: audit-report
scope: paperclip-adoption / Section B (Phase 2 — Audit infra)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-section-f-closure.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/05-issue-documents.md
  - ../../../proposals/09-activity-log.md
  - ../../../proposals/12-approvals.md
---

# Section B — Closure Report (Phase 2: Audit infra)

> **Purpose.** Record the closure of 6 tasks of Section B of the plan
> `paperclip-adoption-task-plan` (PCA-100, PCA-101, PCA-110, PCA-111,
> PCA-120, PCA-121) and formalize findings → backlog for Phase 3 / Section F.

## 1. TL;DR

- **PCA-100/101** — `task_document` table + `TaskDocumentService` + 5 MCP tools
  (`task_doc_get/put/list/revisions/revert`). Optimistic lock via
  `base_revision_id`, snapshot revisions in the shared `revision` table
  (`entity_kind='task_doc'`).
- **PCA-110/111** — `activity_event` table + `ActivityEmitter` + 2 MCP tools
  (`activity_list`, `activity_for_run`). `run_id` is picked up from a contextvar.
- **PCA-120/121** — `approval` + `approval_task_link` + `approval_doc_revision_link`
  tables + `ApprovalService` + 5 MCP tools (`approval_request/list/get/resolve/cancel`).
  Resolve returns a `wake_hint` for `run_agent_once`. Single-pending-per-task
  invariant with auto-cancel on supersede.
- **Test coverage:** 43 new unit tests (13 + 12 + 18). Full suite —
  929 passed (was 886).
- **6 findings** (F1-F6) → backlog in Phase 3 / Section F.

## 2. Section B deliverables

| # | Deliverable | File / artifact | Status |
|---|------------|------------------|--------|
| D1 | Section B audit-report | `docs/system/audit/2026-05-08-section-b-phase-2.md` | ✅ this file |
| D2 | Migration `0011_task_documents` | `cod_doc/infra/migrations/versions/20260508_0011_task_documents.py` | ✅ |
| D3 | Migration `0012_activity_events` | `cod_doc/infra/migrations/versions/20260508_0012_activity_events.py` | ✅ |
| D4 | Migration `0013_approvals` | `cod_doc/infra/migrations/versions/20260508_0013_approvals.py` | ✅ |
| D5 | Models | `cod_doc/infra/models/{task_docs,activity,approvals}.py` | ✅ |
| D6 | Services | `cod_doc/services/{task_doc_service,activity_service,approval_service}.py` | ✅ |
| D7 | MCP tools | `cod_doc/mcp/tools/{task_doc_tools,activity_tools,approval_tools}.py` | ✅ |
| D8 | `EntityKind.TASK_DOC` enum | `cod_doc/domain/entities.py` | ✅ |
| D9 | Server registration | `cod_doc/mcp/server.py` (3 new modules) | ✅ |
| D10 | Tests (43) | `tests/services/test_{task_doc,activity,approval}_service.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-100 (migration)** — `task_document` table with UNIQUE(task_id, key),
      cascade FK to `task.row_id`, an index on `task_id`. Up/down work.
- [x] **PCA-101 (feature)** — `task_doc_get/put/list/revisions/revert`
      are registered; the optimistic lock throws `TaskDocConflictError`;
      revisions are saved with `entity_kind='task_doc'`; `run_id` is stamped
      from a contextvar.
- [x] **PCA-110 (migration)** — `activity_event` table with 5 indexes
      (`ts`, `(scope_kind, scope_id, ts)`, `run_id`, `(project_id, ts)`,
      `(kind, ts)`). Append-only; up/down work.
- [x] **PCA-111 (feature)** — `ActivityEmitter.emit()` is ready; `activity_list`
      with filters by scope/kind/actor/since/until + pagination; `activity_for_run`
      returns the events of one run oldest-first.
- [x] **PCA-120 (migration)** — `approval` + `approval_task_link` +
      `approval_doc_revision_link` tables; 4 indexes. Up/down work.
- [x] **PCA-121 (feature)** — `approval_request/list/get/resolve/cancel`
      are registered; single-pending-per-task invariant with auto-cancel;
      `wake_hint` is returned on resolve; `activity.requested/resolved/cancelled`
      events are emitted.

## 4. Findings (→ backlog)

### F1 — ActivityEmitter is not connected to existing write-tools *(high)*

**What is now:** `activity_service.emit()` is called **only** from
`approval_tools.py` (3 places). `task_tools.create/update_status/set_blocker/
clear_blocker`, `doc_tools.create/update/rename`, `task_doc_tools.put/revert`,
`run_context.start_/finalize_orchestrator_run` — **do not write events**.

**Consequence:** `activity_list` returns only approval events; the UI
timeline and the audit chain from proposal 09 §1 (*"answer on one UI page
what happened today"*) do not work. The Phase 1 plan from proposal 09 §3
(*"tasks (creation, status, blockers, comments)"*) → is not fulfilled.

**Recommendation:** introduce a middleware/decorator in `cod_doc/mcp/tools/_db.py`
(or explicit `activity.emit()` in each write-tool) for kinds:
`task.created/status_changed/blocker_added/blocker_cleared`,
`doc.created/updated/renamed`, `task_doc.updated`,
`run.started/finished/failed`. Each — in one transaction with the mutation itself.

### F2 — `approval_request` does not move linked tasks to `in_review` *(high)*

**What is now:** `ApprovalService.request()` creates an approval, links
`approval_task_link` rows, but **does not touch the task status**. The acceptance
for PCA-121 in the plan was *"approval_request moves the linked tasks to
in_review"* — not fulfilled.

**Root:** our TaskStatus enum is still 3-state (`pending | in-progress | done`).
The `in_review` status does not exist until **PCA-220** (Section C, 7-state taxonomy).

**Recommendation:** do not do a partial fix in Section B. Record
this as a **dependency of PCA-121 ↔ PCA-220** in the plan; the auto-status is enabled
after PCA-220, in a single PR.

### F3 — No expiry-routine for `expired` approvals *(medium)*

**What is now:** `expires_at` is written to the DB, but no routine
moves expired approvals to `status='expired'`. They forever
remain `pending`.

**Root:** routines (cron) — that is **PCA-211** (Section C), not yet present.

**Recommendation:** after PCA-211 add a routine `approval_stale` which
SELECTs `pending AND expires_at < now()` → `cancel(reason='expired')`
(or a new kind `expire`) + wake the operator.

### F4 — Activity `id` uses UUID4, not UUID7 *(low)*

**What is now:** `activity_service._make_id()` generates `uuid4()` with a TODO comment
*"swap to UUID7 when available"*. The field was intended to be time-sortable
(proposal 09 §32), but `uuid4` sorts lexicographically randomly.

**Consequence:** `ORDER BY id` gives wrong ordering; pagination by id
breaks. Currently saved by `ORDER BY ts` + `row_id` tiebreak, but `id`
cannot be used as a cursor.

**Recommendation:** add `uuid7`/`uuid_extensions` to the project's
dependencies (pip), replace `uuid4()` → `uuid7()`. Single-line fix +
bump dep.

### F5 — Heartbeat-context does not include `task_documents` and pending approvals *(medium)*

**What is now:** `task_heartbeat_context(task_id)` returns a task summary,
linked_docs, recent_changes — but **does not mention task-bound docs and
approvals**. Proposal 05 §5 explicitly requires:
*"add a slice task_documents: [{key, current_revision_id, summary]"*.

**Consequence:** an agent woken by `task_assigned` does not see its
plan/design/verification docs and does not know that the task is in pending approval.

**Recommendation:** extend `heartbeat_service.heartbeat_context()`:
- `task_documents`: `[{key, title, current_revision_id, last_updated}]`
- `pending_approvals`: `[{approval_id, type, requested_at, expires_at}]`
The heartbeat budget ≤ 4 KB is respected (1 line per entry).

### F6 — `task_doc.revert` is parallel to `revision_service.revert()` *(low)*

**What is now:** the general `revision_service.revert(revision_id)` supports
TASK / SECTION / DOCUMENT (see `RevertNotSupportedError`). For TASK_DOC
a separate function `task_doc_service.revert()` was made, which bypasses
this flow.

**Consequence:** `mcp.revision_revert(...)` for a task_doc-revision will return
"not supported", although the data allows a revert. A split of the surface.

**Recommendation:** add a `revision_service.revert()` branch for
`EntityKind.TASK_DOC` — it simply delegates to `task_doc_service.revert()`.
Leave the `task_doc_revert` MCP tool as a convenient shortcut.

## 5. Metrics

| Metric | Before Section B | After | Δ |
|---------|-------------:|------:|--:|
| Tables in schema | 19 | 22 | +3 |
| Migrations | 10 | 13 | +3 |
| Service modules | ~25 | 28 | +3 |
| MCP write-tools | ~30 | 41 | +11 |
| `tests/` total | 886 | 929 | +43 |
| Section A+B done tasks | 22 | 28 | +6 |

## 6. What was not included (out of scope)

- **Web UI:** approval inbox / activity timeline / task-doc tabs —
  not implemented. Web pages for the three new entities — separate
  tasks in Phase 3+.
- **CLI:** `cod-doc activity / approval / task_doc` commands — no.
- **Search index:** `search_docs` does not index task_docs (proposal 05 Q1
  left open).
- **Retention/archive** of activity_events (proposal 09 §95) — not implemented.
- **`activity_summary_daily`** aggregator for the dashboard — not implemented.

## 7. Next step

Section A (Phase 1) and Section B (Phase 2) **are closed**. Section C
(Phase 3 — Extensions) is unblocked: PCA-200..230 (atomic checkout,
routines, 7-state TaskStatus taxonomy, AGENTS.md).

Recommended order of Section C:
1. **PCA-220** (TaskStatus 7-state migration) — removes the blocker from F2
   (will bring linked tasks to `in_review` on approval_request).
2. **PCA-221** (status transition rules) — closes enforcement.
3. **PCA-211** (scheduler runner) — removes the blocker from F3 (expiry routine).
4. In parallel: F1 (activity emission in existing write-tools) —
   small, can go as a Section F-task without dependencies.

Findings F1-F6 are filed as separate tasks in the plan
`paperclip-adoption-task-plan` (Section F: PCA-912..PCA-917) for
tracking.
