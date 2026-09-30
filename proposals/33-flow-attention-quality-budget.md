# 33 — Поток при ИИ-исполнителе: внимание человека, качество, бюджеты

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: нет обязательных;
> RFC 31 (событие `task.committed`, колонка Backlog) и RFC 30 (RFC в статусе
> `review`) расширяют очередь внимания, когда вольются. BPR-001 (`force` в
> `task_complete`) — источник одного сигнала качества.

## 1. Контекст

Kanban ограничивает число задач в работе, потому что узкое место команды
людей — их руки. У cod-doc исполнитель — ИИ-агент, который пишет код
конвейером; решение владельца (2026-09-30): WIP-лимит не нужен, вопрос в
качестве и бюджетах мощностей. Исследование «Конвейер артефактов»
(`docs/system/research/2026-09-30-artifact-pipeline.md` §4.5) выделяет два
узких места:

1. **Внимание человека.** Агент производит быстрее, чем владелец
   принимает. Всё, что ждёт решения человека, — единственная очередь, где
   закон Литтла по-прежнему работает.
2. **Бюджет мощностей** — токены, деньги, лимиты провайдеров.

Плюс качество: при дешёвом производстве дорогим становится брак —
возвраты с ревью, откаты, закрытия в обход DoD.

Ни одно из трёх cod-doc сейчас не измеряет. Более того, при проверке
выяснилось, что сломана и база — метрики длительности (F1–F3).

## 2. Текущее состояние (проверено по коду и живой БД 2026-09-30)

| # | Факт | Где |
|---|---|---|
| F1 | `task_metrics.in_progress_hours` / `blocked_hours` пусты у **всех** 231 строк: `_derive_state_durations` ищет в диффе ревизии ключи `new_status`/`to`, а `update_status` пишет `{"op":"status","old","new"}` | `services/metrics_service.py:98`; `services/task_service.py:306-307, 645` |
| F2 | `checkout` (переход `todo → in_progress`) пишет событие `task.checked_out`, но не ревизию — начало работы в ревизиях не видно вовсе | `services/checkout_service.py:73` |
| F3 | MCP `task_update_status` эмитит второй `task.status_changed` без `old_status` поверх события сервиса: на живой БД 16 из 92 событий — дубли | `mcp/tools/task_tools.py:951-961` |
| F4 | Метрики выставлены только в вебе (`/p/{slug}/metrics`); MCP и CLI — нет (нарушение «четырёх поверхностей») | `api/web/pages/metrics.py:19`; `services/metrics_service.py:182-237` |
| F5 | `agent_run.llm_tokens_in/out`, `llm_calls` никто не пишет: 20 строк, 0 с токенами; раннер, для которого они задуманы, не используется (ADR-012) | `infra/models/revisions.py:51-86`; `services/run_context.py:88, 368` |
| F6 | `trace_call` (model, input/output tokens, task_id) пишут только веб-помощники ИИ; стоимость считается при чтении, страница `/costs` есть | `services/trace_service.py:25, 57, 94`; `api/web/pages/costs.py:22` |
| F7 | Расход coding-агентов есть только в транскриптах Claude Code: в каждом сообщении `usage` (input, output, cache_creation, cache_read, thinking); `task_checkout` встречается в 34 транскриптах проекта. `scripts/agent_usage_report.py` читает транскрипты, но токены не считает и в БД не пишет | `~/.claude/projects/-Users-dakh-Git--my-cod-doc/*.jsonl`; `scripts/agent_usage_report.py:52, 327` |
| F8 | Тип согласования `budget` заведён, но ни один код его не создаёт; таблица `approval` пуста | `services/approval_service.py:42`; `approval_request` `:154` |
| F9 | В `in_review` задача попадает только через `approval_request`; счётчика возвратов `in_review → in_progress` нет; живых задач в `in_review` — 0 | `services/task_status_machine.py:72-73`; `approval_service.py:219-238` |
| F10 | Проверки пересечения `affected_file` между задачами в работе нет; таблица и индекс по `path` есть | `infra/models/plans.py:159-172` (`ix_affected_path`) |
| F11 | «Застрявшая» задача — фиксированные 24 ч от `last_updated` | `services/task_service.py:1640-1644` |
| F12 | Чего ждёт человек, сейчас разбросано: RFC/документы `review` (0), истории `draft` (1), approvals `pending` (0), задачи `in_review` (0) — единой сводки нет | живая БД |

## 3. Предложение

### 3.1. Фундамент: метрики из событий, без дублей (секция A плана)

