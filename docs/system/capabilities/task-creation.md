---
type: capability
scope: task-creation
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../standards/task-plan.md
  - plan-management.md
---

# Capability — Task Creation

> Standardized and automatic task creation without manual YAML filling and manual format checking.

## 1. What needs to be automated

Today (Restate baseline) the task author must:

1. Know the frontmatter structure (`id`, `title`, `section`, `status`, `depends_on`, `type`, `priority`, `affected_files`).
2. Pick the correct ID from the free range of the section.
3. Choose a verb-pattern for the title.
4. Not violate enums (`type`, `status`, `priority`).
5. Manually recompute `tasks_total` / `tasks_done` in the section frontmatter.
6. Update Progress Overview and Next Batch in the parent-plan.
7. Rebuild the dependency graph.

Each step is a point of failure. COD-DOC does all seven automatically.

## 2. Main surfaces

| Surface | Command |
|-------------|---------|
| CLI | `cod-doc task new --plan <plan> [--section <letter>] --title "<text>" --type <type> [--priority <p>] [--depends <ID,...>] [--affected <path,...>]` |
| TUI | `cod-doc wizard task new` — step-by-step form |
| MCP | `task.create({...})` |
| REST | `POST /api/v1/tasks` |

## 3. The `task.create` contract

Input:

```json
{
  "plan": "M1-auth-module",
  "section": "C",                       // letter OR null — the service will pick
  "title": "Implement: account deactivation flow",
  "type": "feature",
  "priority": "high",                   // default: "medium"
  "depends_on": ["AUTH-020", "AUTH-021"],
  "affected_files": [
    "restate-api/src/auth/services/auth.service.ts",
    "restate-api/src/auth/__tests__/auth-lifecycle.spec.ts"
  ],
  "description": "...",
  "acceptance": "..."
}
```

Output:

```json
{
  "task_id": "AUTH-025",
  "section": "C-AccountLifecycle",
  "status": "pending",
  "created_revision": "01HQX5Z9F0K8RNG6CB7VHQK4XX"
}
```

## 4. Automation of the `TaskService.create` service

1. **Title validation** → verb-pattern regex (`standards/task-plan.md §7`). On mismatch, the list of allowed patterns is returned and the task is not created.
2. **Section selection** if not specified — by `type`:
   - `test` → first `Test Coverage` section, if any.
   - `feature` → section with unfinished implement-tasks.
   - otherwise — the last open section.
3. **ID generation**:
   - PREFIX = `plan.prefix` (cached).
   - NUMBER = `max(existing in section range) + 1`, clamped to decade bounds.
   - If the decade is full — the next free decade.
4. **depends_on validation**:
   - All task_ids exist.
   - No cycle (recursive CTE + inserted edge).
   - Cross-plan is allowed.
5. **DB write** in a single transaction:
   - `task` row.
   - `dependency` rows.
   - `affected_file` rows.
   - `revision(entity_kind=task, ...)`.
6. **Post-actions** (same transactional scope):
   - Recompute `section_totals` (view, automatically).
   - `PlanService.recalc(plan_id)` to update the Progress Overview/Next Batch body.
   - `LinkService.reindex(task.section.doc_id)` for new outgoing links.
7. **Projection**: if auto-export is enabled in the project config — section markdown files are regenerated.

## 5. Forbidden scenarios

- Creating a task without a `plan` — forbidden at the schema level (`NOT NULL`).
- Manual markdown edits without a subsequent import — not forbidden, but on the next export it will be overwritten from the DB.
- Attempting to create a task with `status: done` immediately — error (you must go through `pending → in-progress → done`).
- Cycles in `depends_on` — error on insert, with the cycle nodes listed.

## 6. Batch creation

Useful when importing user stories:

```bash
cod-doc task bulk --plan M1-auth-module --from-yaml tasks.yaml
```

where `tasks.yaml` is an array of objects of the same format. The operation is transactional (all or nothing).

## 7. Integration with user stories

If `story_id` is specified in the request, a `story_link(to_kind=task, relation=implemented_by)` is created. This later allows getting "all tasks implementing US-014" without parsing markdown.

## 8. Integration with the ready logic

Right after creating a task:

- If the task has an empty `depends_on` — it goes into `ready_tasks`.
- `PlanService.recalc_next_batch()` recomputes the top-7 unblocked.

## 9. Examples

### 9.1 Via CLI

```bash
cod-doc task new \
  --plan M1-auth-module \
  --title "Implement: account deactivation flow" \
  --type feature \
  --priority high \
  --depends AUTH-020,AUTH-021 \
  --affected restate-api/src/auth/services/auth.service.ts
# → AUTH-025 created in C-AccountLifecycle
```

### 9.2 Via MCP (agent)

```
→ task.create({
    plan: "M1-auth-module",
    title: "Test: registration field validation",
    type: "test",
    priority: "medium"
  })
← { task_id: "AUTH-050", section: "A-Test-Coverage", status: "pending" }
```

No manual work with markdown, no conflicts with `tasks_total`.

## 10. Migration from Restate

When importing existing plans (see [migration/from-restate.md](../migration/from-restate.md)) the `TaskService.import_bulk` service accepts parsed markdown and runs the same validations as `task.create`. Restate format violations (encountered, e.g. `section: A MR Blockers` with spaces) are fixed automatically + a revision is written with `reason: "import-normalize"`.
