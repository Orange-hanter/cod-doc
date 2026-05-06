# 09 — Activity & Events log (унифицированный таймлайн)

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: 04

## Контекст: как у paperclip

Документация прямо называет это «one of the systems»:
> *"Activity & Events — Mutating actions, heartbeat state changes, cost events, approvals, comments, and work products are recorded as durable activity so operators can audit what happened and why."*

Все мутации, переходы статусов, cost-events, approvals — single durable stream. Это позволяет:
- одной страницей UI ответить «что произошло сегодня в проекте/у этого агента»,
- собирать метрики (productivity, drift-frequency),
- дебажить «почему задача внезапно изменилась».

## Текущее состояние cod-doc

Аудит — фрагментарный:
- Ревизии — только по docs ([revision_tools.py](cod_doc/mcp/tools/revision_tools.py)).
- Изменения статусов задач — нет отдельного лога (только финальное состояние в БД).
- Запуски агента — нигде не сохраняются как сущность ([04](04-run-id-audit.md) это исправляет).
- Findings от drift-чеков — теряются после консольного вывода.

Нет ответа на вопросы:
- «Что изменилось в проекте за прошлую неделю?»
- «Какой агент-прогон последний раз трогал MASTER.md?»
- «Когда у этой задачи появился блокер и кто его убрал?»

## Предложение

Единая таблица `activity_events` (append-only):

```sql
CREATE TABLE activity_events (
  id           TEXT PRIMARY KEY,         -- uuid7 для сортировки
  ts           TIMESTAMP NOT NULL,
  actor_kind   TEXT NOT NULL,            -- 'orchestrator' | 'human' | 'routine' | 'system'
  actor_id     TEXT,                     -- user id, agent id, routine name
  run_id       TEXT,                     -- из 04, NULL для прямых human-действий
  kind         TEXT NOT NULL,            -- canonical event kind (см. ниже)
  scope_kind   TEXT,                     -- 'task' | 'doc' | 'story' | 'project'
  scope_id     TEXT,                     -- id сущности
  payload      JSON,                     -- typed по kind
  summary      TEXT                      -- человекочитаемая строка
);

CREATE INDEX idx_activity_ts ON activity_events(ts DESC);
CREATE INDEX idx_activity_scope ON activity_events(scope_kind, scope_id, ts DESC);
CREATE INDEX idx_activity_run ON activity_events(run_id);
```

### Канонические `kind` значения

| `kind`                          | Семантика                                            |
| ------------------------------- | ---------------------------------------------------- |
| `task.created`                  | Создана задача                                       |
| `task.status_changed`           | Изменён статус (payload: from/to)                    |
| `task.checked_out` / `released` | Lock из [06](06-atomic-checkout.md)                  |
| `task.blocker_added` / `cleared` | Изменены блокеры                                    |
| `task.commented`                | Добавлен комментарий                                 |
| `doc.created` / `updated` / `renamed` | Изменён глобальный doc                         |
| `doc.drift_detected`            | Обнаружен sha-mismatch                              |
| `task_doc.updated`              | Изменён task-bound doc (см. [05](05-issue-documents.md)) |
| `master.updated`                | Обновлён MASTER.md (хэши, секции)                    |
| `link.synced` / `broken`        | Изменения линков                                     |
| `run.started` / `finished` / `failed` | Жизненный цикл agent-run'а                     |
| `routine.fired` / `found_issue` / `created_task` | Из [07](07-routines.md)             |
| `approval.requested` / `resolved` | Из [12](12-approvals.md)                          |

### Источники событий

Каждый MCP-write-tool **дополнительно** к своему write-у пишет событие. Реализация — через декоратор/middleware в [cod_doc/mcp/tools/_db.py](cod_doc/mcp/tools/_db.py) или явные вызовы `record_event(...)`.

### MCP-тулы для чтения

- `activity_list(scope_kind?, scope_id?, kind?, since?, until?, actor?, limit?)` — основной фильтр.
- `activity_for_run(run_id)` — что произошло в конкретном run'е (дополняет [04](04-run-id-audit.md)).
- `activity_summary_daily(date_range)` — агрегаты для dashboard'а.

### UI

