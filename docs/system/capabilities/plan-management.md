---
type: capability
scope: plan-management
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-09-27
related_docs:
  - ../standards/task-plan.md
  - task-creation.md
  - user-stories-graph.md
---

# Capability — Plan Management

> Ведение execution-plan как живой сущности: пересчёт Progress Overview, Next Batch, Dependency Graph, completed-log — всё автоматически.

## 1. Чем занимается `PlanService`

- Создание планов; создание, правка, перестановка и удаление секций.
- Пересчёт секционных и плановых totals.
- Генерация/обновление Progress Overview, Next Batch, Dependency Graph.
- Ведение completed-tasks log.
- Статус плана (derived).
- Импорт / экспорт markdown-проекций плана.

## 2. Операции

`plan_service` — единственный write-путь планов и секций (RFC 26 §3.1,
ADO-201) и read-path progress / ready-set / audit / export / graph queries.
До RFC 26 `plan_create` и `plan_section_create` писали прямо из MCP через
`PlanRepository.add()` / `PlanSectionRepository.add()` — без ревизии,
activity event и `author`. Теперь каждая реальная мутация пишет revision и
activity event в транзакции самой мутации
(`plan_service/sections.py`); адрес ревизии секции —
`EntityKind.PLAN_SECTION`, у `plan_section` своя нумерация `row_id`.

`reason` обязателен в `update_section`, `move_section`, `delete_section` и
необязателен в `create_plan` / `create_section` — эти два тула уже зовут
живые агенты, обязательный параметр сломал бы их вызовы.

Позиции секций плана — плотный порядок `0..n-1`: `move_section` перенумеровывает
весь план, приём `position=-1` закрыт. Явный `slug` обязан пройти
`validate_section_slug` (`<LETTER>-<KebabSlug>`). Непустая секция удаляется
только с `reassign_to=<letter>`, иначе `SectionHasTasksError`: флага `force`
нет, потому что `task.section_id` — NOT NULL + CASCADE.

| Операция | Сервис | MCP | CLI |
|----------|--------|-----|-----|
| Создать план | `create_plan` | `plan_create` | `cod-doc plan create` |
| Добавить секцию | `create_section` | `plan_section_create` | `cod-doc plan section create` |
| Поправить секцию | `update_section` | `plan_section_update` | `cod-doc plan section update` |
| Переставить секцию | `move_section` | `plan_section_move` | `cod-doc plan section move` |
| Удалить секцию | `delete_section` | `plan_section_delete` | `cod-doc plan section rm` |
| Список планов | `list_plans_summary` | `plan_list` | `cod-doc plan list` |
| Список секций | `list_sections` (CLI); MCP считает task counts своим запросом | `plan_sections_list` | `cod-doc plan section list` |
| Пересчитать progress | `plan_service.recalc` ← MCP `plan_progress` / CLI `cod-doc plan progress`, `cod-doc plan show` |
| Ready-tasks | `plan_service.ready` ← MCP `plan_ready` / CLI `cod-doc plan ready` |
| Экспорт проекции | `plan_service.export` ← MCP `plan_export` / CLI `cod-doc plan export` |
| Freeze | `plan_service.freeze_projection` ← MCP `plan_freeze` / CLI `cod-doc plan freeze` |
| Аудит | `plan_service.audit` ← MCP `plan_audit` / CLI `cod-doc plan audit` |
| Граф | `forward_chain` / `reverse_chain` / `critical_path` — нет отдельной команды `plan graph` |

`PlanService.close` и `split_inline_to_section_files` **не существуют**.

Паритет поверхностей над write-функциями `plan_service` машинно проверяет
`tests/services/test_plan_mutation_surface_parity.py` (ADO-209);
`freeze_projection` вне спеки — пишет через `doc_service`. Что презентация
не пишет в ORM мимо сервисов, стережёт
`tests/services/test_presentation_no_orm_writes.py` (ADO-208).

## 3. Progress Overview — generated artifact

Таблица в parent-plan документе генерируется запросом к `plan_totals`. Ручная правка перезапишется при следующем export.

