# 05 — Issue documents с ревизиями (pinned per-task)

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: 04

## Контекст: как у paperclip

Каждая issue может иметь именованные **документы**, прикреплённые к ней:
```
PUT /api/issues/:id/documents/plan
{
  "title": "Plan",
  "format": "markdown",
  "body": "...",
  "baseRevisionId": "rev-abc"   // оптимистичный лок
}
```

Канонические ключи: `plan` (план), и далее по необходимости `design`, `verification`, `acceptance` и т.п. Скилл планирования даже специально требует:
> *"If you're asked to make a plan, create or update the issue document with key `plan`. Do not append plans into the issue description anymore."*

Каждый документ имеет независимые ревизии и адресуется через deep-link `/{prefix}/issues/{id}#document-plan`.

## Текущее состояние cod-doc

- Есть **глобальные** доки (`doc_create`, `doc_body`, `doc_get`) с ревизиями — но они **не привязаны** к задачам.
- Есть **stories** с критериями (`story_add_criterion`, `story_link`) — это ближе всего, но другая семантика (приёмочные критерии истории, не любой структурный документ).
- План для задачи сейчас живёт обычно прямо в `task.description` или как отдельный markdown-файл, который надо вручную линковать.

## Предложение

Ввести понятие **task-bound document** с фиксированным `key`:

```
TaskDocument:
  task_id: str
  key: str           # 'plan' | 'design' | 'verification' | 'acceptance' | <custom>
  title: str
  format: 'markdown'
  body: str
  base_revision_id: str | None    # для optimistic locking
  current_revision_id: str
```

Один документ на пару (task_id, key); каждое обновление создаёт новую ревизию, логгируется с `run_id` (см. [04](04-run-id-audit.md)).

**MCP-тулы:**
- `task_doc_get(task_id, key)`
- `task_doc_put(task_id, key, body, base_revision_id?)`
- `task_doc_list(task_id)` — все доки задачи
- `task_doc_revisions(task_id, key)` — история
- `task_doc_revert(task_id, key, revision_id)`

**Канонические ключи** (рекомендация, не enforcement):

| Ключ           | Назначение                                              | Когда обязателен                |
| -------------- | ------------------------------------------------------- | ------------------------------- |
| `plan`         | План работы                                             | задачи `kind=feature` сложности > S |
| `design`       | Архитектурные решения, схемы                            | задачи затрагивающие arch/      |
| `verification` | Как проверить, что сделано                              | любая задача со статусом done   |
| `acceptance`   | Приёмочные критерии (если не покрыты story)             | для standalone-задач без story  |

## Отличие от существующих сущностей

| Сущность              | Скоуп                | Назначение                    |
| --------------------- | -------------------- | ----------------------------- |
| Глобальный `doc:*`    | проект               | спецификация / архитектура    |
| `Story` + `criterion` | пользовательская история | бизнес-приёмка             |
| `TaskDocument` (нов.) | задача               | рабочий контекст исполнителя  |

Не пересекаются: TaskDocument — это «черновик мышления для именно этой задачи».

## План внедрения

1. **Схема БД + миграция.** Новая таблица `task_documents` + `task_document_revisions`.
2. **MCP-тулы** в [cod_doc/mcp/tools/task_tools.py](cod_doc/mcp/tools/task_tools.py) (или отдельный `task_doc_tools.py`).
3. **Скилл `plan-to-tasks`** (из [01](01-skills-layer.md)) — обновить: «план задачи кладётся в `task_doc_put(task, 'plan', ...)`, не в task.description».
4. **UI:** на странице задачи — табы по ключам доков; markdown-редактор с показом diff между ревизиями.
5. **Heartbeat-context** (из [02](02-heartbeat-context.md)) — добавить срез `task_documents: [{key, current_revision_id, summary}]`.

## Риски

- **Двойная семантика с `story`.** Решение: чёткое правило в скилле — `acceptance` идёт в story-criterion, если есть привязка к story; иначе в task-doc `acceptance`.
- **Optimistic-lock конфликты.** При параллельных правках — explicit error с показом конфликта (как у `link_sync`).
- **Захламление.** Без enforcement список ключей может разрастись. Решение: warn-проверка `plan_audit` — флагит непривычные ключи.

## Метрики успеха

- Планы перестают жить в `task.description` для задач сложности > S.
- На каждую `done`-задачу feature-уровня есть `verification`-документ.
- Ревизии `plan`-доков позволяют отследить эволюцию подхода в спорных задачах.

## Связанные

- 04 (run-id) — каждая ревизия task-doc'а тегается run_id.
- 12 (approvals) — `approval` может ссылаться на конкретную ревизию `plan` («апрувлю plan@rev-abc»).
- 09 (activity log) — изменения task-doc'ов в едином таймлайне.

## Замечания (контекст cod-doc)

- **Story+criterion уже есть.** Не пересекается семантически: story-criterion — про бизнес-приёмку, task-doc — про рабочий контекст исполнителя. Разграничение в RFC корректное, но enforcement (в скилле/audit) обязателен, иначе ключи `acceptance` будут дублироваться между сущностями.
- **Линки на глобальные docs.** Task-docs реально будут ссылаться на `doc:arch_*`, `doc:specs_*` и т.п. — нужно убедиться, что текущие `link_verify` / `link_sync` (уже работающие) автоматически распространяются на task-docs, иначе появятся «слепые» битые ссылки.
- **Optimistic-lock — паттерн знаком.** В `link_sync` уже есть похожая семантика «base-version + conflict». Стоит переиспользовать тот же error-формат, чтобы UI/CLI единообразно показывали конфликты.
- **Поиск.** `search_docs` сейчас не индексирует task-docs. Решить заранее — индексировать сразу или Phase 2; иначе в проекте появится «второй сорт» докум, не покрытых поиском.

## Открытые вопросы

- **Q1.** Видимость в `search_docs` и Snowball Protocol — task-docs как полноправные docs, или отдельная категория с собственным скоупом?
- **Q2.** Миграция планов из `task.description` — автомат (выкусываем markdown-блок «План:») или ручное задание?
- **Q3.** Что с task-docs при `task.status=cancelled` — заморозить (read-only), оставить как есть, или удалить?
- **Q4.** Лимит количества кастомных ключей на задачу — есть ли cap или предупреждение?
- **Q5.** Сводка всех задач со старыми/устаревшими `plan`-доками — как выявлять (по дате, по revision-count, по дрейфу с реализацией)?
- **Q6.** Diff между ревизиями `plan` в UI — markdown-aware или plain text?
