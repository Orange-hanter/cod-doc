# 09 — Activity & Events log (unified timeline)

> Category: 🟡 Adaptation · Risk: medium · Dependencies: 04

## Context: like paperclip

The documentation directly calls this "one of the systems":
> *"Activity & Events — Mutating actions, heartbeat state changes, cost events, approvals, comments, and work products are recorded as durable activity so operators can audit what happened and why."*

All mutations, status transitions, cost-events, approvals — a single durable stream. This lets:
- one UI page answer "what happened today in the project / with this agent",
- collect metrics (productivity, drift-frequency),
- debug "why did the task suddenly change".

## Current state of cod-doc

Audit is fragmented:
- Revisions — only for docs ([revision_tools.py](cod_doc/mcp/tools/revision_tools.py)).
- Task status changes — no separate log (only the final state in the DB).
- Agent runs — are not stored anywhere as an entity ([04](04-run-id-audit.md) fixes this).
- Findings from drift checks — are lost after the console output.

There is no answer to:
- "What changed in the project over the past week?"
- "Which agent run last touched MASTER.md?"
- "When did this task get a blocker and who removed it?"

## Proposal

A unified `activity_events` table (append-only):

```sql
CREATE TABLE activity_events (
  id           TEXT PRIMARY KEY,         -- uuid7 for sorting
  ts           TIMESTAMP NOT NULL,
  actor_kind   TEXT NOT NULL,            -- 'orchestrator' | 'human' | 'routine' | 'system'
  actor_id     TEXT,                     -- user id, agent id, routine name
  run_id       TEXT,                     -- from 04, NULL for direct human actions
  kind         TEXT NOT NULL,            -- canonical event kind (see below)
  scope_kind   TEXT,                     -- 'task' | 'doc' | 'story' | 'project'
  scope_id     TEXT,                     -- entity id
  payload      JSON,                     -- typed per kind
  summary      TEXT                      -- human-readable string
);

CREATE INDEX idx_activity_ts ON activity_events(ts DESC);
CREATE INDEX idx_activity_scope ON activity_events(scope_kind, scope_id, ts DESC);
CREATE INDEX idx_activity_run ON activity_events(run_id);
```

### Canonical `kind` values

| `kind`                          | Semantics                                            |
| ------------------------------- | ---------------------------------------------------- |
| `task.created`                  | Task created                                         |
| `task.status_changed`           | Status changed (payload: from/to)                    |
| `task.checked_out` / `released` | Lock from [06](06-atomic-checkout.md)                |
| `task.blocker_added` / `cleared` | Blockers changed                                    |
| `task.commented`                | Comment added                                        |
| `doc.created` / `updated` / `renamed` | Global doc changed                             |
| `doc.drift_detected`            | sha-mismatch detected                                |
| `task_doc.updated`              | Task-bound doc changed (see [05](05-issue-documents.md)) |
| `master.updated`                | MASTER.md updated (hashes, sections)                 |
| `link.synced` / `broken`        | Link changes                                         |
| `run.started` / `finished` / `failed` | Lifecycle of an agent-run                       |
| `routine.fired` / `found_issue` / `created_task` | From [07](07-routines.md)             |
| `approval.requested` / `resolved` | From [12](12-approvals.md)                          |

### Event sources

Each MCP-write-tool **additionally** to its write writes an event. Implementation — via a decorator/middleware in [cod_doc/mcp/tools/_db.py](cod_doc/mcp/tools/_db.py) or explicit `record_event(...)` calls.

### MCP-tools for reading

- `activity_list(scope_kind?, scope_id?, kind?, since?, until?, actor?, limit?)` — main filter.
- `activity_for_run(run_id)` — what happened in a specific run (complements [04](04-run-id-audit.md)).
- `activity_summary_daily(date_range)` — aggregates for the dashboard.

### UI

- **Project timeline** — event feed for the project.
- **Task timeline** — on the task card (replaces/complements existing comments).
- **Run page** — all events of the run (complements [04](04-run-id-audit.md)).
- **Daily digest** — on the home page: "yesterday: 12 events, 2 drifts, 3 closed tasks".

## Implementation plan

1. **Schema + migration.** Table + indexes.
2. **`Event` model** in [cod_doc/core/](cod_doc/core/) + canonical kinds enum.
3. **Recording.** Phased connection of write-tools:
   - Phase 1: tasks (creation, status, blockers, comments).
   - Phase 2: docs (including task_docs from [05](05-issue-documents.md)).
   - Phase 3: master, links, runs, routines, approvals.
4. **Read MCP-tools.**
5. **UI:** a basic timeline-component on one page, then — embed into cards.
6. **Retention:** old events (> 6 months) can be archived to a separate table/file, so the main table stays snappy.

## Risks

- **Duplication with revisions.** Solution: revisions are snapshots of **content**; events are **facts of changes** with context (actor, run, summary). They are complementary, not alternative.
- **Table size.** Simple partitioning by month + retention.
- **Inconsistency on crash.** Event recording and the mutation itself — in one transaction (if the DBMS allows) or via outbox-pattern.

## Success metrics

- 100% of mutating MCP-tools write an event.
- On the task page the full timeline is visible (without needing to run `revision_list` separately).
- The daily digest gives the operator an understanding "what happened at all" in < 10 seconds of reading.

## Related

- 04 (run-id) — `run_id` — a mandatory field, the main correlation key.
- 05 (issue docs) — task-doc changes in the stream.
- 06 (checkout) — checkout/release as events.
- 07 (routines) — routine-fires in the stream.
- 12 (approvals) — approval lifecycle in the stream.

## Notes (cod-doc context)

- **Revisions ≠ events.** The separation in the RFC is correct and important: revisions — content snapshots, events — facts with actor/run/scope/payload. Do not try to unify into one table.
- **SQLite → one transaction; Postgres → outbox.** The decision on the approach must be fixed at the start, because subsequent switching requires migrating existing events. Given we are on sqlite for now — one transaction is simple and works; outbox — overkill.
- **Retention from the start.** Without archiving the table accumulates millions of rows in a year (drift every 30 min = 17k events/year from one routine alone). Archive > 6 months to a JSONL-file or a separate table with the same index.
- **Correlation with git.** Some mutations in cod-doc lead to a commit in the project repo (e.g. `master.updated`). A `commit_sha` field in `payload` for such events closes the audit chain "event → commit in the project".
- **Phase 1 — tasks and docs.** Do not try to record everything at once. First tasks (status changes, checkout, blockers), then docs (including task-docs), then master/links/runs/routines/approvals. Each phase = a separate PR.

## Open questions

- **Q1.** Outbox or one transaction — which approach do we fix at the start? If sqlite — clearly one transaction?
- **Q2.** Archive (> 6 months) — a separate `activity_events_archive` table, a JSONL-file on disk, or just an `archived=true` boolean without moving?
- **Q3.** Include read-events (views of docs/tasks via MCP)? Useful for metrics "where the agent looks", but noisy.
- **Q4.** `payload` structure — typed per-kind (Pydantic models per kind) or generic JSON with runtime validation?
- **Q5.** Correlation with git-commits — add `commit_sha` to payload for scale-relevant events or a separate `activity_event_git_link` table?
- **Q6.** What to do if event recording failed but the mutation went through (error in outbox-flow)? Silent, retry, or escalate?
- **Q7.** Do we need "summary" events — aggregates (e.g. a `daily_summary` row with counts), or is this computed on-demand?
