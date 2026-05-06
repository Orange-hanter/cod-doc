# 07 — Routines (cron-триггеры)

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: 03 (wake-payload)

## Контекст: как у paperclip

Routines — рекуррентные задачи. Каждый запуск создаёт **issue** assigned на routine'ового агента — он подбирает её обычным heartbeat-флоу.

```
POST /api/companies/:id/routines
{
  "name": "weekly-report",
  "agentId": "...",
  "triggers": [{ "type": "schedule", "cron": "0 9 * * MON" }],
  "concurrencyPolicy": "skip" | "queue" | "parallel",
  "catchUpPolicy": "run_once" | "run_all" | "skip"
}
```

Поддерживаются триггеры: `schedule` (cron), `webhook`, `api`. Concurrency и catch-up policies — first-class.

## Текущее состояние cod-doc

В cod-doc уже есть набор «health-чеков», но они вызываются вручную из CLI или UI:
- [check_stale_refs](cod_doc/mcp/tools/legacy_master_tools.py) — поиск устаревших ссылок в MASTER.md.
- [link_verify](cod_doc/mcp/tools/link_tools.py) — проверка целостности линков.
- [doc_drift](cod_doc/mcp/tools/doc_tools.py) — обнаружение sha-расхождений.
- `plan_audit`, `task_stale` — планерные проверки.

Нет:
- расписания их запуска,
- автоматического создания задач при найденных проблемах,
- единой панели «health pulse» проекта.

## Предложение

1. **Сущность `Routine`:**
   ```python
   @dataclass
   class Routine:
       id: str
       name: str
       enabled: bool
       trigger: RoutineTrigger        # cron | manual | event
       cron: str | None
       check: str                     # имя функции-проверки
       check_args: dict
       on_finding: OnFindingPolicy    # create_task | update_existing_task | comment_only
       concurrency: Literal['skip', 'queue']
       catch_up: Literal['skip', 'run_latest']
   ```

2. **Catalog встроенных проверок:**

   | Имя                  | Источник                                | Рекомендуемое расписание |
   | -------------------- | --------------------------------------- | ------------------------ |
   | `stale_refs`         | `check_stale_refs`                      | каждый час               |
   | `link_integrity`     | `link_verify`                           | каждые 4 часа            |
   | `doc_drift`          | `doc_drift`                             | каждые 30 мин (light)    |
   | `task_stale`         | `task_stale`                            | ежедневно                |
   | `plan_audit`         | `plan_audit`                            | при каждом merge         |
   | `revision_pruning`   | очистка старых revision'ов > N         | еженедельно              |

3. **Поведение `on_finding`:**
   - `create_task` — если найдена проблема, создать задачу нужного типа (например, drift → task `kind=fix`, привязка к найденному doc-ref).
   - `update_existing_task` — если уже есть открытая задача с тем же signature, добавить comment с дельтой; новую не плодить.
   - `comment_only` — записать в activity log (см. [09](09-activity-log.md)) без создания задачи.

4. **Триггер → wake.** Routine при срабатывании собирает `WakeContext` (см. [03](03-wake-payload.md)) с `reason='routine_<name>'` и `payload` содержащим findings + diff + рекомендуемое действие.

5. **Concurrency:**
   - `skip` — если предыдущий запуск ещё бежит, пропустить.
   - `queue` — поставить в очередь (но cap'ом, например 3).

6. **Catch-up:**
   - `skip` — пропустить пропущенные тики.
   - `run_latest` — выполнить один раз, как «накопленную» проверку.

## Реализация

- **Планировщик:** уже есть daemon в [cod_doc/services/](cod_doc/services/) — добавить scheduler-loop (например, на `apscheduler` или собственный simple cron).
- **MCP-тулы:**
  - `routine_list` / `routine_get` / `routine_create` / `routine_update_status` (enable/disable) / `routine_run_now`
  - `routine_history(routine_id, limit)` — последние запуски и их результаты
- **Хранилище:** таблицы `routines`, `routine_runs`.
- **UI:** страница «Routines» — таблица + кнопка «Run now» + последние результаты.

## Риски

- **Шум.** Слишком частые cron'ы создадут шквал задач. Решение: жёсткие defaults (см. таблицу выше) + `update_existing_task` policy для повторов.
- **Drift на стороне БД.** Если cron-схема падает — пропуск незаметен. Решение: routine_runs пишутся всегда, dashboard показывает «не запускалось N часов».
- **Дублирование existing daemon-логики.** Сначала убедиться, что текущий drift-watcher переезжает в этот фреймворк, а не сосуществует.

## Метрики успеха

- 100% «health-чеков» оформлены как routines.
- 0 запусков из CLI/UI «вручную, потому что забыли расписать».
- Найденные drift'ы автоматически становятся задачами в очереди оркестратора.

## Связанные

- 03 (wake-payload) — routine-trigger даёт WakeContext с пейлоадом findings'ов.
- 04 (run-id) — routine-run = один run_id, все созданные задачи и комменты тегаются.
- 09 (activity log) — `routine.fired`, `routine.found_issue`, `routine.created_task` — события.

## Замечания (контекст cod-doc)

- **Существующий drift-watcher должен мигрировать.** В daemon уже есть логика «найди drift → создай задачу». Не оставлять старый watcher параллельно с routines — будут дубли задач. Переезд первой routine = удаление эквивалентного куска из daemon.
- **`update_existing_task` — must-have policy.** Drift повторяется на одних и тех же доках; без дедупликации очередь захлебнётся. Signature для дедупа — детерминированный hash по `(check_name, scope_kind, scope_id, finding_kind)`.
- **Дефолты для single-user.** Concurrency `skip` + catch-up `run_latest` — единственно разумные. `parallel`/`queue` для одиночного режима только усложняют отладку.
- **Pause без удаления.** Возможность временно отключить routine критична при отладке (например, `doc_drift` каждые 30 мин шумит, пока чинишь батчем). Нужно поле `enabled` отдельно от удаления.
- **Расписание в локальном TZ.** Cron-выражения для single-user логичнее интерпретировать в локальной таймзоне юзера, не UTC. Требует фиксации в конфиге.

## Открытые вопросы

- **Q1.** Куда мигрирует существующая логика daemon health-check'ов — атомарно за один PR (риск регрессий) или поэтапно (риск временного дублирования)?
- **Q2.** Cron-парсер — стандартный (`croniter`/`apscheduler`) или собственный мини? Тащить ли deps ради `0 9 * * MON`?
- **Q3.** Как считать «то же signature» для `update_existing_task` — фиксированный набор полей или конфигурируемо per-routine?
- **Q4.** Routine-runs хранение — рядом с `agent_runs` ([04](04-run-id-audit.md)) или отдельная таблица? Если рядом — поле `kind=routine`?
- **Q5.** Можно ли создавать кастомные routines через UI/CLI, или только встроенные из catalog?
- **Q6.** Что делать, если `check`-функция упала с исключением — записать `routine_run.failed` и при следующем тике повторить, или эскалация после N подряд failures?
