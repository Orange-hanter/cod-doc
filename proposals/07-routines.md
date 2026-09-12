# 07 — Routines (cron triggers)

> Category: 🟡 Adaptation · Risk: medium · Dependencies: 03 (wake-payload)

## Context: like paperclip

Routines are recurrent tasks. Each launch creates an **issue** assigned to a routine agent — it picks it up via the normal heartbeat flow.

```
POST /api/companies/:id/routines
{
  "name": "weekly-report",
  "agentId": "...",
  "triggers": [{ "type": "schedule", "cron": "0 9 * * MON" }],
  "concurrencyPolicy": "skip" | "queue" | "parallel",
  "catchUpPolicy": "run_once" | "run_all" | "skip"
}
```

Supported triggers: `schedule` (cron), `webhook`, `api`. Concurrency and catch-up policies — first-class.

## Current state of cod-doc

cod-doc already has a set of "health checks", but they are invoked manually from CLI or UI:
- [stale_refs](cod_doc/services/routine_service.py) — search for stale links in MASTER.md; legacy MCP tool `check_stale_refs` was removed in `c310503`.
- [link_verify](cod_doc/mcp/tools/link_tools.py) — link integrity check.
- [doc_drift](cod_doc/mcp/tools/doc_tools.py) — sha-mismatch detection.
- `plan_audit`, `task_stale` — planning checks.

Missing:
- a schedule for their launch,
- automatic task creation on found problems,
- a unified "health pulse" panel of the project.

## Proposal

1. **`Routine` entity:**
   ```python
   @dataclass
   class Routine:
       id: str
       name: str
       enabled: bool
       trigger: RoutineTrigger        # cron | manual | event
       cron: str | None
       check: str                     # check function name
       check_args: dict
       on_finding: OnFindingPolicy    # create_task | update_existing_task | comment_only
       concurrency: Literal['skip', 'queue']
       catch_up: Literal['skip', 'run_latest']
   ```

2. **Catalog of built-in checks:**

   | Name                | Source                                  | Recommended schedule     |
   | ------------------- | --------------------------------------- | ------------------------ |
   | `stale_refs`        | `check_stale_refs`                      | every hour               |
   | `link_integrity`    | `link_verify`                           | every 4 hours            |
   | `doc_drift`         | `doc_drift`                             | every 30 min (light)     |
   | `task_stale`        | `task_stale`                            | daily                    |
   | `plan_audit`        | `plan_audit`                            | on every merge           |
   | `revision_pruning`  | cleanup of old revisions > N           | weekly                   |

3. **`on_finding` behavior:**
   - `create_task` — if a problem is found, create a task of the needed type (e.g. drift → task `kind=fix`, bound to the found doc-ref).
   - `update_existing_task` — if there is already an open task with the same signature, add a comment with the delta; do not spawn a new one.
   - `comment_only` — write to the activity log (see [09](09-activity-log.md)) without creating a task.

4. **Trigger → wake.** A routine on fire assembles a `WakeContext` (see [03](03-wake-payload.md)) with `reason='routine_<name>'` and a `payload` containing findings + diff + recommended action.

5. **Concurrency:**
   - `skip` — if the previous run is still going, skip.
   - `queue` — put in a queue (but with a cap, e.g. 3).

6. **Catch-up:**
   - `skip` — skip missed ticks.
   - `run_latest` — run once, as an "accumulated" check.

## Implementation

- **Scheduler:** there is already a daemon in [cod_doc/services/](cod_doc/services/) — add a scheduler-loop (e.g. on `apscheduler` or a simple homegrown cron).
- **MCP-tools:**
  - `routine_list` / `routine_get` / `routine_create` / `routine_update_status` (enable/disable) / `routine_run_now`
  - `routine_history(routine_id, limit)` — recent runs and their results
- **Storage:** tables `routines`, `routine_runs`.
- **UI:** a "Routines" page — table + "Run now" button + recent results.

## Risks

- **Noise.** Too-frequent crons create a hailstorm of tasks. Solution: strict defaults (see the table above) + `update_existing_task` policy for repeats.
- **Drift on the DB side.** If the cron-scheme crashes — the miss is invisible. Solution: routine_runs are always written, the dashboard shows "not run for N hours".
- **Duplication with existing daemon logic.** First make sure the current drift-watcher migrates into this framework, not coexists.

## Success metrics

- 100% of "health checks" are formalized as routines.
- 0 launches from CLI/UI "manually, because forgot to schedule".
- Found drifts automatically become tasks in the orchestrator queue.

## Related

- 03 (wake-payload) — routine-trigger gives a WakeContext with a payload of findings.
- 04 (run-id) — routine-run = one run_id, all created tasks and comments are tagged.
- 09 (activity log) — `routine.fired`, `routine.found_issue`, `routine.created_task` — events.

## Notes (cod-doc context)

- **The existing drift-watcher must migrate.** The daemon already has "find drift → create task" logic. Do not leave the old watcher in parallel with routines — there will be task duplicates. Migration of the first routine = removal of the equivalent piece from the daemon.
- **`update_existing_task` — must-have policy.** Drift repeats on the same docs; without dedup the queue overflows. Signature for dedup — a deterministic hash over `(check_name, scope_kind, scope_id, finding_kind)`.
- **Defaults for single-user.** Concurrency `skip` + catch-up `run_latest` — the only reasonable ones. `parallel`/`queue` for single-user mode only complicate debugging.
- **Pause without deletion.** The ability to temporarily disable a routine is critical during debugging (e.g. `doc_drift` every 30 min is noisy while you fix in a batch). Need an `enabled` field separate from deletion.
- **Schedule in local TZ.** Cron expressions for single-user are more logically interpreted in the user's local timezone, not UTC. Requires fixing in config.

## Open questions

- **Q1.** Where does the existing daemon health-check logic migrate — atomically in one PR (risk of regressions) or phased (risk of temporary duplication)?
- **Q2.** Cron parser — standard (`croniter`/`apscheduler`) or a homegrown mini? Do we pull deps for `0 9 * * MON`?
- **Q3.** How to count "same signature" for `update_existing_task` — a fixed set of fields or configurable per-routine?
- **Q4.** Routine-runs storage — next to `agent_runs` ([04](04-run-id-audit.md)) or a separate table? If next to — a `kind=routine` field?
- **Q5.** Can custom routines be created via UI/CLI, or only the built-in ones from the catalog?
- **Q6.** What to do if the `check` function crashed with an exception — record `routine_run.failed` and retry on the next tick, or escalate after N consecutive failures?
