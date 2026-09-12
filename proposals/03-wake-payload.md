# 03 — Wake-payload pattern

> Category: 🎯 Direct · Risk: low · Dependencies: 02

## Context: like paperclip

When the agent is launched via heartbeat, the environment gets injected variables:
- `PAPERCLIP_TASK_ID`, `PAPERCLIP_WAKE_REASON`, `PAPERCLIP_WAKE_COMMENT_ID`, `PAPERCLIP_APPROVAL_ID`, `PAPERCLIP_APPROVAL_STATUS`, `PAPERCLIP_LINKED_ISSUE_IDS`.
- The most important: `PAPERCLIP_WAKE_PAYLOAD_JSON` — a ready compact JSON with issue summary + new comments + the wake reason.

The skill directly requires:
> *"Use it first. For comment wakes, treat that batch as the highest-priority new context in the heartbeat: in your first task update or response, acknowledge the latest comment and say how it changes your next action before broad repo exploration."*

Effect: the agent **does not** "first read everything, then think". It immediately sees the reason, the context, and can act.

There is also a **scoped-wake fast path:** if the wake points to a specific task, the agent skips the "identity / inbox / pick work" steps and goes straight to checkout.

## Current state of cod-doc

- In [cod_doc/agent/orchestrator.py](cod_doc/agent/orchestrator.py) launching the agent looks like "get the project, read the queue". There is no distinction "cold start vs resumption by a specific trigger".
- The system prompt dictates: "1. Read MASTER.md (L0)" — the agent reflexively does this always, even when raised for a specific task.
- `run_agent_once` in MCP accepts context, but does not use it as "scoped wake".

## Proposal

Introduce the notion of **WakeContext** in [cod_doc/agent/](cod_doc/agent/), which is assembled **before** the first LLM call and injected into the system as the first message "WAKE PAYLOAD: ...".

```python
@dataclass
class WakeContext:
    reason: WakeReason  # cold_start | task_assigned | doc_drift | approval_resolved | manual
    task_id: str | None
    triggering_doc_ref: str | None
    triggering_revision_id: str | None
    payload: dict  # result of task_heartbeat_context (see 02) if there is a task_id
    skills_to_preload: list[str]  # from the trigger matcher
```

**Assembly:**
- The entry point in `run_agent_once` / daemon accepts `WakeContext`.
- If `task_id` is set → immediately call `task_heartbeat_context` and put it in `payload`.
- If `triggering_doc_ref` (e.g. drift check found STALE) → put a slice over the doc + list of dependent tasks.

**Injection into LLM:**
- The first message in the conversation — a structured system message:
  ```
  WAKE PAYLOAD
  reason: doc_drift
  triggering_doc: doc:specs_modules_md (sha mismatch)
  ...

  Acknowledge this in your first action.
  ```
- The orchestrator skill (see [01](01-skills-layer.md)) obliges the agent to acknowledge the wake-context in the first self_check.

**Scoped fast path:**
- If `reason in {task_assigned, approval_resolved, doc_drift}` and `payload` contains enough data — the skill instruction says "do not call `get_master`, do not scan the queue, act immediately".

## Implementation plan

1. **`WakeContext` model** + `WakeReason` enum.
2. **Assembler** `build_wake_context(task_id?, doc_ref?, ...) -> WakeContext` — reuses [02](02-heartbeat-context.md).
3. **Adapt `Orchestrator.run`** — accepts `WakeContext`, injects it into the conversation as the first user-message block (or on top of system).
4. **Update `run_agent_once` MCP-tool** — accepts explicit trigger parameters.
5. **Update daemon** ([cod_doc/services/](cod_doc/services/)) — on wake from drift/cron/UI assembles the correct `WakeContext`.
6. **Skill rule** in `orchestrator/SKILL.md`: "if there is a WAKE PAYLOAD — act on it, do not read MASTER.md".

## Risks

- **Stale payload.** If the daemon assembled the payload a minute ago and the state changed — the agent has a stale picture. Solution: payload includes `assembled_at` and `since_revision_id`; the agent on suspicion calls `task_heartbeat_context(since_revision_id=...)` for the delta.
- **Temptation to put "everything" into the payload.** Solution: a hard size limit (e.g. 4KB), everything above — the agent fetches itself.

## Success metrics

- For wakes with an explicit triggering source: 0 calls of `get_master` in the first round-trip.
- Time to first productive action (write/update) reduced vs cold-start.

## Related

- 02 (heartbeat-context) — payload is mostly the result of heartbeat-context.
- 04 (run-id) — wake-context assigns the `run_id`, which then tags all mutations.
- 07 (routines) — a routine on trigger creates a wake with `reason=routine_<name>` and the needed payload.

## Notes (cod-doc context)

- **Daemon already triggers on drift.** After COD-070..077 in [cod_doc/services/](cod_doc/services/) there is wake on drift/UI events, but without a structured wake-context — each source patches its own set of arguments. A unified `build_wake_context()` eliminates the chaos.
- **Reflexive reading of MASTER.md.** The system prompt now directly requires "1. Read MASTER.md (L0)" — this is correct for cold-start, but expensive for a wake on a specific task. The skill instruction must explicitly distinguish the two modes.
- **Race between payload and real state.** Between payload assembly and agent start, external mutations are possible. `assembled_at` + `since_revision_id` give the agent a way to check freshness with one cheap call, but this must be explicitly written in the skill, otherwise the agent will trust the stale-payload.
- **Multiple reasons.** If within 5 seconds drift + approval_resolved + comment happened — do we assemble one wake with an array of reasons or N separate ones? A real scenario for single-user is rare, but the semantics must be fixed.

## Open questions

- **Q1.** WakeContext transport — env vars (like paperclip), CLI argv, stdin-JSON, or a separate MCP call with `wake_id`? Affects how the daemon launches the agent.
- **Q2.** What to do if the payload was assembled but the agent did not start (crash, kill)? Record a `run.aborted` event or is the wake-context simply lost?
- **Q3.** Logging the wake itself in the activity log ([09](09-activity-log.md)) — even if the run did not start? `wake.scheduled` / `wake.fired` / `wake.aborted`?
- **Q4.** Debounce — if 3 identical wakes arrive within 1 second (watcher jitter), what to do?
- **Q5.** Can the user manually "reassemble" the payload and restart the run (for debugging) without losing history?
