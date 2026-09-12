---
type: audit-report
scope: paperclip-adoption / Section F (Tooling fixes)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-doc-consolidation-cycle-2.md
  - 2026-05-07-doc-consolidation-cycle-4.md
  - ../roadmap/paperclip-adoption-task-plan.md
---

# Section F — Closure Report (Tooling Fixes)

> **Purpose.** Record the closure of 3 of 4 tasks of Section F of the
> `paperclip-adoption-task-plan` plan (PCA-901, PCA-902, PCA-903). PCA-911
> remains open as a low-priority cleanup.

## 1. TL;DR

- **PCA-902** (`critical`) and **PCA-903** (`high`) are closed by a joint fix —
  a single family of edits in `task_service.create` and `_db.task_to_dict`.
- **PCA-901** (`high`) is closed by adding the MCP tools `plan.create` and
  `plan.section_create` in `cod_doc/mcp/tools/plan_tools.py`.
- 5 new tests in `tests/services/test_task_create.py`; smoke-assertions
  on the new tool names in `tests/test_mcp.py`.
- Regression: services suite **407/407**, MCP smoke **2/2**, API web suite
  **251/251**.

## 2. Changes

### 2.1 PCA-902 + PCA-903 — task_create persistence

**Files:**
- `cod_doc/services/task_service.py` — `create()` accepts `blocked_by:
  list[str] | None` and `story_id: str | None`. After the task insert:
  - For each `blocked_by` task_id as a string — lookup by `TaskModel.task_id`
    and insert `DependencyModel(from_task_id=new, to_task_id=blocker,
    kind='blocks')`. Unknown task_id → `ValueError`.
  - For `story_id` — lookup by `(project_id, story_id)` and insert
    `StoryLinkModel(story_id=story.row_id, to_kind='task',
    to_ref=task_id, relation='implemented_by')`. Unknown story_id → `ValueError`.
- `cod_doc/mcp/tools/_db.py` — `task_to_dict(t, session=None)` optionally
  accepts a session. With a session — three SELECTs that populate
  `blocked_by` (via dependency join), `affects_files` (via affected_file)
  and `story_id` (via story_link reverse-lookup).
- `cod_doc/mcp/tools/task_tools.py` — `task.create` passes
  `blocked_by` and `story_id` to the service; `task_to_dict(t, session=session)`
  for `task.create / task.get / task.list`. The "echo back" stub is removed.

**Tests:**
- `tests/services/test_task_create.py`:
  - `test_create_with_blocked_by_persists_dependency_rows` — two blockers
    create two correct DependencyModel rows.
  - `test_create_with_unknown_blocked_by_raises` — ValueError + the
    original `task_id` is preserved in the message.
  - `test_create_with_story_id_creates_story_link` — a story_link with
    `relation='implemented_by'`.
  - `test_create_with_unknown_story_id_raises` — ValueError.
  - `test_plan_ready_excludes_tasks_with_unfinished_blockers` — the behavior
    of `plan_service.ready()` now correctly reflects blocked_by-edges.

### 2.2 PCA-901 — plan.create / plan.section_create MCP tools

**File:** `cod_doc/mcp/tools/plan_tools.py`.

```python
@mcp.tool(name="plan.create")
def plan_create(project, scope, principle="from-rfc", sections=None) -> dict:
    ...

@mcp.tool(name="plan.section_create")
def plan_section_create(project, plan_scope, letter, title, slug=None, position=None) -> dict:
    ...
```

`plan.create` optionally accepts an inline list `sections=[{letter,title,
slug,position}]` to bootstrap a new direction with a single call.
There is no idempotency — a repeat create with the same scope gives a `ValueError`
(as an `existing.scope` conflict).

**Tests:**
- `tests/test_mcp.py::test_mcp_lists_tools` is extended with smoke-assertions on
  `plan.create` and `plan.section_create`.
- The logic is directly covered by the existing PlanRepository tests
  (the repo layer is stable since COD-002).

## 3. Metrics

| Metric | Before | After | Δ |
|---------|-----|-------|---|
| `tests/services/` | 402 | 407 | +5 |
| `tests/test_mcp.py` smoke assertions | 18 | 20 | +2 |
| MCP plan-tools surface | 7 | 9 | +2 |
| Section F open tasks | 4 | 1 (PCA-911 only) | -3 |

## 4. Acceptance per task

- [x] **PCA-902 (critical)** — `task_create(blocked_by=['X'])` creates a
      `dependency` row; `task.get` shows the persisted blocked_by;
      `plan_ready` excludes blocked tasks.
- [x] **PCA-903 (high)** — `task_create(story_id='US-X')` creates a
      `story_link`; `task.get` shows the persisted story_id; affects_files
      are now visible in the response via the session-aware task_to_dict.
- [x] **PCA-901 (high)** — `plan.create` and `plan.section_create` MCP tools
      are registered and work; bootstrapping a new plan is possible without
      bypassing through PlanRepository.

## 5. What remains / not-in-scope

- **PCA-911 (low)** — cleaning up the `arch/arch/architecture.md` fixture —
  deferred as low-priority (needs a git rm + dedup of the doc-record).
- **A real DB body resync** for root `MASTER`/`docs/system/MASTER` —
  the accumulated drift after the edit-in-place cycles 1-3 is not fixed
  by Section F (drift accepted as a known state in Cycle 4).

## 6. Next step

The basic plan ergonomics are now correct: `plan_ready` / `plan_audit` /
`critical_path_length` reflect the real graph structure of the backlog.
We can start the Phase 1 paperclip plan (PCA-001 — Skills layer) or the
ADR plan (ADR-001 — Migration). Both are independent of Section F and have
`pending` statuses.

Recommendation: **PCA-001** as the first task of Phase 1, it is small-scope
and validates that the new dependency-flow actually breaks the race-specific
scenarios (PCA-002 has blocked_by=PCA-001).
