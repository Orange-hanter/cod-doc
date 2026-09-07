---
name: task-flow
description: |
  Работа с задачами cod-doc: атомарный checkout → работа → complete с
  commit-sha, создание задач и секций плана, grooming. Порядок инструментов:
  MCP-тулы → CLI → service-слой. Триггеры: задача, task, checkout, complete,
  закрой задачу, возьми задачу, plan ready, секция плана, plan section,
  очередь задач, blocked_by.
---

# Task flow — статус задачи живёт в БД

Источник истины — `<project>/.cod-doc/state.db`, не markdown. Закрытие задачи
правкой .md — не закрытие, а расхождение: проекция скажет «done», очередь и
`blocked_by` останутся прежними.

Слаг проекта нужен почти в каждом вызове. Определи один раз:
`cod-doc project list`, либо
`sqlite3 -readonly .cod-doc/state.db "select slug, root_path from project"`.
Мутирующий ad-hoc SQL по этой БД — запрещён: мимо него не пишутся ревизии и
activity events.

## Цикл

1. **Очередь** — `plan_ready(project, plan_scope)` или `task_next_ready`.
   Зависимости уже учтены, бери верх списка. CLI:
   `cod-doc plan ready <scope> -p <slug> --json`.
2. **Захват — атомарный `task_checkout(project, task_id, agent="claude-<тема>")`.**
   Не голый status-переход: `todo → in_progress` через `task_update_status`
   падает по протоколу. Идемпотентен для того же `agent`; чужой лок даёт
   конфликт, а не перетирание.
3. **Работа.** Промежуточный прогресс — `task_log_progress`, не комментарий в
   markdown. Гейт проекта — до закрытия, не после.
4. **Коммит** — conventional + ID задачи: `feat(scope): TASK-ID — суть`.
5. **Закрытие** — `task_complete(project, task_id, commit_sha=..., author=...)`.
   Валидирует `blocked_by`, снимает лок, пишет revision + activity event.
   Не закончил — `task_release`, а не тихо брошенный лок.

## Что где лежит

| Нужда | Инструмент |
|---|---|
| checkout | только MCP `task_checkout` (в CLI нет) |
| секция плана | MCP `plan_section_create(project, plan_scope, letter, title)` |
| grooming (description / acceptance / priority) | `task_update` — есть и в MCP, и в CLI (`cod-doc task update`) |
| смена title | по дизайну нет: `cancel` с причиной + новая задача |
| поиск дубля перед созданием | `task_find_duplicate` |
| зависшие задачи | `task_stale`, `task_list_blocked` |

## Статусы

Семь канонических bucket'ов: `todo`, `in_progress`, `blocked`, `review`,
`done`, `cancelled`, `deferred`; legacy-алиасы `pending` ≡ `todo`,
`in-progress` ≡ `in_progress`. Единственный источник истины по переходам —
`services/task_status_machine.py::ALLOWED_TRANSITIONS` в самом cod-doc; не
изобретай переходы по памяти, спроси `capabilities` / `tool_describe`.

## Service-слой (fallback, когда MCP недоступен)

Мутации — только через сервисы, они сами пишут ревизии и события. Скрипт
клади во временный каталог сессии, не в репозиторий:

```python
from pathlib import Path
from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url, transactional
from cod_doc.services import checkout_service, task_service

sf = make_session_factory(make_engine(resolve_db_url(Path("<project-root>"))))
with transactional(sf) as s:
    checkout_service.checkout(s, task_id="ABC-002", agent="claude-x")   # kwarg именно agent
    task_service.complete(s, task_id="ABC-002", author="claude-x", commit_sha="abc1234")
```

Создание: `task_service.create(s, project_id=..., plan_id=..., section_id=...,
title=..., type=TaskType..., priority=Priority..., author=..., id_prefix="ABC",
description=..., acceptance=..., blocked_by=[...])` — id выдаётся как
`{prefix}-NNN`.

## Перед закрытием

- Acceptance criterion выполнен буквально; «по смыслу» — это не выполнен.
- Гейт проекта зелёный.
- Закрыта последняя задача секции плана → audit-отчёт в `docs/system/audit/`,
  если проект держит эту конвенцию.
