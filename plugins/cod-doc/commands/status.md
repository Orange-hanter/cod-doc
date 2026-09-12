---
description: A snapshot of the cod-doc project — plan progress, ready tasks, stuck checkouts, markdown ↔ DB drift
argument-hint: "[project slug]"
---

Collect the cod-doc project status and report it on one screen.

Project: `$1` — if empty, determine the slug yourself (`cod-doc project list`; in
the embedded DB: `sqlite3 -readonly .cod-doc/state.db "select slug, root_path from project"`)
and name it in the answer. If there is no `.cod-doc/state.db` in the repository — the project is not
connected, say so and suggest `/cod-doc:setup`, do not go further.

Collect (MCP tools are preferable to the CLI, no ad-hoc python scripts):

1. **Tasks** — `task_summary`: how many in which status.
2. **Stuck** — tasks in `in_progress` with `checked_out_by`: these are unclosed
   checkouts from past sessions, each is either continued or `task_release`.
3. **Queue** — `plan_ready` for the active plan (or `task_next_ready`):
   the top of the list, 3–5 items with priority.
4. **Blockers** — `task_list_blocked`, if any.
5. **Drift** — `ctx_drift`: the count of `edited_in_place` (a defect, fixed by
   `doc import`) separately from `stale_export` (the norm, do not touch).

Answer format: a "metric → value" table, below it — no more than three lines
about what requires action right now. Do not fix anything without a separate request:
this is a report, not a repair.
