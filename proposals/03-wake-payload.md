# 03 — Wake-payload pattern

> Категория: 🎯 Прямое · Риск: низкий · Зависимости: 02

## Контекст: как у paperclip

При запуске агента через heartbeat в среду инжектятся переменные:
- `PAPERCLIP_TASK_ID`, `PAPERCLIP_WAKE_REASON`, `PAPERCLIP_WAKE_COMMENT_ID`, `PAPERCLIP_APPROVAL_ID`, `PAPERCLIP_APPROVAL_STATUS`, `PAPERCLIP_LINKED_ISSUE_IDS`.
- Самое важное: `PAPERCLIP_WAKE_PAYLOAD_JSON` — готовый компактный JSON с issue summary + новыми комментариями + причиной wake.

Скилл прямо требует:
> *"Use it first. For comment wakes, treat that batch as the highest-priority new context in the heartbeat: in your first task update or response, acknowledge the latest comment and say how it changes your next action before broad repo exploration."*

Эффект: агент **не делает** «сначала прочитаю всё, потом подумаю». Он сразу видит причину, контекст и может действовать.

Есть и **scoped-wake fast path:** если wake указывает на конкретную задачу, агент пропускает шаги «identity / inbox / pick work» и идёт сразу на checkout.

## Текущее состояние cod-doc

- В [cod_doc/agent/orchestrator.py](cod_doc/agent/orchestrator.py) запуск агента выглядит как «получи проект, читай очередь». Нет различия «холодный старт vs возобновление по конкретному триггеру».
- Системный промпт диктует: «1. Прочитай MASTER.md (L0)» — агент рефлекторно делает это всегда, даже когда поднят на конкретный таск.
- `run_agent_once` в MCP принимает контекст, но не использует его как «scoped wake».

## Предложение

Ввести понятие **WakeContext** в [cod_doc/agent/](cod_doc/agent/), который собирается **до** первого LLM-вызова и инжектится в систему как первое сообщение «WAKE PAYLOAD: ...».

```python
@dataclass
class WakeContext:
    reason: WakeReason  # cold_start | task_assigned | doc_drift | approval_resolved | manual
    task_id: str | None
    triggering_doc_ref: str | None
    triggering_revision_id: str | None
    payload: dict  # результат task_heartbeat_context (см. 02) если есть task_id
    skills_to_preload: list[str]  # из триггер-матчера
```

**Сборка:**
- Точка входа в `run_agent_once` / daemon принимает `WakeContext`.
- Если `task_id` задан → сразу вызвать `task_heartbeat_context` и положить в `payload`.
- Если `triggering_doc_ref` (например, drift-проверка нашла STALE) → положить срез по доку + список зависимых задач.

**Инжекция в LLM:**
- Первое сообщение в conversation — структурированный системный месседж:
  ```
  WAKE PAYLOAD
  reason: doc_drift
  triggering_doc: doc:specs_modules_md (sha mismatch)
  ...

  Acknowledge this in your first action.
  ```
- Орестратор-скилл (см. [01](01-skills-layer.md)) обязывает агента подтвердить wake-context в первом self_check.

**Scoped fast path:**
- Если `reason in {task_assigned, approval_resolved, doc_drift}` и `payload` содержит достаточно данных — скилл-инструкция говорит «не вызывай `get_master`, не сканируй очередь, сразу выполняй».

## План внедрения

1. **Модель `WakeContext`** + `WakeReason` enum.
2. **Сборщик** `build_wake_context(task_id?, doc_ref?, ...) -> WakeContext` — переиспользует [02](02-heartbeat-context.md).
3. **Адаптация `Orchestrator.run`** — принимает `WakeContext`, инжектит в conversation как первое user-message блоком (или поверх system).
4. **Обновление `run_agent_once` MCP-tool** — принимает явные триггер-параметры.
5. **Обновление daemon** ([cod_doc/services/](cod_doc/services/)) — при пробуждении из drift/cron/UI собирает корректный `WakeContext`.
6. **Скилл-правило** в `orchestrator/SKILL.md`: «если есть WAKE PAYLOAD — действуй по нему, MASTER.md не читать».

## Риски

- **Stale payload.** Если daemon собрал payload минуту назад, а состояние изменилось — у агента устаревшая картина. Решение: payload включает `assembled_at` и `since_revision_id`; агент при подозрении делает `task_heartbeat_context(since_revision_id=...)` для дельты.
- **Соблазн положить в payload «всё».** Решение: жёсткий лимит размера (например, 4KB), всё свыше — агент дёрнет сам.

## Метрики успеха

- Для wake'ов с явным triggering source: 0 вызовов `get_master` в первом round-trip.
- Время до первого продуктивного действия (write/update) сокращено vs cold-start.

## Связанные

- 02 (heartbeat-context) — payload это в основном результат heartbeat-context.
- 04 (run-id) — wake-context присваивает `run_id`, который потом тегает все мутации.
- 07 (routines) — routine при срабатывании создаёт wake с `reason=routine_<name>` и нужным payload.

## Замечания (контекст cod-doc)

- **Daemon уже триггерится по drift.** После COD-070..077 в [cod_doc/services/](cod_doc/services/) есть пробуждение по drift'у/UI-событиям, но без структурированного wake-context'а — каждый источник лепит свой набор аргументов. Единый `build_wake_context()` устраняет хаос.
- **Рефлекторное чтение MASTER.md.** Системный промпт сейчас прямо требует «1. Read MASTER.md (L0)» — это правильно для cold-start, но дорого для wake'а на конкретный таск. Скилл-инструкция должна явно различать два режима.
- **Race payload vs реальное состояние.** Между сборкой payload и стартом агента возможны внешние мутации. `assembled_at` + `since_revision_id` дают агенту способ проверить актуальность одним дешёвым вызовом, но это надо явно прописать в скилле, иначе агент будет доверять stale-payload'у.
- **Multiple reasons.** Если за 5 секунд произошли drift + approval_resolved + comment — собирать один wake с массивом reasons или N отдельных? Реальный сценарий для single-user — редкий, но семантика должна быть зафиксирована.

## Открытые вопросы

- **Q1.** Транспорт WakeContext — env vars (как paperclip), CLI argv, stdin-JSON, или отдельный MCP-вызов с `wake_id`? Влияет на как daemon запускает агента.
- **Q2.** Что делать, если payload собрался, но агент не стартанул (краш, kill)? Записать `run.aborted` событие или wake-context просто потерян?
- **Q3.** Логирование самого wake'а в activity log ([09](09-activity-log.md)) — даже если run не запустился? `wake.scheduled` / `wake.fired` / `wake.aborted`?
- **Q4.** Дебаунс — если за 1 секунду пришло 3 одинаковых wake'а (дрожание watcher'а), что делать?
- **Q5.** Может ли пользователь вручную «пересобрать» payload и перезапустить run (для отладки), не теряя историю?
