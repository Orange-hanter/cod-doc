# 05 — Issue documents with revisions (pinned per-task)

> Category: 🟡 Adaptation · Risk: medium · Dependencies: 04

## Context: like paperclip

Each issue can have named **documents** attached to it:
```
PUT /api/issues/:id/documents/plan
{
  "title": "Plan",
  "format": "markdown",
  "body": "...",
  "baseRevisionId": "rev-abc"   // optimistic lock
}
```

Canonical keys: `plan` (the plan), and as needed `design`, `verification`, `acceptance`, etc. The planning skill even specifically requires:
> *"If you're asked to make a plan, create or update the issue document with key `plan`. Do not append plans into the issue description anymore."*

Each document has independent revisions and is addressed via a deep-link `/{prefix}/issues/{id}#document-plan`.

## Current state of cod-doc

- There are **global** docs (`doc_create`, `doc_body`, `doc_get`) with revisions — but they are **not bound** to tasks.
- There are **stories** with criteria (`story_add_criterion`, `story_link`) — this is the closest, but with different semantics (story acceptance criteria, not any structural document).
- A plan for a task now usually lives directly in `task.description` or as a separate markdown file that must be linked manually.

## Proposal

Introduce the notion of **task-bound document** with a fixed `key`:

```
TaskDocument:
  task_id: str
  key: str           # 'plan' | 'design' | 'verification' | 'acceptance' | <custom>
  title: str
  format: 'markdown'
  body: str
  base_revision_id: str | None    # for optimistic locking
  current_revision_id: str
```

One document per pair (task_id, key); each update creates a new revision, is logged with `run_id` (see [04](04-run-id-audit.md)).

**MCP-tools:**
- `task_doc_get(task_id, key)`
- `task_doc_put(task_id, key, body, base_revision_id?)`
- `task_doc_list(task_id)` — all docs of the task
- `task_doc_revisions(task_id, key)` — history
- `task_doc_revert(task_id, key, revision_id)`

**Canonical keys** (recommendation, not enforcement):

| Key           | Purpose                                                | When mandatory                    |
| -------------- | ------------------------------------------------------ | --------------------------------- |
| `plan`         | Work plan                                              | tasks `kind=feature` complexity > S |
| `design`       | Architectural decisions, schemas                       | tasks touching arch/               |
| `verification` | How to verify what's done                               | any task with status done         |
| `acceptance`   | Acceptance criteria (if not covered by story)          | for standalone tasks without story |

## Difference from existing entities

| Entity                | Scope                | Purpose                       |
| --------------------- | -------------------- | ----------------------------- |
| Global `doc:*`        | project              | specification / architecture |
| `Story` + `criterion` | user story           | business acceptance           |
| `TaskDocument` (new)  | task                 | executor's work context       |

They do not overlap: TaskDocument is "the draft thinking for exactly this task".

## Implementation plan

1. **DB schema + migration.** New table `task_documents` + `task_document_revisions`.
2. **MCP-tools** in [cod_doc/mcp/tools/task_tools.py](cod_doc/mcp/tools/task_tools.py) (or a separate `task_doc_tools.py`).
3. **Skill `plan-to-tasks`** (from [01](01-skills-layer.md)) — update: "the task plan goes into `task_doc_put(task, 'plan', ...)`, not into task.description".
4. **UI:** on the task page — tabs by doc keys; markdown editor with diff between revisions.
5. **Heartbeat-context** (from [02](02-heartbeat-context.md)) — add a slice `task_documents: [{key, current_revision_id, summary}]`.

## Risks

- **Double semantics with `story`.** Solution: a clear rule in the skill — `acceptance` goes into story-criterion if there is a story binding; otherwise into task-doc `acceptance`.
- **Optimistic-lock conflicts.** On parallel edits — explicit error showing the conflict (like `link_sync`).
- **Clutter.** Without enforcement the list of keys can grow. Solution: a warn-check `plan_audit` — flags unusual keys.

## Success metrics

- Plans stop living in `task.description` for tasks of complexity > S.
- For every `done` feature-level task there is a `verification` document.
- Revisions of `plan` docs let you trace the evolution of the approach in controversial tasks.

## Related

- 04 (run-id) — each task-doc revision is tagged with run_id.
- 12 (approvals) — `approval` can reference a specific `plan` revision ("approve plan@rev-abc").
- 09 (activity log) — task-doc changes in the unified timeline.

## Notes (cod-doc context)

- **Story+criterion already exists.** Does not overlap semantically: story-criterion — about business acceptance, task-doc — about the executor's work context. The separation in the RFC is correct, but enforcement (in skill/audit) is mandatory, otherwise the `acceptance` keys will be duplicated between the entities.
- **Links to global docs.** Task-docs will realistically reference `doc:arch_*`, `doc:specs_*`, etc. — we need to make sure the current `link_verify` / `link_sync` (already working) automatically extend to task-docs, otherwise "blind" broken links will appear.
- **Optimistic-lock — a familiar pattern.** `link_sync` already has a similar "base-version + conflict" semantics. Worth reusing the same error format so UI/CLI show conflicts uniformly.
- **Search.** `search_docs` currently does not index task-docs. Decide up front — index immediately or Phase 2; otherwise the project gets a "second class" of docs not covered by search.

## Open questions

- **Q1.** Visibility in `search_docs` and Snowball Protocol — task-docs as full-fledged docs, or a separate category with its own scope?
- **Q2.** Migration of plans from `task.description` — automatic (we cut out the markdown block "Plan:") or a manual task?
- **Q3.** What happens to task-docs on `task.status=cancelled` — freeze (read-only), leave as is, or delete?
- **Q4.** Limit on the number of custom keys per task — is there a cap or a warning?
- **Q5.** Summary of all tasks with old/stale `plan` docs — how to detect (by date, by revision-count, by drift with implementation)?
- **Q6.** Diff between `plan` revisions in UI — markdown-aware or plain text?