- `_derive_state_durations` читает переходы из `activity_event`
  (`task.status_changed` с `old_status`/`new_status`, `task.checked_out`,
  `task.completed`), а не из диффов ревизий. События — журнал переходов
  (ADR-012), ревизии — журнал содержимого.
- MCP `task_update_status` перестаёт эмитить собственное событие — сервис
  уже эмитит (F3). Тест: одна смена статуса через MCP = одно событие.
- `checkout` пишет ревизию `op=status` (F2) — след в том же журнале, что и
  остальные переходы (ADO-040).
- Бэкфилл: пересчёт `task_metrics` по `activity_event` для задач, у которых
  есть события; дубли без `old_status` при пересчёте игнорируются.
- Метрики на MCP и CLI: `flow_metrics(project, since=None, group_by="type")`
  и `cod-doc flow metrics` поверх `metrics_service.summary` (F4).

### 3.2. Очередь внимания человека

```python
def attention_queue(session, *, project_id, now=None) -> list[AttentionItem]
# AttentionItem: kind, ref, title, waiting_since, age_hours, action_hint
```

| kind | Источник | С какого момента ждёт |
|---|---|---|
| `task_review` | `task.status='in_review'` | последнее событие перехода в `in_review` |
| `approval` | `approval.status='pending'` | `approval.created` |
| `story_draft` | `user_story.status='draft'` | `created` |
| `doc_review` | `document.status='review'` | последняя ревизия |
| `rfc_review` | после RFC 30: RFC в `review` | переход в `review` |
| `idea_shaped` | после RFC 31: `idea.status='shaped'` | переход в `shaped` |

Поверхности: MCP `attention_list`, CLI `cod-doc attention`, веб — блок на
главной проекта. Метрика — возраст самого старого пункта и p50 времени до
решения за 30 дней (закон Литтла: растёт очередь при той же пропускной
способности человека — растёт ожидание). Пункт `curator_next`
`attention_backlog` — только сводкой «N пунктов ждут, старейший X дней»,
без отдельного пункта на каждую задачу: куратор не принимает решения за
человека.

### 3.3. Качество как сигнал потока

`flow_quality(project, since, group_by: "type" | "author" | "plan")` —
запросы, без новой схемы:

| Сигнал | Запрос |
|---|---|
| Возвраты с ревью | `activity_event` `kind='task.status_changed'`, `json_extract(payload,'$.old_status')='in_review'`, `new_status ∈ {in_progress, in-progress}`, group by `scope_id` (фильтр по `old_status` заодно отсекает дубли F3) |
| Закрытия в обход DoD | `task.completed` с `force=true` (после BPR-001) |
| Откаты | `revision` с `op=revert` по сущностям, изменённым задачей |
| Возврат находок | находки, переоткрытые `reconcile_partition`, чей `promoted_task_id` — закрытая задача (починка не удержалась) |
| Доля задач без acceptance при закрытии | до BPR-001 — замер; после — должна быть 0 |

Группировка по `author` показывает, какой агент (или модель) даёт больше
брака: это данные для выбора модели под тип задачи, а не рейтинг.

### 3.4. Бюджеты: расход агентов в БД

**Приём.** `usage_service.ingest_transcripts(session, *, project_id,
transcripts_dir, since=None) -> IngestReport`:

1. Читает `*.jsonl` Claude Code (F7), берёт из каждого assistant-сообщения
   `message.usage` и `message.model`.
2. Привязывает сообщение к задаче: от вызова `task_checkout(task_id=X)` до
   `task_complete`/`task_release` того же X в той же сессии; сообщения вне
   окна — к `task_id=NULL` (расход сессии без задачи).
3. Пишет в существующую `trace_call` (`kind="agent_session"`, model,
   input/output tokens, task_id) — страница `/costs` и оценка стоимости
   начинают работать без правок (F6).
4. Идемпотентность: новая колонка `trace_call.source_ref`
   (`<session_id>:<message_uuid>`) с уникальным индексом; повторный прогон
   ничего не дублирует.

Поверхности: CLI `cod-doc usage ingest [--since]`, MCP `usage_ingest`,
рутина `usage_ingest` (cron, по умолчанию раз в час) — демон на той же
машине, что и транскрипты. Кэш-токены хранятся отдельно (новые колонки
`cache_read_tokens`, `cache_write_tokens`): в стоимости они весят иначе.

