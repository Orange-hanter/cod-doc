---
name: plan-to-tasks
description: |
  How to decompose an execution-plan into task nodes: structured fields
  blocked_by, story_id, affects_files, acceptance. Triggers: plan,
  decompose, split, task_create, blocked_by, story_id, acceptance,
  section, breakdown.
---

# Skill — Plan → Tasks decomposition

## When it loads

Tasks where the LLM / user **decomposes a plan** or RFC into concrete
executable tasks. Trigger keywords: `plan`, `decompose`, `split`,
`breakdown`, `task_create`, `blocked_by`, `story_id`, `acceptance`,
`section`, `phase`, `roadmap`.

## Canonical task node

Every task in the DB is a graph node:

```yaml
task_id: PCA-NNN          # PREFIX-NNN, prefix 2-5 uppercase
title: "Implement: <short description>"
type: feature | test | bug | refactor | migration | docs | chore
priority: critical | high | medium | low
story_id: US-NNN          # link to user story (motivation)
blocked_by: [PCA-MMM]     # IDs of prereq tasks — become dependency rows
affects_files: [path1, path2]  # code / docs the task touches
acceptance: "<one paragraph>"     # condition of done
description: "<context and hints>"
```

## Rules

1. **Title** — imperative with a colon: `Implement: …`, `Refactor: …`,
   `Test: …`, `Migration: …`, `Bug: …`, `Docs: …`. No
   "make a thing" / "fix it".
2. **type** — codifies the nature of the work:
   - `feature` — new functionality.
   - `test` — add test coverage.
   - `bug` — fix a defect.
   - `refactor` — improvement without changing behavior.
   - `migration` — DB / schema / data migration.
   - `docs` — a document or guide.
   - `chore` — housekeeping task (cleanup, deps).
3. **priority** — `critical` for section blockers, `high` for the main
   work, `medium` for needed-but-non-blocking, `low` for chore.
4. **acceptance** — concrete, verifiable. "N+ tests pass", "column
   added", "endpoint returns shape X". Do not write "works correctly".
5. **blocked_by** — only real prereq dependencies. After closing
   PCA-902 (cycle-2 G2) edges land in the `dependency` table and are
   visible in `plan.ready` / `plan.audit` / `critical_path`.
6. **affects_files** — path from the repo root. Helps the agent find the
   task context without grep (see capability `observability-and-indexing`
   US-023).

## Algorithm for a large plan

1. Name the section — a letter (A, B, C, ...) + a name.
2. Assign an ID-prefix (3 uppercase letters) — `PCA`, `OBI`, `WEB`, `COD`.
3. Decompose into tasks of 1-3 hours each. If a task is "1+ day" —
   split further.
4. For each task:
   - Pick `type` and `priority`.
   - Link to `story_id` (motivation — what we close).
   - Fill `blocked_by` from existing prereq tasks.
   - List `affects_files`.
   - Frame `acceptance` in one paragraph.
5. Write to the DB via `task.create` (MCP) **and** to the markdown
   execution-plan (mirror).

## What NOT to do

- Do not create tasks without `acceptance`.
- Do not set `affects_files=[]` — better list the most likely candidates
  than leave it empty.
- Do not overload `description` — for large context use an attached
  `task_doc` (proposal 05 → US-009; awaits implementation).
- Do not write `blocked_by` with tasks from other projects / plans.

## Related

- [standards/task-plan.md](../../../docs/system/standards/task-plan.md)
- [capabilities/plan-management.md](../../../docs/system/capabilities/plan-management.md)
- [capabilities/user-stories-graph.md](../../../docs/system/capabilities/user-stories-graph.md)
