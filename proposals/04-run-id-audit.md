# 04 — Run-id audit trail

> Category: 🎯 Direct · Risk: low · Dependencies: 03 (or standalone)

## Context: like paperclip

All mutating API calls from agents carry the header:
```
X-Paperclip-Run-Id: <run-uuid>
```

The skill explicitly requires:
> *"You MUST include `-H 'X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID'` on ALL API requests that modify issues. This links your actions to the current heartbeat run for traceability."*

What this gives:
- **Audit:** "what the agent did on a specific run" — one SQL query.
- **Rollback:** on an erroneous run you can roll back **all** related mutations.
- **Cost/token binding:** run_id is linked to cost-events.
- **Debug:** on a strange change you can see in which context it happened.

## Current state of cod-doc

- There is [revision_list / revision_get / revision_revert](cod_doc/mcp/tools/revision_tools.py) — but **per doc**, not per operation.
- If on one run the agent did `doc_create` + `update_master_hashes` + `task_update_status`, there is no way to know these mutations are related.
- You cannot answer "roll back everything the agent did in run X".

## Proposal

1. **Generate `run_id`** on each orchestrator launch (`uuid7` for sortability).
2. **Pass it through `ToolExecutor`** in [cod_doc/agent/tools.py](cod_doc/agent/tools.py) — all calls of mutating MCP-tools get this run_id implicitly.
3. **Extend the revision schema.** In the revision table (or the equivalent in [cod_doc/mcp/tools/_db.py](cod_doc/mcp/tools/_db.py)) add a `run_id` column. Similarly — for task status changes, MASTER.md changes.
4. **New `Run` entity:**
   ```sql
   CREATE TABLE agent_runs (
     run_id TEXT PRIMARY KEY,
     started_at TIMESTAMP,
     finished_at TIMESTAMP,
     wake_reason TEXT,            -- from WakeContext (see 03)
     triggering_task_id TEXT,
     triggering_doc_ref TEXT,
     llm_calls INT,
     llm_tokens_in INT,
     llm_tokens_out INT,
     status TEXT,                 -- running | done | failed | cancelled
     summary TEXT                 -- final self_check
   );
   ```
5. **MCP-tools:**
   - `run_list(since?, limit?)` — recent runs.
   - `run_get(run_id)` — all mutations of this run: doc revisions, task status changes, master updates.
   - `run_revert(run_id, dry_run=true)` — rollback: calls `revision_revert` for each linked change.
6. **Web UI:** a "Runs" page with a timeline; click on a run → diff of everything that changed.

## List of mutating operations that require run_id

| Operation                            | Source                                  |
| ----------------------------------- | --------------------------------------- |
| `doc_create`, `doc_body` (write)    | [doc_tools.py](cod_doc/mcp/tools/doc_tools.py) |
| `update_master_hashes`              | legacy `legacy_master_tools.py` (removed in `c310503`; historical mutation source) |
| `task_update_status`, `task_complete` | [task_tools.py](cod_doc/mcp/tools/task_tools.py) |
| `task_create`, `task_set_blocker`   | [task_tools.py](cod_doc/mcp/tools/task_tools.py) |
| `link_sync`                         | [link_tools.py](cod_doc/mcp/tools/link_tools.py) |
| `story_*` mutations                 | [story_tools.py](cod_doc/mcp/tools/story_tools.py) |

## Implementation plan

1. **DB schema + migration.** `run_id NULL` column, `agent_runs` table.
2. **Context propagation.** In [orchestrator.py](cod_doc/agent/orchestrator.py) generate run_id; through `ToolExecutor` — into each MCP call as an implicit argument (in payload or contextvar).
3. **Recording.** Each mutating function writes run_id together with the revision.
4. **MCP-tools `run_*`.**
5. **UI.** Minimum — table + detail page.
6. **Rollback.** `run_revert` — in Phase 2; start with read-only audit.

## Risks

- **External mutations without run_id.** If a human edits MASTER.md by hand — run_id will be NULL. OK — UI shows this explicitly ("human edit, no run").
- **Partial success.** A run may crash mid-way; run stays in `failed`, its mutations are visible and can be rolled back separately.
- **Rollback with conflicts.** If subsequent runs touched the same artifacts, `run_revert` must show conflicts, not "apply silently". Same as [revision_revert].

## Success metrics

- 100% of mutating MCP-tools write run_id.
- The query "show everything the agent did in run X" is answerable via UI or CLI.
- In Phase 2: dry-run revert is possible, conflicts are explicitly listed.

## Related

- 03 (wake-payload) — `WakeContext` gives birth to run_id; payload is written to `agent_runs.wake_reason`.
- 09 (activity log) — run-id is used as a correlation key in the unified timeline.
- 12 (approvals) — approval stores the run_id of the requesting operation.

## Notes (cod-doc context)

- **NULL for legacy and human-edits.** Past revisions will have `run_id IS NULL`. UI must explicitly show this as "pre-runs era" or "human edit, no run", not leave an empty cell — otherwise the operator gets the impression of a bug.
- **Take together with [09](09-activity-log.md).** If the activity log is done next — `run_id` is needed as a correlation-key from the moment the events table is created, otherwise backfill will be needed.
- **uuid7 — is it needed?** Sortability gives native ORDER BY without a separate timestamp column, but adds a dep (or a manual implementation). Alternative — uuid4 + explicit `started_at` index.
- **Phase-1 read-only.** Defer `run_revert`. First observability ("what the agent did"), then — reversibility. Otherwise the risk of clashing with the current `revision_revert`.
- **Cost/tokens.** If the LLM adapter ([10](10-adapter-pattern.md)) is not done yet, the `llm_tokens_*` fields are filled by heuristics of the current OpenAI client. This is OK, but do not block on 10.

## Open questions

- **Q1.** uuid7 vs uuid4 + timestamp column — which variant is accepted?
- **Q2.** Run hierarchy — if an agent launches a sub-agent or a routine fires inside a run, is it a flat list or parent_run_id?
- **Q3.** TTL for `agent_runs` — store forever (important for audit), or aggregate older than N months?
- **Q4.** `run_revert` with conflicts — strict abort with a list of conflicting revisions, partial-revert with a marker, or interactive mode in UI?
- **Q5.** What to do if a run crashed and left mutations in a half-applied state (e.g. doc_create without update_master_hashes)? Auto-rollback by run_id or manual fix?
- **Q6.** Visibility in CLI — do we need `cod-doc run list` and `cod-doc run get`, or is UI + MCP enough?
