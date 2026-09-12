---
description: Take a cod-doc task into work by the protocol (checkout → work → complete with a sha) or close the current one
argument-hint: "next | <TASK-ID> | done <TASK-ID> <sha> | release <TASK-ID>"
---

Argument: `$ARGUMENTS`.

Determine the project slug yourself (see `/cod-doc:status`), if it is not named explicitly.
Work with the cod-doc MCP server tools; the CLI is a fallback path, ad-hoc SQL against
`state.db` for writes is forbidden (bypasses revisions and activity events).

**Argument parsing**

| Argument | What to do |
|---|---|
| empty or `next` | `task_next_ready` (or `plan_ready` for the active plan) → show the top 3–5 and **ask** which one to take; do not grab one yourself |
| `<TASK-ID>` | `task_get` → show the description and acceptance → `task_checkout(project, task_id, agent="claude-<topic>")` |
| `done <TASK-ID> <sha>` | `task_complete(project, task_id, commit_sha=<sha>, author=...)` |
| `release <TASK-ID>` | `task_release` — release without closing |

**Protocol (must not be violated)**

1. The `todo → in_progress` transition — only through `task_checkout`. A direct
   `task_update_status` on this transition fails: checkout is atomic and takes
   a lock, someone else's lock is a conflict, not an overwrite.
2. While the task is in progress — write progress via `task_log_progress`, not into markdown.
3. Closing — `task_complete` with the sha of the real commit. It checks
   `blocked_by`, releases the lock, writes a revision + an activity event. Closing
   by editing an .md is not closing; the status lives in the DB.
4. The commit — conventional + the task ID: `feat(scope): TASK-ID — short summary`.
5. Closed the last task of a plan section — remind about the audit report
   (`docs/system/audit/`, if the project follows this convention).

Before `task_complete` make sure the project gate is green (in cod-doc this is
`/gate`), and that the acceptance criterion is met literally, not "by feel".
If the acceptance is not met — do not close, report the discrepancy.
