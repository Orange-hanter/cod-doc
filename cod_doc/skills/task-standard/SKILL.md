---
name: task-standard
description: |
  Standard for setting up and formatting tasks. Title format, mandatory and
  recommended fields, when to add acceptance, how to use blocked_by /
  story_id / affects_files, priority semantics, status-flow, when to split
  a task, anti-patterns.
  Triggers: task, task_create, add_task, update_task, decompose, breakdown,
  new task, plan task, acceptance, blocked_by, story_id, affects_files.
---

# Skill — Task standard

## When it loads

Tasks where the LLM / user **creates**, **splits**, or **sets up** a task:
`task_create`, `task_update_status`, `plan_create`, manual setup via web /
MCP. Trigger keywords: `task`, `task_create`, `add_task`, `update_task`,
`acceptance`, `blocked_by`, `story_id`, `affects_files`, `decompose`,
`breakdown`, `plan task`.

## Principle

A task is a node in the execution graph. It MUST be atomic (one executor,
one merge), verifiable (has a DoD), and recognizable (the name describes an
action, not a subject area).

## 1. Title

- **Verb first**, imperative: "Implement X", "Refactor Y", "Fix Z",
  "Audit module Auth", "Migrate from A to B".
- **≤ 80 characters**, no trailing period, no emoji.
- **No vague words** ("improve ui", "fix", "finish", "sort out"). If it is
  unclear WHAT to do — the task is not ready yet, return it to the backlog
  with a TODO in the description.

## 2. Mandatory fields on creation

| Field | Why |
|------|-------|
| `title` | See §1 |
| `type` | `feature` / `bug` / `refactor` / `test` / `docs` / `chore` |
| `priority` | See §4 — filtering and sorting of the ready-batch |
| `plan_id` | An orphan without a plan = invisible on the kanban board |
| `section_id` | Grouping within the plan |

Creating a task without `plan_id` is forbidden. If there is no plan —
first `plan_create`, then `task_create`.

## 3. Recommended (almost mandatory) fields

| Field | When mandatory |
|------|-------|
| `description` | Always for `feature` / `refactor`. Describes WHY + context; does not retell the title. |
| `acceptance` | For `priority >= medium`. Definition of Done — checklist format. Without AC a task is not closed. |
| `affects_files` | If the scope is limited to ≤ 5 files. Used by the drift audit. |
| `blocked_by` | Task_ids that MUST complete earlier. Not "would be nice", but technically impossible to start without them. |
| `story_id` | If the task implements part of a user-story — mandatory. Links execution to requirements. |

## 4. Priority semantics

- `critical` — incident / release blocker. You take it TODAY, everything
  else is postponed.
- `high` — falls into the current sprint / next ready-batch.
- `medium` — default. Taken in order of readiness.
- `low` — nice-to-have. Ready to postpone for a quarter. Does not block
  any user-story.

No more than **20%** of plan tasks should have `critical` / `high` —
otherwise priorities become devalued.

## 5. Status flow

Canonical 7-state taxonomy (proposal 08, single source —
`cod_doc/services/task_status_machine.py::ALLOWED_TRANSITIONS`):

```
backlog ─→ todo ─→ in_progress ─→ in_review ─→ done
                       │              │
                       ↓              ↓
                    blocked        cancelled
                       │
                       └──→ todo (after the blocker is resolved)
```

- `backlog` — in the plan, but not prioritized.
- `todo` — ready to pick up.
- `in_progress` — taken into work. There must be commits ≤ 24h.
  **The `todo → in_progress` transition goes ONLY through `task_checkout`**
  (proposal 06, PCA-200) — `task_update_status` will reject it.
- `in_review` — code / docs ready, awaiting review. AC is not marked ✓
  until review.
- `blocked` — **ALWAYS** fill `blocked_reason`. Without a reason it is
  impossible to unblock.
- `done` — completed, AC checklist all ✓, drift-check (see skill
  `module-audit`) passed for the module if needed.
- `cancelled` — cancelled. Why — a comment via `task_log_progress`.

### Legacy aliases

In older tasks / `tasks.yaml` you will encounter a 3-state set. They are
valid on a par with the canonical ones —
`task_status_machine.normalise` collapses them into the corresponding
bucket before checking the transition:

| Legacy | Canonical |
|--------|-----------|
| `pending` | `todo` |
| `in-progress` (with hyphen) | `in_progress` (with underscore) |
| `done` | `done` |

When writing new tasks use canonical names; legacy remains for
backward-compat only.

### Editing an already created task (grooming)

Reformulating the scope and re-evaluating the priority is a routine
operation, not a reason to dig into the DB (ADO-067):

| Surface | How |
|---|---|
| MCP | `task_update(project, task_id, description=…, acceptance=…, priority=…)` — any subset of fields; the response contains `updated_fields` |
| CLI | `cod-doc task update TASK_ID -p SLUG --description … --acceptance … --priority …` |

Each changed field writes a revision and an activity event
(`task.description_updated` / `task.acceptance_updated` /
`task.priority_changed`), so `--reason` is worth filling in.

The tool does not change `title`: renaming is changing the task identity,
create a new one and cancel the old one. Status lives separately
(`task_update_status` / `task_checkout`).

## 6. When to split a task

Split into subtasks (via `blocked_by`) if AT LEAST ONE holds:

- ≥ 1 working day of one executor
- > 3 files changed by one merge
- AC decomposes into "do A" + "do B" + "do C"
- Different types (`feature` + `test` + `docs`) — each as a separate task

A subtask inherits the parent's `plan_id` / `section_id`.

## 7. Anti-patterns

- ❌ "Improve the documentation" — no scope, no DoD.
- ❌ in_progress without commits > 24h — assign a blocker or return to todo.
- ❌ done without AC check — pencil-whip.
- ❌ blocked without `blocked_reason` — impossible to unblock.
- ❌ Task without `plan_id` — orphan. First create / choose a plan.
- ❌ All tasks priority=high — devalues the filter.
- ❌ AC of the form "should work" — not verifiable. Write as a checklist:
      "✓ POST /api/x returns 201 with {id, created_at}; ✓ DB-row created
      in table Y; ✓ test in tests/api/test_x.py covers happy + 400-error".

## Related

- Full format specification: `docs/system/standards/task-plan.md`.
- Skill `plan-to-tasks` — how to decompose an execution-plan into nodes.
- Skill `module-audit` — what to check when closing a module.
- Skill `validation` — write-path validation of task structure.
