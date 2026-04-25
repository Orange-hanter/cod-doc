---
type: capability
scope: task-creation
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../standards/task-plan.md
  - plan-management.md
---

# Capability — Task Creation

> Стандартизированное и автоматическое создание задач без ручного заполнения YAML и ручной проверки формата.

## 1. Что нужно автоматизировать

На сегодня (Restate baseline) автор задачи обязан:

1. Знать структуру frontmatter (`id`, `title`, `section`, `status`, `depends_on`, `type`, `priority`, `affected_files`).
2. Выбрать правильный ID из свободного диапазона секции.
3. Подобрать verb-pattern для заголовка.
4. Не нарушить enum-ы (`type`, `status`, `priority`).
5. Руками пересчитать `tasks_total` / `tasks_done` в section frontmatter.
6. Обновить Progress Overview и Next Batch в parent-plan.
7. Пересобрать dependency graph.

Каждый шаг — точка отказа. COD-DOC делает все семь автоматически.

## 2. Основные поверхности

| Поверхность | Команда |
|-------------|---------|
| CLI | `cod-doc task new --plan <plan> [--section <letter>] --title "<text>" --type <type> [--priority <p>] [--depends <ID,...>] [--affected <path,...>]` |
| TUI | `cod-doc wizard task new` — пошаговая форма |
| MCP | `task.create({...})` |
| REST | `POST /api/v1/tasks` |

## 3. Контракт `task.create`

Вход:

```json
{
  "plan": "M1-auth-module",
  "section": "C",                       // letter ИЛИ null — сервис подберёт
  "title": "Implement: account deactivation flow",
  "type": "feature",
  "priority": "high",                   // default: "medium"
  "depends_on": ["AUTH-020", "AUTH-021"],
  "affected_files": [
    "restate-api/src/auth/services/auth.service.ts",
    "restate-api/src/auth/__tests__/auth-lifecycle.spec.ts"
  ],
  "description": "...",
  "acceptance": "..."
}
```

Выход:

```json
{
  "task_id": "AUTH-025",
  "section": "C-AccountLifecycle",
  "status": "pending",
  "created_revision": "r_01hq..."
}
```

## 4. Автоматика сервиса `TaskService.create`

1. **Валидация title** → verb-pattern регэкс (`standards/task-plan.md §7`). Мисматч — возвращается список допустимых шаблонов, задача не создаётся.
2. **Выбор section** если не задана — по `type`:
   - `test` → первая секция `Test Coverage`, если есть.
   - `feature` → секция с незавершёнными implement-задачами.
   - иначе — последняя открытая секция.
3. **Генерация id**:
   - PREFIX = `plan.prefix` (кэшируется).
   - NUMBER = `max(existing in section range) + 1`, clamp в пределы decade.
   - Если decade заполнен — следующий свободный decade.
4. **Проверка depends_on**:
   - Все task_id существуют.
   - Нет цикла (recursive CTE + inserted edge).
   - Cross-plan допустимо.
5. **Запись в БД** в одной транзакции:
   - `task` row.
   - `dependency` rows.
   - `affected_file` rows.
   - `revision(entity_kind=task, ...)`.
6. **Пост-действия** (тот же транзакционный scope):
   - Пересчёт `section_totals` (view, автоматически).
   - `PlanService.recalc(plan_id)` для обновления Progress Overview/Next Batch body.
   - `LinkService.reindex(task.section.doc_id)` для новых исходящих ссылок.
7. **Проекция**: если в конфиге проекта включён auto-export — markdown-файлы секций регенерируются.

## 5. Запрещённые сценарии

- Создание таска без `plan` — запрет на уровне схемы (`NOT NULL`).
- Ручная правка markdown без последующего import — не запрещена, но при следующем export перезапишется из БД.
- Попытка создать таск с `status: done` сразу — error (нужно пройти `pending → in-progress → done`).
- Циклы в `depends_on` — error при insert, с указанием узлов цикла.

## 6. Batch-создание

Полезно при импорте user stories:

```bash
cod-doc task bulk --plan M1-auth-module --from-yaml tasks.yaml
```

где `tasks.yaml` — массив объектов того же формата. Операция транзакционна (всё или ничего).

## 7. Интеграция с user stories

Если в запросе указан `story_id`, создаётся `story_link(to_kind=task, relation=implemented_by)`. Позволяет потом получить «все задачи, реализующие US-014» без парсинга markdown.

## 8. Интеграция с логикой ready

Сразу после создания задачи:

- Если у задачи пустой `depends_on` — она попадает в `ready_tasks`.
- `PlanService.recalc_next_batch()` пересчитывает top-7 unblocked.

## 9. Примеры

### 9.1 Через CLI

```bash
cod-doc task new \
  --plan M1-auth-module \
  --title "Implement: account deactivation flow" \
  --type feature \
  --priority high \
  --depends AUTH-020,AUTH-021 \
  --affected restate-api/src/auth/services/auth.service.ts
# → AUTH-025 created in C-AccountLifecycle
```

### 9.2 Через MCP (агент)

```
→ task.create({
    plan: "M1-auth-module",
    title: "Test: registration field validation",
    type: "test",
    priority: "medium"
  })
← { task_id: "AUTH-050", section: "A-Test-Coverage", status: "pending" }
```

Никакой ручной работы с markdown, никаких конфликтов с `tasks_total`.

## 10. Миграция от Restate

При импорте существующих планов (см. [migration/from-restate.md](../migration/from-restate.md)) сервис `TaskService.import_bulk` принимает parsed markdown и прогоняет те же валидации, что и `task.create`. Нарушения формата Restate (встречающиеся, напр. `section: A MR Blockers` с пробелами) фиксятся автоматически + пишется revision `reason: "import-normalize"`.
