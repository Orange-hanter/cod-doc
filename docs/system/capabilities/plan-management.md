---
type: capability
scope: plan-management
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-09-15
related_docs:
  - ../standards/task-plan.md
  - task-creation.md
  - user-stories-graph.md
---

# Capability — Plan Management

> Ведение execution-plan как живой сущности: пересчёт Progress Overview, Next Batch, Dependency Graph, completed-log — всё автоматически.

## 1. Чем занимается `PlanService`

- Создание и переименование планов.
- Пересчёт секционных и плановых totals.
- Генерация/обновление Progress Overview, Next Batch, Dependency Graph.
- Ведение completed-tasks log.
- Статус плана (derived).
- Импорт / экспорт markdown-проекций плана.

## 2. Операции

`PlanService` — **read-path**: progress, ready-set, audit, export, graph
queries. Create/section-create живут в MCP/CLI и пишут через репозиторий, не
через этот пакет.

| Операция | Где |
|----------|--------|
| Создать план | MCP `plan_create` (CLI create нет) |
| Добавить секцию | MCP `plan_section_create` (CLI section-create нет) |
| Список секций | MCP `plan_sections_list` |
| Пересчитать progress | `plan_service.recalc` ← MCP `plan_progress` / CLI `cod-doc plan show` |
| Ready-tasks | `plan_service.ready` ← MCP `plan_ready` / CLI `cod-doc plan ready` |
| Экспорт проекции | `plan_service.export` ← MCP `plan_export` / CLI `cod-doc plan export` |
| Freeze | `plan_service.freeze_projection` ← MCP `plan_freeze` / CLI `cod-doc plan freeze` |
| Аудит | `plan_service.audit` ← MCP `plan_audit` / CLI `cod-doc plan audit` |
| Граф | `forward_chain` / `reverse_chain` / `critical_path` — нет отдельной команды `plan graph` |

`PlanService.close` и `split_inline_to_section_files` **не существуют**.

## 3. Progress Overview — generated artifact

Таблица в parent-plan документе генерируется запросом к `plan_totals`. Ручная правка перезапишется при следующем export.

Формат совпадает с Restate §2.3:

```markdown
| Section | File | Total | Done | Remaining | Status |
|:--------|:-----|------:|-----:|----------:|:-------|
| A: Test Coverage | [section-a](tasks/section-a.md) | 5 | 5 | 0 | ✅ done |
| B: Profile       | [section-b](tasks/section-b.md) | 4 | 2 | 2 | 🔄 in-progress |
| **TOTAL**        |                                  | **9** | **7** | **2** | |
```

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

Считается в `plan_service._derive_status(total, done, in_progress)` из
view `plan_totals` / `section_totals`. Это **не** `TaskStatus`.

| Условие | `DerivedStatus` |
|---------|--------|
| `total == 0` | `empty` |
| `done == total` | `done` |
| `in_progress > 0` или `done > 0` | `in-progress` |
| иначе (все задачи ещё не взяты) | `pending` |

Capability раньше утверждала «все задачи pending → pending» и не знала
`empty`. Код — источник истины.

## 6. Completed-tasks log

Колонка `plan.completed_log_id` есть в схеме. Автосоздание log-документа
при пороге «≥ 10 задач» **не реализовано**: ни `complete`, ни `plan_service`
её не заполняют. След закрытия задачи — `revision` + `activity_event`, не
generated completed-log.

## 7. Dependency Graph

Рёбра — таблица `dependency` (`kind='blocks'`). Живые запросы:

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

## 9. Поддержка inline ↔ split переходов

`cod-doc plan convert` и `PlanService.split_inline_to_section_files`
**не существуют**. Секции плана — строки в БД, не выбор формата markdown.

## 10. MCP-поверхность

Имена — `snake_case` (`plan_*`), не dotted `plan.list`. Профили
`standard` / `full`.

| Tool | Операция |
|------|----------|
| `plan_create` | Создать план, опционально с секциями |
| `plan_section_create` | Добавить секцию |
| `plan_sections_list` | Секции с task counts |
| `plan_progress` | `recalc`: total/done/remaining + derived status |
| `plan_ready` | Ready-set, priority-ordered |
| `plan_audit` | Циклы + done-drift |
| `plan_export` | Markdown-проекции |
| `plan_freeze` | Snapshot проекции в Document |
| `plan_critical_path` | Длиннейшая цепочка зависимостей |
| `plan_forward_chain` | Prerequisites задачи |
| `plan_reverse_chain` | Dependents задачи |

`plan.list` / `plan.graph` / `plan.next_batch` **не зарегистрированы**.
CLI-зеркало: `cod-doc plan show|ready|audit|export|freeze|critical-path|forward|reverse`.

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
