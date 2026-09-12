# 01 — Skills layer: modular `SKILL.md`

> Category: 🎯 Direct borrowing · Risk: low · Dependencies: —

## Context: like paperclip

In [`skills/`](https://github.com/paperclipai/paperclip/tree/master/skills) each skill is a directory with:
- `SKILL.md` — markdown with YAML-frontmatter (`name`, `description` — description of load triggers).
- Optional `references/` folder — deep references, loaded on demand.

Examples:
- `paperclip` (main) — heartbeat protocol + links to `references/api-reference.md`, `references/workflows.md`, `references/routines.md`.
- `paperclip-converting-plans-to-tasks` — narrow skill "how to convert a plan into issues".
- `diagnose-why-work-stopped` — narrow diagnostics of stopped states.
- `para-memory-files` — separate memory pattern.

The main idea: **the system prompt stops being a monolith**. The LLM receives a base prompt + an index of skills, and a specific skill "activates" only when a task matches its triggers from `description`.

## Current state of cod-doc

- The entire system prompt is in [cod_doc/agent/prompts.py:3](cod_doc/agent/prompts.py#L3) as a single constant.
- Rules that currently live in the agent's memory (FM-002/003/004/005 validation, audit-cadence) do NOT get into the prompt; they live in `MEMORY.md`.
- Snowball Protocol is declared (L0/L1/L2 for documents), but **the agent itself** loads its instructions as a single block — this contradicts its own principle.
- Agent tools already return markdown — but there are no "trigger-activated" instructions.

## Proposal

Create a `cod_doc/skills/` catalog with the following structure:

```
cod_doc/skills/
  orchestrator/                # base, always loaded
    SKILL.md                   # minimal heartbeat protocol + index
    references/
      hybrid-refs.md           # link format 📁 | 🗃️ | 🔑
      self-check.md            # self_check block format
  validation/
    SKILL.md                   # FM-002..FM-005, when to escalate
    references/
      escalation-flow.md
  audit-cadence/
    SKILL.md                   # closing a phase → audit-report; new phase → kickoff
  drift-handling/
    SKILL.md                   # what to do on STALE/BROKEN
  plan-to-tasks/
    SKILL.md                   # how to split a plan into task nodes
  doc-style/
    SKILL.md                   # documentation style, hybrid links
```

Frontmatter of each skill:
```yaml
---
name: validation
description: >
  When to apply structural validation (raise) vs advisory audit (issues).
  Triggers: writing to MASTER.md, creating/updating a doc, changing hashes.
  FM-002/003 escalate as blockers; FM-004/005 — advisory comments.
---
```

**Loading:**
- At orchestrator startup, only `orchestrator/SKILL.md` + the list of names and `description` of the other skills are loaded.
- Before each LLM call — a simple matcher (regex over trigger words in the current task) mixes relevant `SKILL.md` into the system.
- `references/*.md` are loaded on demand only when the skill itself explicitly references ("see `references/X.md` for ...").

## Implementation plan

1. **Extract and split.** Cut [prompts.py](cod_doc/agent/prompts.py) into 3-4 base skills. SYSTEM_PROMPT stays in code, but becomes thin — it assembles orchestrator/SKILL.md + trigger skills.
2. **Move memory-rules.** FM-validation, audit-cadence from `MEMORY.md` into the corresponding skills (this is shared knowledge, not the user's personal memory).
3. **Trigger matcher.** A simple function `select_skills(task: Task) -> list[Path]` in [cod_doc/agent/](cod_doc/agent/). Can start with keyword-matching over `task.title + task.description + task.kind`.
4. **MCP-tool `skill_list` / `skill_get`.** So the agent itself can request: "give me `audit-cadence`".
5. **Tests.** Each skill — a test case of a task on which it should/should not activate.

## Risks

- **Drift between skills.** Solution: one base "orchestrator" — the single source of truth for common rules; the rest complement, not contradict.
- **Over-fragmentation.** Granularity no finer than 1 skill = 1 conversation. Recommendation: up to 8 skills at start.
- **Cost of matching.** Cheap regex matcher — fine. ML classifier is not needed.

## Success metrics

- Base system prompt ≤ 30 lines (currently ~60).
- Average size of "active" skill set per iteration ≤ 2 skills.
- No rules that live only in `MEMORY.md` and have no reflection in a skill (for shared knowledge).

## Related

- 03 (wake-payload) — wake-payload decides which skills to preload for a specific task.
- 11 (AGENTS.md) — AGENTS.md references skills as canonical instructions for contributors and agents.

## Notes (cod-doc context)

- **Moving from personal memory.** The `validation pattern` and `audit cadence` rules currently live in the user's `MEMORY.md` — this is shared knowledge, not personal preferences. The skill layer makes them part of the repo and available to any future session/contributor.
- **Already formalized — writing is nearly ready.** FM-002/003/004/005 are split into structural (raise) and advisory (issues), audit-cadence has a clear trigger "closing a phase → audit-report; new phase → kickoff-brief". The descriptions of the `validation` and `audit-cadence` skills are essentially a copy-paste of the already accepted rule.
- **Contradiction with Snowball Protocol.** The agent's own system prompt violates the principle it preaches for documents: it loads as a single block. The skill layer eliminates the split-brain — the same L0/L1/L2 semantics for agent context.
- **Cheap matcher.** Keyword-matcher over `task.title + task.description + task.kind` — fine; ML/LLM classifier is overkill.

## Open questions

- **Q1.** How to separate `MEMORY.md` (personal) and `cod_doc/skills/` (shared)? Is a formal criterion "this is knowledge about the user vs about the project" needed?
- **Q2.** Skill versioning — do we need revision-history and `skill_revert` by analogy with docs, or is git history sufficient?
- **Q3.** Trigger conflict — if 3+ skills activate on the same task, is there a priority, or do we mix all of them?
- **Q4.** Who audits drift between skills and the actual code behavior (FM-validation in a skill vs implementation in `cod_doc/core/`)? A routine from [07](07-routines.md)?
- **Q5.** Compatibility with the built-in `Skill` tool of Claude Code — if a contributor works via CC and edits a skill, does their cod-doc agent see it as the canonical source?
