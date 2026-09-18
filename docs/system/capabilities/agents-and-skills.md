---
type: capability
scope: agents-and-skills
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-09-15
related_docs:
  - ../audit/2026-04-19-initial-audit.md
---

# Capability — Agents & Skills Catalog

> Каталог ролей агентов проекта; формализует, что появляется в `revision.author=agent:<role>`.
> Аналог Restate `.github/agents/` и `.github/skills/`, но первоклассный объект COD-DOC.

## 0. As implemented (2026-09-15)

MCP `agent_pick` / `agent_get` / `agent_report` / `agent_complete` /
`agent_release` / `agent_capabilities` (профиль `agent` — ровно 6 тулов).
Пустой ready-set → `{"task": null, "reason": "no_ready_tasks"}`. Скиллы —
`cod_doc/skills/<name>/SKILL.md`, подбор `skill_matcher`. CLI `cod-doc agent`
не дублирует каждый `agent_*` тул.

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
| `docs-reviewer` | **дефолт (RFC 25)** — корпус, drift, ссылки, поиск, Snowball |
| `task-steward` | постановка планов; не исполнение feature-очереди оркестратором |
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

## 5. Прогоны оркестратора и их шаги

Контур появился целиком только в ADO-115: до неё `agent_run` существовал, а
шаги прогона нигде не сохранялись. Ни одна капабилити эту подсистему не
описывала — `web-frontend.md` упоминал её двумя строками таблицы роутов.
Здесь её дом, потому что после ADO-115 у неё появилось состояние на диске
вне БД.

### Три места, где живёт прогон

| Где | Что | Живёт |
|---|---|---|
| `agent_run` (БД) | строка прогона: `run_id`, статус, причина пробуждения, время | пока жива БД |
| `activity_event` (БД) | шаги с `run_id` и `scope_kind='run'`, текст **обрезан** до 2000 символов, не больше 500 шагов на прогон | пока жива БД |
| `<project>/.cod-doc/runs/<run_id>.jsonl` | **полное** тело каждого шага, одна строка на шаг, без потолка | пока не удалили |

`run_id` — ULID, он же имя sidecar-файла. Он приходит в том числе из URL
(`/p/{slug}/run/{run_id}/step/{index}`), поэтому проверяется
`run_context.validate_run_id` **до любого касания пути** — и на записи, и на
чтении: проверка на одной стороне создавала бы ложное чувство безопасности
на другой.

### Чем sidecar НЕ является

Рядом в `.cod-doc/` уже лежат sidecar-файлы (`nav_cache.json`,
`task_audit.json`, `section_summaries.json`), и отличие надо назвать вслух:
**те — регенерируемый кэш, а этот — единственная копия деталей.** Отсюда:

- он **не бэкап**: удалили — детали не восстановить ниоткуда;
- он **не источник истины**: контракт — строка в `activity_event`. Если
  файл не записался, событие всё равно пишется, но с `full: false`;
  указатель не должен обещать того, чего нет;
- он **не переживёт** `rm -rf .cod-doc`, и не должен: это телеметрия,
  потеря допустима.

Но именно поэтому **чистить его молча нельзя**. Ротации сейчас нет: файл
ограничен числом шагов одного прогона, а каталог растёт с числом прогонов.
Кто и когда его чистит — открытый вопрос, и до ответа на него удаление
остаётся ручной операцией.

### Что НЕ изменилось

ADR-012 в силе. `run_id` остаётся телеметрией встроенного оркестратора:
мутации через MCP/CLI/REST по-прежнему идут с `run_id IS NULL`, и лента
прогона их не показывает. ADO-115 пишет ровно туда, где ADR это и
зарезервировал, а не возвращает снятое правило «все мутации несут run_id».

Мёртвая телеметрия, найденная попутно и НЕ починенная здесь: `llm_calls`,
`llm_tokens_in`, `llm_tokens_out` в `agent_run` не пишет никто (в живой БД
все нули), но `run_service` и `run_tools` их исправно отдают.

## 6. Что не делаем

- Не запускаем агентов из COD-DOC — они работают извне (Claude Code, Copilot, локальные скрипты).
- Не храним промпты агентов — это обязанность среды (Restate хранит в `.github/agents/*.md`; мы можем держать ссылки `prompt_doc_key` на документ типа `guide`, но не парсим).
