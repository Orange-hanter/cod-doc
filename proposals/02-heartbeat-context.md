# 02 — Heartbeat-context endpoint

> Категория: 🎯 Прямое · Риск: низкий · Зависимости: —

## Контекст: как у paperclip

`GET /api/issues/:issueId/heartbeat-context` — **компактный** срез:
- issue state (только нужные поля)
- summary родителя/целей (не полные тела)
- cursor по комментариям (`after_comment_id`) для инкрементального чтения
- pending interactions / approvals в явном виде

Скилл прямо предписывает: «Prefer `heartbeat-context` first. Use `GET /comments` only when incremental isn't enough.»

Цель — отдать агенту ровно то, что нужно для решения «что я делаю в этом heartbeat», без полной выгрузки графа.

## Текущее состояние cod-doc

- Чтобы понять, что делать со следующей задачей, агент сейчас:
  1. Читает `MASTER.md` (через `get_master`) — целиком.
  2. Читает `next_pending_task` или `task_get`.
  3. Часто следом — `read_context` для связанных доков.
- Это 3-4 MCP-вызова, каждый раз тянущий куда больше, чем нужно для одной итерации.
- В [cod_doc/mcp/tools/task_tools.py](cod_doc/mcp/tools/task_tools.py) уже есть `task_get`, но он отдаёт «полную» задачу — без срезов и cursor'ов.

## Предложение

Добавить MCP-tool `task_heartbeat_context(task_id, since_revision_id?)`, который возвращает:

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

Ключевые свойства:
- **Срезы, не тела.** Никаких полных markdown-файлов, только заголовки/sha/status.
- **Cursor для инкрементального чтения.** `since_revision_id` — агент вызывает повторно, получает только дельту.
- **Подсказка скиллов.** Поле `active_skills_hint` — продукт триггер-матчера из [01](01-skills-layer.md).

## План внедрения

1. **Реализовать tool.** В [cod_doc/mcp/tools/task_tools.py](cod_doc/mcp/tools/task_tools.py) — функция `task_heartbeat_context`. Внутри — переиспользует существующие `task_get` + новый `_revisions_since(revision_id)` поверх `revision_list`.
2. **Прописать в `tool_defs.py`.** Зарегистрировать как первый-класс MCP-tool.
3. **Обновить orchestrator-loop.** В [cod_doc/agent/orchestrator.py](cod_doc/agent/orchestrator.py) — если есть текущая задача, вызывать `task_heartbeat_context` ДО `get_master`. `get_master` тогда нужен только при «холодном» старте (нет конкретной задачи).
4. **Документировать в скилле `orchestrator`** (см. [01](01-skills-layer.md)) — «всегда heartbeat-context первым, get_master — fallback для cold start».

## Риски

- **Дублирование.** Поля частично пересекаются с `task_get` + `read_context`. Решение: heartbeat-context — это **композиция**, не новый источник истины. Не кэшируем — пересобираем at request time.
- **Bigger payload, чем `task_get`.** Не страшно: один консолидированный вызов вместо 3-4 разрозненных.

## Метрики успеха

- Среднее число MCP-вызовов на «начало iteration» ≤ 1 (было 3-4).
- Размер контекста на cold-start iteration сокращён минимум вдвое vs полный `get_master + task_get + read_context`.

## Связанные

- 01 (skills) — поле `active_skills_hint` опирается на матчер.
- 03 (wake-payload) — wake-payload **инжектит результат** этого endpoint'а в первое сообщение, агенту даже не нужно его явно вызывать.
- 04 (run-id) — `since_revision_id` в комбинации с run-id даёт инкрементальный взгляд «что изменилось с моего прошлого run'а».

## Замечания (контекст cod-doc)

- **Уже есть `get_agent_context`.** В MCP-каталоге есть тул `mcp__cod-doc__get_agent_context` — нужно явно решить судьбу: расширяем его до heartbeat-семантики, или вводим `task_heartbeat_context` рядом, а старый помечаем deprecated. Вариант сосуществования — худший: split-brain в скилле «когда что вызывать».
- **MASTER.md потяжелел.** После COD-078 (tree, filters) и COD-079 (markdown tables, link backfill) `get_master` отдаёт заметно больше — экономия от перехода на heartbeat-context растёт.
- **Композиция, не источник истины.** Внутренне переиспользуем `task_get` + `revision_list` + summary docs. Не кэшируем, собираем at-request — это ОК, потому что MCP-вызов сам по себе быстрый.
- **Story-context.** Если задача привязана к story, в payload нужно включать критерии story (выжимкой) — иначе агент полезет в `story_get` отдельно и экономия пропадёт.

## Открытые вопросы

- **Q1.** Поглощаем `get_agent_context` или сосуществуем? Если поглощаем — миграционный путь и срок deprecation.
- **Q2.** Что в payload при `cold_start` (без `task_id`) — пустой объект, выжимка MASTER.md, или `next_pending_task` + heartbeat по нему?
- **Q3.** ETag/If-None-Match для cursor-чтений — если `since_revision_id` не сдвинулся, возвращать `304`-аналог или всё равно payload?
- **Q4.** Лимит размера payload (4KB? 8KB?) и поведение при overflow — truncate с маркером, или ошибка с указанием «дёргайте детали отдельно»?
- **Q5.** Как `linked_docs_summary` определяет, что значит «summary» — первый параграф, секция перед TOC, кастомное поле `summary` в frontmatter?
