---
name: task-flow
description: |
  Работа с задачами cod-doc: атомарный checkout → работа → complete с
  commit-sha → release, создание задач и секций плана, grooming. Порядок
  инструментов: MCP-тулы → CLI. Триггеры: задача, task, checkout, complete,
  закрой задачу, возьми задачу, plan ready, секция плана, plan section,
  очередь задач, blocked_by.
---

# Task flow — статус задачи живёт в БД

Источник истины — `<project>/.cod-doc/state.db`, не markdown. Закрытие задачи
правкой .md — не закрытие, а расхождение: проекция скажет «done», очередь и
`blocked_by` останутся прежними.

## Профиль

Роль выводит `agent_capabilities()` по профилю MCP-сервера (RFC 27, AFT-004):

| Профиль | Где | `role` | `forbidden` | Скилл |
|---|---|---|---|---|
| `standard` / `full` / `minimal` | демон `:8801` — `standard` | `coder` | пуст | применяется: checkout → работа → complete законны |
| `agent` | демон `:8802` | `doc-curator` | `agent_pick`, `task_checkout`, `task_complete` | **не применяется**: вход в работу — `curator_next` |

Сомневаешься — вызови `agent_capabilities` и следуй `role`.

Слаг проекта нужен почти в каждом вызове. Определи один раз:
`cod-doc project list --json` (`[{slug, root_path, db_url}]`).
Мутирующий ad-hoc SQL по этой БД — запрещён: мимо него не пишутся ревизии и
activity events.

## Цикл

1. **Очередь** — `plan_ready(project, plan_scope)` или `task_next_ready`.
   Зависимости уже учтены, бери верх списка. CLI:
   `cod-doc plan ready <scope> -p <slug> --json`.
2. **Захват — атомарный `task_checkout(project, task_id, agent="claude-<тема>")`**
   (CLI: `cod-doc task checkout <TASK_ID> -p <slug> --agent <имя>`).
   Не голый status-переход: `todo → in_progress` через `task_update_status`
   падает по протоколу. Идемпотентен для того же `agent`; чужой лок даёт
   конфликт, а не перетирание.
3. **Работа.** Промежуточный прогресс — `task_log_progress`, не комментарий в
   markdown. Гейт проекта — до закрытия, не после.
4. **Коммит** — conventional + ID задачи: `feat(scope): TASK-ID — суть`.
5. **Закрытие** — `task_complete(project, task_id, commit_sha=..., author=...)`
   (CLI: `cod-doc task complete <TASK_ID> -p <slug> --commit <sha>`).
   Валидирует `blocked_by`, из `todo` сам делает checkout-ногу
   `todo → in_progress` (ADO-038), пишет revision + activity event.
   **Замок `complete` не снимает**: `checked_out_by` остаётся прежним.
6. **Снятие замка** — `task_release(project, task_id, agent=...)`
   (CLI: `cod-doc task release <TASK_ID> -p <slug> --agent <имя>`). Без него
   задача числится захваченной: `checked_out_by` не пустеет, и всё, что читает
   замок (фильтр `locked` в `task_next_ready`, предупреждение
   `task_in_progress` в `task_add_dep`), видит её занятой. Не закончил работу —
   тот же `task_release`, а не тихо брошенный лок.

## Что где лежит

| Нужда | MCP | CLI |
|---|---|---|
| checkout | `task_checkout` | `cod-doc task checkout <TASK_ID> -p <slug> --agent <имя>` |
| release | `task_release` | `cod-doc task release <TASK_ID> -p <slug> --agent <имя>` (`--force` — чужой лок) |
| закрытие | `task_complete` | `cod-doc task complete <TASK_ID> -p <slug> --commit <sha>` |
| секция плана | `plan_section_create(project, plan_scope, letter, title)` | `cod-doc plan section create PLAN_SCOPE LETTER TITLE -p <slug>` |
| grooming (description / acceptance / priority) | `task_update` | `cod-doc task update` |
| смена title | по дизайну нет: `cancel` с причиной + новая задача | — |
| поиск дубля перед созданием | `task_find_duplicate` | — |
| зависшие задачи | `task_stale`, `task_list_blocked` | — |

MCP недоступен → CLI (`cod-doc task …`); ad-hoc скрипты по `state.db`
запрещены.

## Статусы

Семь канонических bucket'ов: `backlog`, `todo`, `in_progress`, `in_review`,
`blocked`, `done`, `cancelled`; legacy-алиасы `pending` ≡ `todo`,
`in-progress` ≡ `in_progress`. Единственный источник истины по переходам —
`services/task_status_machine.py::ALLOWED_TRANSITIONS` в самом cod-doc; не
изобретай переходы по памяти, спроси `capabilities` / `tool_describe`.

## Перед закрытием

- Acceptance criterion выполнен буквально; «по смыслу» — это не выполнен.
- Гейт проекта зелёный.
- Закрыта последняя задача секции плана → audit-отчёт в `docs/system/audit/`,
  если проект держит эту конвенцию.