Формат совпадает с Restate §2.3:

```markdown
| Section | Total | Done | Cancelled | Remaining | Status |
|:--------|------:|-----:|----------:|----------:|:-------|
| A: Test Coverage | 5 | 5 | 0 | 0 | ✅ done |
| B: Profile       | 4 | 2 | 1 | 1 | 🔄 in-progress |
| **TOTAL**        | **9** | **7** | **1** | **1** | 🔄 in-progress |
```

Колонка `Cancelled` — ADO-078. `Remaining` перестал считать отменённые
задачи, и без неё строка «5 / 4 / 0» читается как арифметическая ошибка.
Печатается всегда, а не только когда отменённые есть: иначе каждая отмена
меняла бы форму проекции и давала дрейф на ровном месте.

Иконки `✅`, `🔄`, `❌`, `⏳` — из `section_totals.status`-деривата:

- `tasks_done == tasks_total` → ✅
- `tasks_done > 0` → 🔄
- `tasks_done == 0` & no open blocker → ❌
- есть blocker через `dependency` — ⏳ `(blocked by X)`.

## 4. Next Batch

Top-N из `ready_tasks`, упорядочено по `priority desc`, `created asc`.

Пример ответа `PlanService.ready("M1-auth-module", max_tasks=5)`:

```json
{
  "plan": "M1-auth-module",
  "readyCount": 7,
  "shownCount": 5,
  "truncated": true,
  "tasks": [
    {"id":"AUTH-025","title":"Implement: account deactivation flow","section":"C","priority":"high"},
    {"id":"AUTH-026","title":"Implement: token rotation hardening","section":"C","priority":"high"},
    ...
  ]
}
```

Поля `readyCount/shownCount/truncated` — наследие Restate `tools/task-plan-ecosystem.md §6.2.2` (там эта история уже отрефакторена в рабочий контракт, не ломаем).

## 5. Статус плана (derived)

Считается в `plan_service._derive_status(total, done, in_progress, cancelled)`
из view `plan_totals` / `section_totals`. Это **не** `TaskStatus`.

| Условие | `DerivedStatus` |
|---------|--------|
| `total == 0` | `empty` |
| `done + cancelled >= total` (то есть `remaining == 0`) | `done` |
| `in_progress > 0` или `done > 0` | `in-progress` |
| иначе (все задачи ещё не взяты) | `pending` |

ADO-078: второе условие раньше было `done == total`, и план с восемью
закрытыми и двумя отменёнными задачами из десяти висел в `in-progress`
навсегда — взять было нечего, а закрыться он не мог. Отменённые при этом
**не** прибавляются к `done`: в счётчиках остаётся 8, а число отменённых
видно полем `cancelled`. Сходится только вывод «работы не осталось».
Отдельного `DerivedStatus.CANCELLED` нет: план, все задачи которого
отменены, доходит до `done` с `done=0` и `cancelled=total` — состав виден по
числам.

Capability раньше утверждала «все задачи pending → pending» и не знала
`empty`. Код — источник истины.

## 6. Completed-tasks log

Колонка `plan.completed_log_id` есть в схеме. Автосоздание log-документа
при пороге «≥ 10 задач» **не реализовано**: ни `complete`, ни `plan_service`
её не заполняют. След закрытия задачи — `revision` + `activity_event`, не
generated completed-log.

## 7. Dependency Graph

Рёбра — таблица `dependency` (`kind='blocks'`) с мотивацией в `note`.
Write-путь рёбер — `task_service.add_dependency` / `remove_dependency`
(ADO-202): `note` обязателен, повтор с тем же `note` — no-op, с другим —
правка `note`, `adopt=True` легализует внесистемное ребро ревизией; ребро,
замыкающее цикл, отвергается `DependencyCycleError` с путём до записи.
MCP `task_add_dependency` / `task_remove_dependency`, CLI
`cod-doc task add-dep` / `remove-dep`.

Живые запросы:

