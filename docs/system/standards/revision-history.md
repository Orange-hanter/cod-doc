---
type: standard
scope: revision-history
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../DATA_MODEL.md
  - ../capabilities/doc-evolution.md
---

# Revision History Standard

> Единая модель истории изменений для всех сущностей COD-DOC.
> Заменяет ручные changelog-таблицы в markdown-документах (как в Restate `Docs/MASTER_DOCUMENTATION.md` или модульных спеках).

## 1. Принцип

- Каждый write-path вызов пишет **одну** `revision`-запись (append-only).
- Revision содержит diff, автора, timestamp, причину, опционально commit SHA.
- Прошлая версия body восстанавливается проигрыванием diffs в обратном порядке.
- Таблица `revision` — имутабельна; компактирование (squash старых revision) — отдельным сервисным job-ом с сохранением snapshot каждые N изменений.

## 2. Схема записи

```yaml
revision_id: ULID                # 26 chars, '01HQX5Z9F0K8RNG6CB7VHQK4XX'
parent_revision_id: ULID | null  # предыдущая revision той же сущности
entity_kind: document | task | plan | story | link | module | proposal
entity_id: <row_id>
author: agent:<name> | human:<login> | mcp:<client> | system:<service>
at: ISO-8601                     # должен соответствовать timestamp в revision_id
diff: unified-diff | json-patch
reason: freeform string (recommended)
commit_sha: optional
```

**Формат `revision_id`** — ULID (Crockford-base32, 26 символов, 128 бит). Первые 48 бит — timestamp ms; остальные 80 — random. Сортируется лексикографически по времени; безопасен при offline-сессиях и нескольких репликах. Подробнее — `revision` в [DATA_MODEL.md §3.5](../DATA_MODEL.md).

## 3. Когда пишется revision

| Событие | entity_kind | Комментарий |
|---------|-------------|-------------|
| `DocService.create` | document | `diff` = `+ entire body` |
| `DocService.apply_patch` | document | canonical unified diff |
| `DocService.rename` | document | diff по метаданным |
| `TaskService.create` / `update` / `complete` | task | JSON-patch по изменённым полям |
| `PlanService.recalc` | plan | revision только если `Progress Overview` действительно изменился |
| `StoryService.*` | story | JSON-patch |
| `LinkService.resolve_bulk` | link | агрегированная revision на все изменённые ссылки |
| `DocService.rename` + cascade | document × N | одна revision на каждый затронутый документ (для навигации) |

## 4. Author

Поле `author` обязательно. Форматы:

- `human:<login>` — человеческая сессия CLI/TUI.
- `agent:<role>` — LLM-агент (`agent:task-steward`, `agent:doc-reviewer`).
- `mcp:<client>` — внешний MCP-клиент (`mcp:claude-code`, `mcp:copilot`).
- `system:<service>` — фоновые job-ы (`system:link-verifier`).

Подмена author запрещена на уровне API: MCP-клиент не может писать `human:...`.

## 5. Diff-форматы

- Текстовые поля документов (`body`, `section.body`) — **unified diff**.
- Структурные сущности (task, story) — **JSON-patch** (RFC 6902).
- Переименования документов — JSON-patch по frontmatter + пустой diff для body.

## 6. Причина (`reason`)

Рекомендована, но не обязательна. Используется:

- В `cod-doc log <doc-key>` для человекочитаемой истории.
- В MCP `revision.list` — агент читает «почему так сделано», не поднимая код.
- В `export-changelog` для публичного CHANGELOG.md.

Формат: одна-две строки на человеческом языке.

Для task-ов причина = `status:pending→in-progress` когда reason не указан явно.

## 7. Связь с Git

Если COD-DOC вызван в контексте git-коммита (через pre-commit hook или CLI с `--commit`), `commit_sha` проставляется автоматически.

Обратно: `cod-doc log --since <sha>` умеет вытащить все revision, привязанные к коммитам с этого SHA.

## 8. Rollback

`cod-doc revision revert <revision_id>`:

1. Читает diff обратно.
2. Применяет inverse через соответствующий сервис.
3. Пишет **новую** revision с `reason: "revert of <revision_id>"` (никогда не удаляет старую).

Запрещено:

- Прямое удаление записи `revision`.
- Rollback без соответствующей сервисной операции (нельзя писать «сырой» контент напрямую).

## 9. Экспорт в public CHANGELOG

Команда `cod-doc export-changelog --since YYYY-MM-DD`:

- Группирует revision по дню.
- Фильтрует только `entity_kind ∈ {document, task, plan}` с `status=active` либо `status=done`.
- Группирует по модулю.
- Генерирует markdown-отчёт, пригодный для публикации (аналог Restate `Docs/MASTER_DOCUMENTATION.md §Change Log`).

## 10. Формат внутридокументной истории

Для документов, где важен «визуальный» changelog прямо в теле (напр. master doc), поддерживается секция `## Changelog`. Её **тело генерируется** из `revision` таблицы при export — редактировать руками нельзя (COD-DOC перезапишет).

```markdown
## Changelog

| Дата | Событие |
|------|---------|
| 2026-04-19 | Первая версия. |
| 2026-04-20 | Добавлена секция "Data Model". |
```

## 11. Ретенция

- По умолчанию — бессрочно.
- Compact-job `cod-doc revision compact --older-than 365d` создаёт snapshot каждые N дней и удаляет «промежуточные» diffs. Исходная операция сохраняется как финальный snapshot.
- Revision для статусов задач (low-value) могут сжиматься до `task-status-series` за день.

## 12. Просмотр

| Команда | Что показывает |
|---------|----------------|
| `cod-doc log <doc-key>` | История документа с diff |
| `cod-doc log task <AUTH-025>` | История задачи |
| `cod-doc log --plan <plan> --since 7d` | Всё, что менялось в плане за неделю |
| `cod-doc revision show <id>` | Детали одной revision |
| MCP `revision.list(target)` | Агент-эквивалент |
