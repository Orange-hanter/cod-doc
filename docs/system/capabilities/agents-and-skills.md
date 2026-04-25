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

> Каталог ролей агентов проекта; формализует, что появляется в `revision.author=agent:<role>`.
> Аналог Restate `.github/agents/` и `.github/skills/`, но первоклассный объект COD-DOC.

## 1. Сущности

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
  - doc.patch_section          # task-steward не пишет код-доки
  - context.get                # ему достаточно plan.* запросов
auto_approve: true             # revision-ы пишутся сразу, без proposal-flow
```

В БД — таблица `agent_definition(project_id, agent_id, body, last_updated)`.

### 1.2 `SkillDefinition`

Skill — короткий рецепт для повторяющейся операции (Restate `.github/skills/docs-sync`). В нашей модели — markdown-документ `type=skill` без отдельной таблицы.

```yaml
type: skill
skill_id: docs-sync
trigger: "code/commands changed; docs need to be synchronized"
agents: [docs-reviewer, task-steward]
steps:
  - "Run cod-doc audit --stale"
  - "For each stale doc, propose patch via doc.propose_edit"
```

## 2. Базовый каталог (поставляется по умолчанию)

| agent_id | scope |
|----------|-------|
| `task-steward` | task-planning, audit |
| `docs-reviewer` | doc evolution, links |
| `migrator` | one-time imports |
| `link-verifier` | system-job для link verify |
| `release-manager` | export-changelog, milestone tagging |

Пользователь может расширять / переопределять через `cod-doc agent new`.

## 3. Применение allowed/denied

При вызове MCP-тула:

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

Audit-log обязательно фиксирует deny.

## 4. Связь с roadmap

- `roadmap/cod-doc-task-plan.md` COD-032 (MCP tools) обязан учитывать allowed-list.
- `roadmap/audit-followups-task-plan.md` DOC-HI-2 — дописать каталог по умолчанию + миграцию.

## 5. Что не делаем

- Не запускаем агентов из COD-DOC — они работают извне (Claude Code, Copilot, локальные скрипты).
- Не храним промпты агентов — это обязанность среды (Restate хранит в `.github/agents/*.md`; мы можем держать ссылки `prompt_doc_key` на документ типа `guide`, но не парсим).
