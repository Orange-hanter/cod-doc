---
name: task-standard
description: |
  Стандарт постановки и оформления задач. Title-формат, обязательные и
  рекомендуемые поля, когда добавлять acceptance, как пользоваться
  blocked_by / story_id / affects_files, семантика priority, status-flow,
  когда дробить задачу, anti-patterns.
  Триггеры: task, task_create, add_task, update_task, поставить, задача,
  задачу, бэклог, decompose, breakdown, новая задача, постановка, plan task.
---

# Skill — Task standard

## Когда подгружается

Задачи, в которых LLM / пользователь **создаёт**, **разбивает** или
**ставит** задачу: `task_create`, `task_update_status`, `plan_create`,
ручная постановка через web / MCP. Триггер-keywords: `task`,
`task_create`, `add_task`, `update_task`, `acceptance`, `blocked_by`,
`story_id`, `affects_files`, `задача`, `задачу`, `бэклог`,
`decompose`, `breakdown`, `новая задача`, `постановка`, `plan task`.

## Принцип

Задача — узел графа исполнения. Она ДОЛЖНА быть атомарной (один
исполнитель, один merge), верифицируемой (есть DoD) и узнаваемой (имя
описывает действие, а не предметную область).

## 1. Title

- **Глагол первым**, императив: «Implement X», «Refactor Y»,
  «Fix Z», «Audit module Auth», «Migrate from A to B».
- **≤ 80 символов**, без точки в конце, без emoji.
- **Без vague-слов** («улучшить ui», «поправить», «доделать», «sort
  out»). Если непонятно ЧТО сделать — задача ещё не готова, верни в
  backlog с TODO в description.

## 2. Обязательные поля при создании

| Поле | Зачем |
|------|-------|
| `title` | См. §1 |
| `type` | `feature` / `bug` / `refactor` / `test` / `docs` / `chore` |
| `priority` | См. §4 — фильтрация и сортировка ready-batch |
| `plan_id` | Сирота без плана = invisible в kanban-board |
| `section_id` | Группировка внутри плана |

Запрещено создавать задачу без `plan_id`. Если плана нет — сначала
`plan_create`, потом `task_create`.

## 3. Рекомендуемые (почти обязательные) поля

| Поле | Когда обязательно |
|------|---------------------|
| `description` | Всегда для `feature` / `refactor`. Описывает ПОЧЕМУ + контекст; не пересказывает title. |
| `acceptance` | Для `priority >= medium`. Definition of Done — формат checklist'а. Без AC задача не закрывается. |
| `affects_files` | Если scope ограничен ≤ 5 файлами. Используется аудитом drift. |
| `blocked_by` | Task_id'ы, которые ДОЛЖНЫ завершиться раньше. Не «было бы хорошо», а технически невозможно начать без них. |
| `story_id` | Если задача реализует часть user-story — обязательно. Связывает execution с requirements. |

## 4. Priority semantics

- `critical` — инцидент / блокер релиза. Берёшь СЕГОДНЯ, всё остальное
  откладывается.
- `high` — попадает в текущий sprint / следующий ready-batch.
- `medium` — default. Берётся в порядке готовности.
- `low` — nice-to-have. Готов отложить на квартал. Не блокирует ни одну
  user-story.

Не более **20%** задач плана должны иметь `critical` / `high` — иначе
приоритеты обесцениваются.

## 5. Status flow

Каноническая 7-state taxonomy (proposal 08, единый источник —
`cod_doc/services/task_status_machine.py::ALLOWED_TRANSITIONS`):

```
backlog ─→ todo ─→ in_progress ─→ in_review ─→ done
                       │              │
                       ↓              ↓
                    blocked        cancelled
                       │
                       └──→ todo (после устранения blocker)
```

- `backlog` — в плане, но не приоритезирована.
- `todo` — ready to pick up.
- `in_progress` — взята в работу. Должны быть коммиты ≤ 24h.
  **Переход `todo → in_progress` идёт ТОЛЬКО через `task_checkout`**
  (proposal 06, PCA-200) — `task_update_status` его отклонит.
- `in_review` — код / док готов, ждёт review. AC не помечен ✓ до review.
- `blocked` — **ВСЕГДА** заполняй `blocked_reason`. Без причины — невозможно разблокировать.
- `done` — выполнено, AC checklist все ✓, drift-check (см. skill `module-audit`) пройден для модуля при необходимости.
- `cancelled` — отменено. Зачем — комментарий через `task_log_progress`.

### Legacy-алиасы

В старых задачах / `tasks.yaml` встретится 3-state набор. Они валидны
наравне с каноническими — `task_status_machine.normalise` коллапсирует их
в соответствующий bucket до проверки перехода:

| Legacy | Canonical |
|--------|-----------|
| `pending` | `todo` |
| `in-progress` (с дефисом) | `in_progress` (с underscore) |
| `done` | `done` |

При записи новых задач используй канонические имена; legacy остаётся
только для backward-compat.

### Правка уже созданной задачи (grooming)

Переформулировать скоуп и переоценить приоритет — штатная операция, а не
повод лезть в БД (ADO-067):

| Поверхность | Как |
|---|---|
| MCP | `task_update(project, task_id, description=…, acceptance=…, priority=…)` — любое подмножество полей; ответ содержит `updated_fields` |
| CLI | `cod-doc task update TASK_ID -p SLUG --description … --acceptance … --priority …` |

Каждое изменённое поле пишет ревизию и activity event
(`task.description_updated` / `task.acceptance_updated` /
`task.priority_changed`), поэтому `--reason` стоит заполнять.

`title` тул не меняет: смена имени — это смена идентичности задачи, заводи
новую и отменяй старую. Статус живёт отдельно (`task_update_status` /
`task_checkout`).

## 6. Когда дробить задачу

Дроби на subtasks (через `blocked_by`), если выполняется ХОТЯ БЫ ОДНО:

- ≥ 1 рабочий день одного исполнителя
- > 3 файлов меняется одним merge
- AC раскладывается на «выполнить A» + «выполнить B» + «выполнить C»
- Разные типы (`feature` + `test` + `docs`) — каждое отдельной задачей

Subtask наследует `plan_id` / `section_id` родителя.

## 7. Anti-patterns

- ❌ «Улучшить документацию» — нет scope, нет DoD.
- ❌ in_progress без коммитов > 24h — назначь blocker или верни в todo.
- ❌ done без проверки AC — pencil-whip.
- ❌ blocked без `blocked_reason` — невозможно разблокировать.
- ❌ Задача без `plan_id` — orphan. Сначала создай / выбери план.
- ❌ Все задачи priority=high — обесценивает фильтр.
- ❌ AC формата «должно работать» — не верифицируемо. Пиши как checklist:
      «✓ POST /api/x возвращает 201 с {id, created_at}; ✓ DB-row создан
      в таблице Y; ✓ тест в tests/api/test_x.py покрывает happy + 400-error».

## Связанное

- Полная спецификация формата: `docs/system/standards/task-plan.md`.
- Skill `plan-to-tasks` — как раскладывать execution-plan на узлы.
- Skill `module-audit` — что проверять при закрытии модуля.
- Skill `validation` — write-path валидация структуры задач.
