# 06 — Atomic task checkout

> Category: 🟡 Adaptation · Risk: low · Dependencies: 08 (statuses)

## Context: like paperclip

```
POST /api/issues/:id/checkout
{
  "agentId": "...",
  "expectedStatuses": ["todo", "backlog", "blocked", "in_review"]
}
```

Semantics:
- If the issue is in `expectedStatuses` → atomically transition to `in_progress`, assign to agent, issue a lock.
- If already with this agent → return OK (idempotent).
- If with another → `409 Conflict`. Skill: **"Never retry a 409"**.
- All task mutations require a valid active checkout.

Effect:
- Impossible to "accidentally" work on someone else's task.
- Impossible to start a task from a non-expected state (catch a stale plan).
- The `todo → in_progress` transition — **through checkout**, not through a direct PATCH (this is the rule).

## Current state of cod-doc

- In [task_tools.py](cod_doc/mcp/tools/task_tools.py) `task_update_status` accepts any transition without an optimistic check.
- Possible race scenarios:
  - A UI tab shows the task `todo`, the operator clicks "start" → the agent already took it and it's `in_progress`. UI rewrites inconsistently.
  - The daemon triggers a wake on drift, while a human edits the same task via CLI.
- There is no notion of "the active executor of the task right now".

## Proposal

1. **Add fields** to Task ([cod_doc/core/project.py](cod_doc/core/project.py)):
   - `checked_out_by: str | None` (run_id or 'human:<user>')
   - `checked_out_at: datetime | None`
   - `expected_status_at_checkout: TaskStatus | None`

2. **MCP-tool `task_checkout(task_id, agent='orchestrator'|'human:<id>', expected_statuses: list[TaskStatus])`:**
   - Atomic transaction: check status ∈ expected_statuses + set `checked_out_by`.
   - If already checked out by the same actor → OK (idempotency).
   - If by another → `CheckoutConflictError(409)`.
   - The `todo → in_progress` transition happens **here**, not via `task_update_status`.

3. **MCP-tool `task_release(task_id, run_id)`:**
   - Releases the lock. Called explicitly (after the task) or automatically by timeout from the daemon.

4. **All task write-tools** check: the operation is possible only if the caller owns the checkout (or an explicit `force=True` for admin cases).

5. **Stale-checkout watchdog:** a daemon every N minutes cleans locks older than TTL (e.g. 30 minutes without run activity).

## Changes in the orchestrator skill

- "Before mutating a task — `task_checkout`. On 409 — do NOT retry, pick another task or escalate".
- "On completion — `task_release` explicitly".

## Implementation plan

1. **DB migration.** Fields `checked_out_by`, `checked_out_at`, `expected_status_at_checkout`.
2. **Atomic function `_checkout`.** Via `SELECT ... FOR UPDATE` or (for SQLite) `BEGIN IMMEDIATE` + check-update in one transaction.
3. **MCP-tools** `task_checkout`, `task_release`.
4. **Refactor `task_update_status`:** forbid the direct `todo → in_progress` transition (only through checkout); other transitions — through update, but with an ownership check.
5. **UI:** show "in use by: orchestrator-run-X" on the card; a "force release" button for the admin.
6. **Watchdog** in [cod_doc/services/](cod_doc/services/).

## Risks

- **Breaking existing flow.** There may already be places in the code doing a direct `todo → in_progress`. Solution: a two-step migration — first add checkout as an option (warn without it), then enforce.
- **Lock-leak.** Orchestrator crash without release. Solution: TTL + watchdog (see above).
- **UX friction for a single user.** In 95% of cases there is simply no lock, and this works transparently. A conflict is a rare event, but when it happens — it saves you.

## Success metrics

- 0 race conditions on parallel UI + daemon work.
- All tasks with `status=in_progress` have a valid `checked_out_by`.
- The watchdog catches < 1% "stuck" checkouts per week (if more — the bug is elsewhere).

## Related

- 04 (run-id) — `checked_out_by` stores the orchestrator's run_id.
- 08 (statuses) — defines `expectedStatuses` for different transitions.
- 09 (activity log) — checkout/release — first-class events.

## Notes (cod-doc context)

- **SQLite — `BEGIN IMMEDIATE`.** We have a sqlite backend, so `SELECT ... FOR UPDATE` does not apply. We need an explicit `BEGIN IMMEDIATE` + check-update in one transaction. Tests must explicitly cover the race — `pytest-xdist` or manual thread-stress.
- **Phased enforce.** A hard checkout requirement will immediately break existing places doing a direct `task_update_status(todo→in_progress)`. Phase 1 — warn-mode with a log "no checkout, proceeded", Phase 2 — enforce.
- **UI after COD-078.** The UI redesign added quick actions — the real probability of a UI ↔ daemon race grew. This is an argument for faster adoption.
- **"In use by" indicator.** We need to show `checked_out_by` on the task card; for single-user it will sometimes be `human:dakh`, sometimes `orchestrator-run-X`. Distinguish visually.
- **Real volume of races.** Before adoption it makes sense to add a log hack: write to the activity log when `task_update_status` now changes the status of a task someone touched < 5 seconds ago. That way we see the frequency of the real problem.

## Open questions

- **Q1.** Watchdog TTL — is 30 minutes reasonable for a single agent? If the agent does a long LLM iteration (>10 min), heartbeats to extend the lock or a wide enough TTL?
- **Q2.** Existing tasks in `in_progress` without checkout — set `checked_out_by='legacy:human'` on migration or reset to `todo`?
- **Q3.** "Force release" from UI — who has the right (any local cod-doc user), or is an owner attribute needed?
- **Q4.** Idempotency for CLI — does a repeated `cod-doc task checkout COD-N` by the same session return OK without overwriting `checked_out_at`?
- **Q5.** What to do with the lock on `cancelled` — auto-release or explicit?
- **Q6.** Does the UI update the status via polling or websocket? This affects how many races the user sees at all.