- **Project timeline** — лента событий по проекту.
- **Task timeline** — на карточке задачи (заменяет/дополняет существующие comments).
- **Run page** — все события прогона (дополняет [04](04-run-id-audit.md)).
- **Daily digest** — на главной: «вчера: 12 событий, 2 drift'а, 3 закрытых задачи».

## План внедрения

1. **Схема + миграция.** Таблица + индексы.
2. **Модель `Event`** в [cod_doc/core/](cod_doc/core/) + canonical kinds enum.
3. **Запись.** Поэтапно подключить write-тулы:
   - Phase 1: задачи (создание, статус, блокеры, комменты).
   - Phase 2: docs (включая task_docs из [05](05-issue-documents.md)).
   - Phase 3: master, links, runs, routines, approvals.
4. **MCP-тулы чтения.**
5. **UI:** базовый timeline-компонент на одной странице, потом — встроить в карточки.
6. **Ретеншн:** старые события (> 6 мес) можно архивировать в отдельную таблицу/файл, чтобы основная таблица оставалась шустрой.

## Риски

- **Дублирование с revisions.** Решение: revisions — это снимки **содержимого**; events — это **факты изменений** с контекстом (actor, run, summary). Они комплементарны, не альтернативны.
- **Размер таблицы.** Простая partitioning by month + ретеншн.
- **Несогласованность при сбое.** Запись события и сама мутация — в одной транзакции (если СУБД позволяет) или через outbox-pattern.

## Метрики успеха

- 100% мутирующих MCP-тулов пишут событие.
- На странице задачи виден полный таймлайн (без необходимости запускать `revision_list` отдельно).
- Daily digest даёт оператору понимание «что вообще произошло» за < 10 секунд чтения.

## Связанные

- 04 (run-id) — `run_id` — обязательное поле, основной correlation key.
- 05 (issue docs) — изменения task-doc'ов в потоке.
- 06 (checkout) — checkout/release как события.
- 07 (routines) — routine-fires в потоке.
- 12 (approvals) — approval lifecycle в потоке.

## Замечания (контекст cod-doc)

- **Revisions ≠ events.** Разграничение в RFC корректное и важное: revisions — снимки контента, events — факты с actor/run/scope/payload. Не пытаться унифицировать в одну таблицу.
- **SQLite → одна транзакция; Postgres → outbox.** Решение про подход нужно зафиксировать на старте, потому что последующее переключение требует миграции существующих событий. Учитывая, что мы пока на sqlite — одна транзакция простая и работает; outbox — overkill.
- **Ретеншн с самого начала.** Без архивации таблица за год набирает миллионы строк (drift каждые 30 мин = 17k событий/год только от одной routine). Архив > 6 месяцев в JSONL-файл или отдельную таблицу с тем же индексом.
- **Корреляция с git.** Часть мутаций в cod-doc ведёт к коммиту в репо проекта (например, `master.updated`). Поле `commit_sha` в `payload` для таких событий замыкает аудит-цепочку «событие → коммит в проекте».
- **Phase 1 — задачи и docs.** Не пытаться записать всё сразу. Сначала задачи (status changes, checkout, blockers), потом docs (включая task-docs), потом master/links/runs/routines/approvals. Каждая фаза = отдельный PR.

## Открытые вопросы

- **Q1.** Outbox или одна транзакция — фиксируем какой подход на старте? Если sqlite — однозначно одна транзакция?
- **Q2.** Архив (> 6 мес) — отдельная таблица `activity_events_archive`, JSONL-файл на диске, или просто `archived=true` boolean без переноса?
- **Q3.** Включать ли read-events (просмотры docs/tasks через MCP)? Полезно для метрик «куда смотрит агент», но шум.
- **Q4.** `payload` структура — typed per-kind (Pydantic-модели на каждый kind) или generic JSON с runtime-валидацией?
- **Q5.** Корреляция с git-коммитами — добавлять `commit_sha` в payload для масштаб-релевантных событий или отдельная таблица `activity_event_git_link`?
- **Q6.** Что делать, если запись события упала, а сама мутация прошла (ошибка в outbox-флоу)? Молчать, retry, или эскалация?
- **Q7.** Нужны ли «summary» события — агрегаты (например, `daily_summary` строка с подсчётами), или это вычисляется on-demand?