- MCP/CLI `plan_critical_path` / `cod-doc plan critical-path`
- `plan_forward_chain` / `plan_reverse_chain`

Отдельной команды `cod-doc plan graph` нет, Mermaid/dot-экспорта нет.
Restate-правила «рисовать с ≥ 15 задач» и prefix module-id для cross-plan
рёбер в этом сервисе не живут.

## 8. Аудит плана

`plan_service.audit` → `PlanAuditReport`: циклы среди `blocks`, done-задачи
чьи blocking-зависимости ещё не done, длина critical path. CLI
`cod-doc plan audit` / MCP `plan_audit`.

Не проверяет: Progressive Overview vs `plan_totals`, сиротские секции,
`last_updated`, наличие completed-log. Флага `--strict` нет.

Постоянный надзор за графом — рутина `graph_health`
(`services/graph_health.py`, RFC 26 §5.3, ADO-205) в
`routine_service.CHECK_CATALOG`. Правила детерминированные: циклы (тот же
`_find_cycles`), немые рёбра (пустой `note` при незакрытой зависимой
задаче), мёртвые рёбра (блокер `done` дольше 30 дней, зависимая `todo`),
кросс-плановые рёбра, позиции секций вне `0..n-1`, слаги вне конвенции.
Находки — в `finding` с `source_ref="graph_health"`, `close_after_misses=1`;
видны в `curator_next`. Отдельной MCP/CLI-поверхности нет, и модуль нарочно
лежит вне пакета `plan_service`: иначе сканер паритета потребовал бы тулов
для `sync()`.

## 9. Поддержка inline ↔ split переходов

`cod-doc plan convert` и `PlanService.split_inline_to_section_files`
**не существуют**. Секции плана — строки в БД, не выбор формата markdown.

## 10. MCP-поверхность

Имена — `snake_case` (`plan_*`), не dotted `plan.list`. Профили
`standard` / `full`.

| Tool | Операция |
|------|----------|
| `plan_create` | Создать план, опционально с секциями |
| `plan_list` | Планы проекта: scope, статус, done/total |
| `plan_section_create` | Добавить секцию |
| `plan_section_update` | Заголовок / слаг / документ секции (`reason` обязателен) |
| `plan_section_move` | Переставить секцию: `before` / `after` / `position` |
| `plan_section_delete` | Удалить секцию; с задачами — только с `reassign_to` |
| `plan_sections_list` | Секции с task counts |
| `plan_progress` | `recalc`: total/done/cancelled/remaining + derived status; без scope — все планы |
| `plan_ready` | Ready-set, priority-ordered |
| `plan_audit` | Циклы + done-drift |
| `plan_export` | Markdown-проекции |
| `plan_freeze` | Snapshot проекции в Document |
| `plan_critical_path` | Длиннейшая цепочка зависимостей |
| `plan_forward_chain` | Prerequisites задачи |
| `plan_reverse_chain` | Dependents задачи |

Dotted `plan.list` / `plan.graph` / `plan.next_batch` **не зарегистрированы**.
CLI-зеркало: `cod-doc plan create|list|progress|show|ready|audit|export|freeze|critical-path|forward|reverse`
и `cod-doc plan section create|list|update|move|rm`.

## 11. UI (TUI/веб)

Web: страницы плана под `/p/{slug}/…` (`cod_doc/api/web/pages/plans.py`,
overview считает `ready_for_project` по всем планам проекта). TUI
`cod-doc dashboard` — legacy. Виджетов «Stale plans» / «Broken
dependencies» как отдельных поверхностей нет.

## 12. Работа с несколькими планами

Все планы проекта — строки в одной SQLite. Ready-set:

- по плану — MCP `plan_ready` / CLI `cod-doc plan ready`
- по проекту — MCP `task_next_ready` (опциональный `plan_scope`); web
  overview зовёт `plan_service.ready_for_project`

Тулов `task.ready` / `task.critical_path` / `task.dependency_chain` нет.
Critical path и цепочки — `plan_critical_path` / `plan_forward_chain` /
`plan_reverse_chain` и всегда привязаны к одному плану.
