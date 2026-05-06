# 06 — Атомарный checkout задач

> Категория: 🟡 Адаптация · Риск: низкий · Зависимости: 08 (статусы)

## Контекст: как у paperclip

```
POST /api/issues/:id/checkout
{
  "agentId": "...",
  "expectedStatuses": ["todo", "backlog", "blocked", "in_review"]
}
```

Семантика:
- Если issue в `expectedStatuses` → атомарно перевести в `in_progress`, заassign на agent, выдать lock.
- Если уже за этим агентом — вернуть OK (idempotent).
- Если за другим — `409 Conflict`. Скилл: **«Never retry a 409»**.
- Все мутации задачи требуют валидного активного checkout'а.

Эффект:
- Невозможно «случайно» работать с чужой задачей.
- Невозможно стартануть задачу не из ожидаемого состояния (catch a stale plan).
- Status-transition `todo → in_progress` — **через checkout**, не через прямой PATCH (это правило).

## Текущее состояние cod-doc

- В [task_tools.py](cod_doc/mcp/tools/task_tools.py) `task_update_status` принимает любой переход без оптимистичной проверки.
- Возможные сценарии гонки:
  - UI-вкладка показывает задачу `todo`, оператор нажимает «start» → агент уже её взял и она `in_progress`. UI перепишет неконсистентно.
  - Daemon триггерит wake по drift'у, в это время человек правит ту же задачу через CLI.
- Нет понятия «активный исполнитель задачи в данный момент».

## Предложение

1. **Добавить поля** в Task ([cod_doc/core/project.py](cod_doc/core/project.py)):
   - `checked_out_by: str | None` (run_id или 'human:<user>')
   - `checked_out_at: datetime | None`
   - `expected_status_at_checkout: TaskStatus | None`

2. **MCP-tool `task_checkout(task_id, agent='orchestrator'|'human:<id>', expected_statuses: list[TaskStatus])`:**
   - Атомарная транзакция: проверка статуса ∈ expected_statuses + установка `checked_out_by`.
   - Если уже заheckout-ен этим же актором → OK (идемпотентность).
   - Если другим → `CheckoutConflictError(409)`.
   - Переход `todo → in_progress` происходит **здесь**, не через `task_update_status`.

3. **MCP-tool `task_release(task_id, run_id)`:**
   - Снимает lock. Вызывается явно (после задачи) или автоматически по таймауту daemon'а.

4. **Все write-тулы задач** проверяют: операция возможна только если caller владеет checkout'ом (или явный `force=True` для админских кейсов).

5. **Stale-checkout watchdog:** daemon раз в N минут чистит lock'и старше TTL (например, 30 минут без активности run'а).

## Изменения в скилле орchestrator'а

- «Перед мутацией задачи — `task_checkout`. На 409 — НЕ ретраить, выбирать другую задачу или эскалировать».
- «По завершении — `task_release` явно».

## План внедрения

1. **Миграция БД.** Поля `checked_out_by`, `checked_out_at`, `expected_status_at_checkout`.
2. **Атомарная функция `_checkout`.** Через `SELECT ... FOR UPDATE` или (для SQLite) `BEGIN IMMEDIATE` + проверка-обновление в одной транзакции.
3. **MCP-тулы** `task_checkout`, `task_release`.
4. **Refactor `task_update_status`:** запретить прямой переход `todo → in_progress` (только через checkout); остальные переходы — через update, но с проверкой ownership.
5. **UI:** показ «in use by: orchestrator-run-X» на карточке; кнопка «force release» для админа.
6. **Watchdog** в [cod_doc/services/](cod_doc/services/).

## Риски

- **Поломка существующего флоу.** В коде уже могут быть места, делающие прямой `todo → in_progress`. Решение: миграция в два шага — сначала добавить checkout как опцию (warn без него), затем enforce.
- **Lock-leak.** Падение оркестратора без release. Решение: TTL + watchdog (см. выше).
- **UX-трение для одиночного пользователя.** В 95% случаев lock'а просто нет, и это работает прозрачно. Conflict — редкое явление, но когда возникает — спасает.

## Метрики успеха

- 0 race-условий при параллельной работе UI + daemon.
- Все задачи с `status=in_progress` имеют валидный `checked_out_by`.
- Watchdog ловит < 1% «зависших» checkout'ов в неделю (если больше — баг где-то ещё).

## Связанные

- 04 (run-id) — `checked_out_by` хранит run_id оркестратора.
- 08 (статусы) — определяет `expectedStatuses` для разных переходов.
- 09 (activity log) — checkout/release — события первого класса.

## Замечания (контекст cod-doc)

- **SQLite — `BEGIN IMMEDIATE`.** У нас sqlite-бэкенд, поэтому `SELECT ... FOR UPDATE` неприменим. Нужен явный `BEGIN IMMEDIATE` + проверка-обновление в одной транзакции. Тесты должны явно покрывать гонку — `pytest-xdist` или ручной thread-stress.
- **Поэтапный enforce.** Жёсткое требование checkout'а сразу подломит существующие места, делающие прямой `task_update_status(todo→in_progress)`. Phase 1 — warn-режим с логом «no checkout, proceeded», Phase 2 — enforce.
- **UI после COD-078.** UI redesign добавил быстрые действия — реальная вероятность гонки UI ↔ daemon выросла. Это аргумент в пользу скорейшего внедрения.
- **«In use by» индикатор.** Нужно показывать `checked_out_by` на карточке задачи; для single-user — иногда это будет `human:dakh`, иногда `orchestrator-run-X`. Различать визуально.
- **Реальный объём гонок.** Перед внедрением имеет смысл добавить лог-хак: писать в activity log, когда сейчас `task_update_status` меняет статус задачи, которую кто-то трогал < 5 секунд назад. Так увидим частоту реальной проблемы.

## Открытые вопросы

- **Q1.** Watchdog TTL — 30 минут разумно для одиночного агента? Если агент делает долгую LLM-итерацию (>10 мин), heartbeat'ы на продление lock'а или достаточно широкого TTL?
- **Q2.** Существующие задачи в `in_progress` без checkout — поставить `checked_out_by='legacy:human'` при миграции или сбросить в `todo`?
- **Q3.** «Force release» из UI — кто имеет право (любой локальный пользователь cod-doc), или нужен признак owner?
- **Q4.** Идемпотентность для CLI — повторный `cod-doc task checkout COD-N` той же сессией возвращает OK без перезаписи `checked_out_at`?
- **Q5.** Что делать с lock'ом при `cancelled` — авто-release или explicit?
- **Q6.** UI обновляет статус через polling или websocket? От этого зависит, сколько race'ов вообще видны юзеру.
