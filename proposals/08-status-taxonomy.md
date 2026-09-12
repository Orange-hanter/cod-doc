# 08 — Status taxonomy: `in_review` ≠ `blocked`

> Category: 🟡 Adaptation · Risk: low · Dependencies: —

## Context: like paperclip

Full set: `backlog | todo | in_progress | in_review | done | blocked | cancelled`.

The skill explicitly fixes the semantics of each:

| Status        | Semantics                                                                                  |
| ------------- | ------------------------------------------------------------------------------------------ |
| `backlog`     | Parked, not now. Not for an active heartbeat.                                              |
| `todo`        | Ready to work, not taken. Transition to `in_progress` **only** through `checkout`.         |
| `in_progress` | Actively in progress, has an owner with a lock.                                            |
| `in_review`   | **Healthy waiting-path** — waiting for review/approval/answer. NOT a synonym of done.     |
| `blocked`     | Cannot move until something changes. Mandatory `blockedByIssueIds` or owner.              |
| `done`        | Closed.                                                                                    |
| `cancelled`   | Cancelled intentionally, will not resume.                                                   |

Key pattern: `in_review` is an **explicit waiting posture**. When the agent creates an approval-request or waits for a human decision, the task goes to `in_review`, not to `blocked`.

A `cancelled` blocker does NOT count as resolved — it must be explicitly removed or replaced.

## Current state of cod-doc

In [cod_doc/core/project.py](cod_doc/core/project.py) `TaskStatus` exists, but:
- `in_review` (if present) is semantically mixed with `blocked`,
- there is no hard rule "`todo → in_progress` only through checkout" (see [06](06-atomic-checkout.md)),
- FM-002/FM-003 escalations (from the agent's memory) have no dedicated status — they usually live in comments or ad-hoc "postponed while I ask".

## Proposal

### 1. Fix the set of statuses as canonical

```python
class TaskStatus(StrEnum):
    BACKLOG = 'backlog'
    TODO = 'todo'
    IN_PROGRESS = 'in_progress'
    IN_REVIEW = 'in_review'      # waiting for human/approval
    BLOCKED = 'blocked'          # waiting for another task
    DONE = 'done'
    CANCELLED = 'cancelled'
```

If some of these statuses are missing in the current code — a migration with a mapping of old values.

### 2. Hard transition rules

| From          | Allowed To                                      | Condition                                |
| ------------- | ----------------------------------------------- | ---------------------------------------- |
| `backlog`     | `todo`, `cancelled`                             | —                                        |
| `todo`        | `in_progress`                                   | **only** through `task_checkout` (06)    |
| `todo`        | `blocked`, `backlog`, `cancelled`               | direct PATCH OK                          |
| `in_progress` | `in_review`, `blocked`, `done`, `cancelled`     | requires a valid checkout                |
| `in_review`   | `in_progress`, `done`, `cancelled`               | on resolve approval/review               |
| `blocked`     | `todo`, `in_progress`, `cancelled`              | `todo` auto on resolve `blockedBy`       |
| `done`        | `todo`, `in_progress`                           | reopen, requires confirmation            |
| `cancelled`   | `todo`                                          | reopen, explicitly                       |

### 3. Binding to existing cod-doc patterns

From project memory:
- **FM-002, FM-003 (structural validation → raise)** → the task transitions to `blocked` with `blockedByIssueIds=[<new task for the fix>]`.
- **FM-004, FM-005 (advisory)** → the task stays in `in_progress`, a comment is added to the activity log.
- **Approval-request** (see [12](12-approvals.md)) → `in_review` with an explicit `pending_approval_id`.

### 4. Auto-wake rules

- On closing a task (`status=done`) — all its `blockedBy`-dependents are automatically checked: if all blockers resolved → wake the assignee of the dependent task (see [03](03-wake-payload.md)).
- `cancelled` does NOT resolve a blocker. UI and the MCP-tool `task_set_blocker` warn if there is a cancelled task in the blockers.

### 5. UI

- Kanban columns: `backlog | todo | in_progress | in_review | done`. `blocked` — an overlay badge (shows blockers), `cancelled` — a separate filter.
- The `in_review` card explicitly shows "waiting for: <approval/user/review>".

## Implementation plan

1. **Audit the current list of TaskStatus.** What is already there, what to add.
2. **State-machine.** A pure function `validate_transition(from, to, context) -> Result`. Tests for each transition.
3. **Refactor MCP-tools.** `task_update_status` uses the state-machine.
4. **Auto-wake hook.** On transition to `done` or `cancelled` — an event, the handler wakes dependents.
5. **UI.** Update kanban columns.
6. **Documentation.** A section in the `validation` skill (see [01](01-skills-layer.md)).

## Risks

- **Backward compatibility.** If some tasks are already in "not from the list" statuses — the migration must explicitly map them. A migration script with a preview.
- **`in_review` overload.** The temptation to put there everything "not ready, but not blocked". Solution: a rule "an `in_review` always has a `pending_*` field — approval, comment, doc-revision-pending".

## Success metrics

- 0 tasks in `in_progress` without a valid checkout.
- Every `blocked` task has either `blockedByIssueIds` or `blocker_owner: <user>`.
- Average time in `in_review` reduced (visibility → faster reaction).

## Related

- 06 (checkout) — defines how `todo → in_progress` happens.
- 12 (approvals) — the typical source of the `in_review` status.
- 09 (activity log) — status transitions — first-class events.

## Notes (cod-doc context)

- **Auditing the existing list — the first task.** Before fixing the set, look in [cod_doc/core/project.py](cod_doc/core/project.py): maybe `in_review` is already there, maybe not. The volume of migration depends on this.
- **`hypothesis` is already in dev-deps.** Perfectly suited for covering the state-machine — we generate (from, to) pairs, check the law "either allowed, or refusal with a reason". Don't miss negative cases.
- **Migration with preview.** The migration script must first show which tasks change status and to what, and only on confirm apply. A separate command `cod-doc migrate-statuses --dry-run`.
- **`in_review` overload — a real risk.** The temptation to put there "well, not blocked, but not active". The rule "an `in_review` always has a `pending_*` field" (approval_id / comment_id / doc_revision_id) — a mandatory invariant.
- **Auto-wake on blocker resolve.** `task_clear_blocker` already exists now, but does not wake dependents. Tie to [03](03-wake-payload.md): clear → emit event → assemble wake-context for the dependent task.

## Open questions

- **Q1.** What to do with tasks whose current status is not from the canonical list (if any are found)? Mapping by heuristic or manual triage?
- **Q2.** `done → todo` reopen — what does it require: a `force=True` flag, an approval, or an explicit comment in audit?
- **Q3.** On `cancelled` — what about TaskDocuments ([05](05-issue-documents.md)): freeze (read-only), leave editable, or soft-delete?
- **Q4.** The `cancelled` column in kanban — a separate column, a "Show cancelled" filter, or always hidden?
- **Q5.** `blocked` without `blockedByIssueIds` (only `blocker_owner: <user>`) — allowed or a validation error?
- **Q6.** Timeout on `in_review` — is there a smart-default (e.g. 7 days without resolve → wake the operator), or only through [12](12-approvals.md)?
