---
name: task-flow
description: |
  Working with cod-doc tasks: atomic checkout → work → complete with
  commit-sha, creating tasks and plan sections, grooming. Tool order:
  MCP tools → CLI → service layer. Triggers: task, checkout, complete,
  close task, take task, plan ready, plan section, task queue,
  blocked_by.
---

# Task flow — a task's status lives in the DB

The source of truth is `<project>/.cod-doc/state.db`, not markdown.
Closing a task by editing .md is not a close, but a divergence: the
projection will say "done", the queue and `blocked_by` will stay as
before.

The project slug is needed in almost every call. Define it once:
`cod-doc project list`, or
`sqlite3 -readonly .cod-doc/state.db "select slug, root_path from project"`.
Mutating ad-hoc SQL on this DB is forbidden: revisions and activity
events are not written bypassing it.

## Cycle

1. **Queue** — `plan_ready(project, plan_scope)` or `task_next_ready`.
   Dependencies are already accounted for, take the top of the list. CLI:
   `cod-doc plan ready <scope> -p <slug> --json`.
2. **Capture — atomic `task_checkout(project, task_id, agent="claude-<topic>")`.**
   Not a bare status transition: `todo → in_progress` via
   `task_update_status` fails by protocol. Idempotent for the same
   `agent`; a foreign lock gives a conflict, not an overwrite.
3. **Work.** Intermediate progress — `task_log_progress`, not a
   comment in markdown. The project gate — before closing, not after.
4. **Commit** — conventional + task ID: `feat(scope): TASK-ID — essence`.
5. **Close** — `task_complete(project, task_id, commit_sha=..., author=...)`.
   Validates `blocked_by`, releases the lock, writes a revision +
   activity event. Did not finish — `task_release`, not a silently
   abandoned lock.

## What lives where

| Need | Tool |
|---|---|
| checkout | only MCP `task_checkout` (not in CLI) |
| plan section | MCP `plan_section_create` or CLI `cod-doc plan section-create` |
| grooming (description / acceptance / priority) | `task_update` — both in MCP and CLI (`cod-doc task update`) |
| title change | by design no: `cancel` with a reason + a new task |
| duplicate search before creating | `task_find_duplicate` |
| stale tasks | `task_stale`, `task_list_blocked` |

## Statuses

Seven canonical buckets: `todo`, `in_progress`, `blocked`, `review`,
`done`, `cancelled`, `deferred`; legacy aliases `pending` ≡ `todo`,
`in-progress` ≡ `in_progress`. The single source of truth on
transitions is `services/task_status_machine.py::ALLOWED_TRANSITIONS` in
cod-doc itself; do not invent transitions from memory, ask
`capabilities` / `tool_describe`.

## Service layer (fallback, when MCP is unavailable)

Mutations — only through services, they write revisions and events
themselves. Put the script in the session's temporary directory, not in
the repository:

```python
from pathlib import Path
from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url, transactional
from cod_doc.services import checkout_service, task_service

sf = make_session_factory(make_engine(resolve_db_url(Path("<project-root>"))))
with transactional(sf) as s:
    checkout_service.checkout(s, task_id="ABC-002", agent="claude-x")   # kwarg exactly agent
    task_service.complete(s, task_id="ABC-002", author="claude-x", commit_sha="abc1234")
```

Create: `task_service.create(s, project_id=..., plan_id=..., section_id=...,
title=..., type=TaskType..., priority=Priority..., author=..., id_prefix="ABC",
description=..., acceptance=..., blocked_by=[...])` — id is issued as
`{prefix}-NNN`.

## Before closing

- The acceptance criterion is fulfilled literally; "by meaning" is not
  fulfilled.
- The project gate is green.
- The last task of a plan section is closed → an audit-report in
  `docs/system/audit/`, if the project keeps this convention.
