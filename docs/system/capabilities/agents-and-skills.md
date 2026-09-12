---
type: capability
scope: agents-and-skills
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../audit/2026-04-19-initial-audit.md
---

# Capability — Agents & Skills Catalog

> A catalog of project agent roles; formalizes what appears in `revision.author=agent:<role>`.
> The analogue of the Restate `.github/agents/` and `.github/skills/`, but a first-class object of COD-DOC.

## 1. Entities

### 1.1 `AgentDefinition`

```yaml
type: agent-definition
agent_id: task-steward
title: "Task Steward"
scope: "Maintain task plans and section files"
allowed_tools:
  - task.create
  - task.update_status
  - plan.audit
  - plan.recalc
  - revision.list
denied_tools:
  - doc.patch_section          # task-steward does not write code-docs
  - context.get                # plan.* queries are enough for it
auto_approve: true             # revisions are written immediately, without a proposal-flow
```

In the DB — table `agent_definition(project_id, agent_id, body, last_updated)`.

### 1.2 `SkillDefinition`

A skill — a short recipe for a recurring operation (Restate `.github/skills/docs-sync`). In our model — a markdown-document `type=skill` without a separate table.

```yaml
type: skill
skill_id: docs-sync
trigger: "code/commands changed; docs need to be synchronized"
agents: [docs-reviewer, task-steward]
steps:
  - "Run cod-doc audit --stale"
  - "For each stale doc, propose patch via doc.propose_edit"
```

## 2. The base catalog (shipped by default)

| agent_id | scope |
|----------|-------|
| `task-steward` | task-planning, audit |
| `docs-reviewer` | doc evolution, links |
| `migrator` | one-time imports |
| `link-verifier` | system-job for link verify |
| `release-manager` | export-changelog, milestone tagging |

The user can extend / override via `cod-doc agent new`.

## 3. Applying allowed/denied

On an MCP-tool call:

```python
def authorize(actor: str, tool: str) -> Decision:
    if actor.startswith("agent:"):
        agent_id = actor.split(":")[1]
        defn = AgentDefinition.get(agent_id)
        if defn.denied_tools and tool in defn.denied_tools:
            return Deny("denied by agent definition")
        if defn.allowed_tools and tool not in defn.allowed_tools:
            return Deny("not in allowed list")
    return Allow()
```

The audit-log always records a deny.

## 4. Relationship with the roadmap

- `roadmap/cod-doc-task-plan.md` COD-032 (MCP tools) must respect the allowed-list.
- `roadmap/audit-followups-task-plan.md` DOC-HI-2 — write the default catalog + migration.

## 5. What we do not do

- We do not run agents from COD-DOC — they work externally (Claude Code, Copilot, local scripts).
- We do not store agent prompts — that is the responsibility of the environment (Restate stores them in `.github/agents/*.md`; we can keep `prompt_doc_key` references to a `guide`-type document, but do not parse them).