**Бюджет.** `plan.budget_tokens` (nullable). Рутина `budget_check`: расход
задач плана превысил 80% / 100% бюджета → согласование типа `budget` (F8),
одно на порог, с payload `{plan, spent, budget, top_tasks}`. Бюджет RFC
(appetite) — после RFC 30, тем же механизмом.

`agent_run.llm_tokens_*` (F5) не трогаем: раннер не используется, колонки
остаются для него.

### 3.5. SLE и застрявшие задачи

- SLE = p85 `in_progress_hours` по типу задачи за 60 дней (после §3.1) —
  в `flow_metrics` как прогноз «85% задач типа X закрываются за N часов».
- Пункт `curator_next` `task_aging`: задачи в работе старше SLE своего
  типа. Фиксированные 24 ч `task_stale` (F11) остаются для типов, где
  истории меньше 10 задач.

### 3.6. Пересечение правок параллельных агентов

`checkout` возвращает `warnings: [{path, held_by_task, held_by_agent}]`,
если `affected_file` задачи пересекается с задачей другого агента в
`in_progress` (F10). Не блокирует: пересечение по файлу — повод
посмотреть, а не запрет.

### 3.7. Страница потока

Веб `/p/{slug}/metrics` расширяется: CFD по статусам (из `activity_event`),
scatter cycle time с линиями p50/p85/p95, очередь внимания, расход по
планам. Данные — те же сервисные функции, что у MCP/CLI.

## 4. Миграция и обратная совместимость

- Миграция `0044_flow` (после `0043_ideas` из RFC 31, если тот вольётся
  раньше; иначе `0043`): `trace_call.source_ref` + unique,
  `trace_call.cache_read_tokens`, `cache_write_tokens`,
  `plan.budget_tokens`. `document` не трогается.
- Бэкфилл `task_metrics` — одноразовая команда `cod-doc flow metrics
  --rebuild`; до неё старые строки остаются с NULL.
- Удаление дублирующего события в MCP меняет число событий на смену
  статуса с 2 до 1 — потребители, считавшие события, увидят половину.
  Проверить `activity_summary` и веб-ленту.
- Новые тулы: `flow_metrics`, `flow_quality`, `attention_list`,
  `usage_ingest` — `standard`/`full` +4; счётчики в прозе по
  `PROSE_COUNTERS`.

## 5. Риски и что не делаем

| Не делаем | Почему |
|---|---|
| WIP-лимиты на число задач | решение владельца: ограничение команды людей |
| Блокировку checkout по пересечению файлов | предупреждение достаточно; блок остановит конвейер на ложных пересечениях |
| Рейтинг агентов/моделей | сигналы качества — для выбора модели под тип задачи, не для наказания |
| Точную стоимость в валюте | оценка по прайсу модели при чтении, как сейчас в `/costs`; счёт провайдера — источник истины |
| Замер токенов изнутри агента (хуки, прокси) | транскрипты уже содержат `usage`; второй канал — второй источник |
| Прогнозы дат | решение владельца (ROADMAP:408); SLE — вероятность, не срок |

Риски: (1) формат транскриптов Claude Code не контракт — парсер держит
версию схемы и падает громко при неизвестной; (2) привязка к задаче через
окно checkout → complete ошибается, если агент ведёт две задачи в одной
сессии — расход делится по окнам, пересечение окон относится к обеим с
пометкой; (3) транскрипты других харнессов (ZCode, рой) не читаются —
расширение отдельной задачей.

## 6. Оценка

11 задач, ~1,5 недели агентской работы. Секция A — фундамент, без неё
остальное считает на пустых данных.

- **A. Фундамент:** (1) метрики из `activity_event` + бэкфилл; (2) убрать
  дубль события в MCP; (3) ревизия на checkout; (4) `flow_metrics` MCP/CLI.
- **B. Внимание:** (5) `attention_queue` + поверхности + пункт куратора.
- **C. Качество:** (6) `flow_quality`.
- **D. Бюджеты:** (7) `usage_ingest` + миграция `trace_call`; (8) рутина
  `usage_ingest`; (9) `plan.budget_tokens` + рутина `budget_check` →
  approval `budget`.
- **E. Поток:** (10) SLE + `task_aging` + предупреждение о пересечении
  файлов; (11) страница потока (CFD, scatter).

## 7. Открытые вопросы

1. Порог бюджета — токены или оценка в деньгах? Токены стабильнее (не
   зависят от прайса), деньги понятнее человеку.
2. Читать ли транскрипты сабагентов (вложенные каталоги сессий) как расход
   родительской задачи?
