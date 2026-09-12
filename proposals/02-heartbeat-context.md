# 02 — Heartbeat-context endpoint

> Category: 🎯 Direct · Risk: low · Dependencies: —

## Context: like paperclip

`GET /api/issues/:issueId/heartbeat-context` — a **compact** slice:
- issue state (only the needed fields)
- summary of parent/goals (not full bodies)
- cursor over comments (`after_comment_id`) for incremental reading
- pending interactions / approvals in explicit form

The skill directly prescribes: "Prefer `heartbeat-context` first. Use `GET /comments` only when incremental isn't enough."

The goal — give the agent exactly what is needed to decide "what am I doing in this heartbeat", without a full graph dump.

## Current state of cod-doc

- To understand what to do with the next task, the agent currently:
  1. Reads `MASTER.md` (via `get_master`) — in full.
  2. Reads `next_pending_task` or `task_get`.
  3. Often followed by `read_context` for related docs.
- This is 3-4 MCP calls, each pulling much more than needed for one iteration.
- In [cod_doc/mcp/tools/task_tools.py](cod_doc/mcp/tools/task_tools.py) there is already `task_get`, but it returns the "full" task — without slices and cursors.

## Proposal

Add MCP-tool `task_heartbeat_context(task_id, since_revision_id?)`, which returns:

```json
{
  "task": {
    "id": "COD-082",
    "status": "todo",
    "title": "...",
    "kind": "feature",
    "blocked_by": ["COD-079"],
    "linked_docs": ["doc:arch_architecture_md"]
  },
  "ancestry": {
    "story": {"id": "ST-014", "title": "...", "status": "in_progress"},
    "project": {"id": "cod-doc", "phase": "..."}
  },
  "linked_docs_summary": [
    {"ref": "doc:arch_architecture_md", "section": "Modules", "sha": "a641cd2bf5e7", "status": "VERIFIED"}
  ],
  "recent_changes": {
    "since_revision_id": "rev-2025-...",
    "doc_revisions": [{"doc": "doc:specs_modules_md", "rev": "...", "summary": "added §4"}],
    "task_status_changes": [],
    "comments": []
  },
  "active_skills_hint": ["validation", "drift-handling"],
  "next_action_guess": "checkout + read linked specs/modules.md"
}
```

Key properties:
- **Slices, not bodies.** No full markdown files, only titles/sha/status.
- **Cursor for incremental reading.** `since_revision_id` — the agent calls repeatedly, gets only the delta.
- **Skill hint.** The `active_skills_hint` field — a product of the trigger matcher from [01](01-skills-layer.md).

## Implementation plan

1. **Implement tool.** In [cod_doc/mcp/tools/task_tools.py](cod_doc/mcp/tools/task_tools.py) — function `task_heartbeat_context`. Inside — reuse existing `task_get` + new `_revisions_since(revision_id)` over `revision_list`.
2. **Register in `tool_defs.py`.** Register as a first-class MCP-tool.
3. **Update orchestrator-loop.** In [cod_doc/agent/orchestrator.py](cod_doc/agent/orchestrator.py) — if there is a current task, call `task_heartbeat_context` BEFORE `get_master`. `get_master` is then needed only at "cold" start (no specific task).
4. **Document in the `orchestrator` skill** (see [01](01-skills-layer.md)) — "always heartbeat-context first, get_master — fallback for cold start".

## Risks

- **Duplication.** Fields partially overlap with `task_get` + `read_context`. Solution: heartbeat-context is a **composition**, not a new source of truth. We don't cache — we reassemble at request time.
- **Bigger payload than `task_get`.** Not scary: one consolidated call instead of 3-4 disparate ones.

## Success metrics

- Average number of MCP calls per "start of iteration" ≤ 1 (was 3-4).
- Context size on cold-start iteration reduced at least twofold vs full `get_master + task_get + read_context`.

## Related

- 01 (skills) — the `active_skills_hint` field relies on the matcher.
- 03 (wake-payload) — wake-payload **injects the result** of this endpoint into the first message, the agent doesn't even need to call it explicitly.
- 04 (run-id) — `since_revision_id` in combination with run-id gives an incremental view "what changed since my previous run".

## Notes (cod-doc context)

- **`get_agent_context` already exists.** In the MCP catalog there is a tool `mcp__cod-doc__get_agent_context` — we need to explicitly decide its fate: extend it to heartbeat semantics, or introduce `task_heartbeat_context` alongside and mark the old one deprecated. The coexistence variant is the worst: split-brain in the skill "when to call what".
- **MASTER.md got heavier.** After COD-078 (tree, filters) and COD-079 (markdown tables, link backfill) `get_master` returns noticeably more — the savings from switching to heartbeat-context grow.
- **Composition, not source of truth.** Internally we reuse `task_get` + `revision_list` + summary docs. We don't cache, we assemble at-request — this is OK because the MCP call itself is fast.
- **Story-context.** If the task is attached to a story, the payload needs to include the story criteria (as a summary) — otherwise the agent will dig into `story_get` separately and the savings disappear.

## Open questions

- **Q1.** Do we absorb `get_agent_context` or coexist? If we absorb — the migration path and deprecation deadline.
- **Q2.** What is in the payload at `cold_start` (without `task_id`) — an empty object, a summary of MASTER.md, or `next_pending_task` + heartbeat over it?
- **Q3.** ETag/If-None-Match for cursor reads — if `since_revision_id` hasn't moved, return a `304`-analog or the payload anyway?
- **Q4.** Payload size limit (4KB? 8KB?) and behavior on overflow — truncate with a marker, or an error pointing "fetch details separately"?
- **Q5.** How does `linked_docs_summary` decide what "summary" means — first paragraph, section before TOC, custom `summary` field in frontmatter?
