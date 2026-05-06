# 12 — First-class Approvals

> Категория: 🔵 Архитектура · Риск: средний · Зависимости: 04, 05, 08

## Контекст: как у paperclip

```
POST /api/companies/:id/approvals
{
  "type": "request_board_approval",
  "requestedByAgentId": "...",
  "issueIds": ["..."],
  "payload": {
    "title": "Approve monthly hosting spend",
    "summary": "Estimated cost is $42/month for provider X.",
    "recommendedAction": "Approve provider X and continue setup.",
    "risks": ["Costs may increase with usage."]
  }
}
```

Approval — **first-class entity**:
- Ссылается на конкретные issues, ставит их в `in_review`.
- При resolve (approve/deny) — wake'ает запросившего агента с `PAPERCLIP_APPROVAL_ID` + `_STATUS`.
- Видна в UI как отдельная сущность, а не комментарий.
- Есть полный аудит: кто запросил, кто решил, когда, payload.

Скилл явно учит:
> *"If the plan needs explicit approval before implementation... create a `request_confirmation` issue-thread interaction... update the source issue to `in_review`... Wait for acceptance before creating implementation subtasks."*

## Текущее состояние cod-doc

Из памяти проекта:
- **FM-002, FM-003** (структурная валидация) — escalate, но через ad-hoc сообщения / ручную остановку.
- **FM-004, FM-005** (advisory) — добавляются как issues / комменты.

Нет:
- структурированной сущности «pending decision»,
- автопаузы задачи до решения,
- автопробуждения после решения,
- аудита решений.

## Предложение

### 1. Сущность `Approval`

```python
@dataclass
class Approval:
    id: str                             # uuid7
    type: ApprovalType                  # 'plan_review' | 'risky_action' | 'fm_escalation' | 'budget' | 'manual'
    requested_by: str                   # 'orchestrator-run-X' | 'human:<id>'
    requested_at: datetime
    status: ApprovalStatus              # 'pending' | 'approved' | 'denied' | 'cancelled' | 'expired'
    resolved_by: str | None             # human:<id>
    resolved_at: datetime | None
    payload: ApprovalPayload            # title, summary, recommendedAction, risks, links
    linked_task_ids: list[str]
    linked_doc_revision_ids: list[str]  # для approve plan@rev-abc
    expires_at: datetime | None
    decision_comment: str | None
    run_id: str | None                  # из 04
```

`ApprovalType`:
- `plan_review` — плана task-doc'а (см. [05](05-issue-documents.md))
- `risky_action` — оркестратор просит подтверждение перед операцией
- `fm_escalation` — структурная валидация требует ручного решения (FM-002/003)
- `budget` — превышение порога стоимости
- `manual` — оператор завёл вручную

### 2. Автоматика

- При создании approval с `linked_task_ids` — все эти задачи → `in_review` (см. [08](08-status-taxonomy.md)).
- При resolve approval — соответствующий wake (см. [03](03-wake-payload.md)) с `reason='approval_resolved'`, payload содержит решение и комментарий.
- При expiry — wake запросившего, payload `reason='approval_expired'`.

### 3. MCP-тулы

- `approval_request(type, payload, linked_task_ids?, linked_doc_revisions?, expires_in?)` → `approval_id`
- `approval_list(status?, type?, since?)` 
- `approval_get(approval_id)` 
- `approval_resolve(approval_id, decision: 'approve'|'deny', comment?)`
- `approval_cancel(approval_id, reason)`

### 4. UI

- **Inbox для оператора:** все `pending` approvals на главной — приоритетный список.
- **Карточка approval:** payload, linked tasks, кнопки approve/deny, поле для комментария.
- **На карточке задачи:** badge «pending approval: …» с deep-link.

### 5. Привязка к существующей валидации cod-doc

| Случай                        | Текущее поведение                         | С proposal                                  |
| ----------------------------- | ----------------------------------------- | ------------------------------------------- |
| FM-002 (схема MASTER нарушена) | Raise, ручное вмешательство              | `approval_request('fm_escalation', ...)`, задача → `in_review` |
| FM-003 (структурный конфликт)  | Raise                                    | То же                                       |
| FM-004/005 (advisory)         | Issue в audit-report, не блокирует        | Никаких approval'ов — оставляем как есть    |
| Drift в критичном doc         | Создаётся task в очередь                  | Optional: для doc'ов с `confidence_required=high` создавать approval перед автофиксом |
| Risky bulk-операция           | Сейчас нет — агент просто делает          | `approval_request('risky_action', ...)` перед действием |

## План внедрения

1. **Схема + миграция.** Таблицы `approvals`, `approval_history`.
2. **Доменная модель.** В [cod_doc/core/](cod_doc/core/).
3. **MCP-тулы.**
4. **Status-machine integration** ([08](08-status-taxonomy.md)) — `linked_task_ids` авто `in_review` ↔ approval status.
5. **Wake integration** ([03](03-wake-payload.md)) — resolve триггерит wake с правильным payload.
6. **Validation integration.** Refactor FM-002/003 — вызывают `approval_request` вместо raise. FM-004/005 — без изменений.
7. **UI:** approvals inbox + карточка.

## Риски

- **Approval fatigue.** Слишком много approval-запросов → их игнорируют. Решение: типы строго ограничены, FM-004/005 НЕ генерируют approval, advisory-комменты остаются комментами.
- **Зависшие approval'ы.** Решение: `expires_at` обязателен (default 7 дней?) + routine `approval_stale` (см. [07](07-routines.md)) wake'ает оператора.
- **Параллельные конфликтующие approvals на одну задачу.** Решение: правило «не более 1 pending approval на task». Создание дубля → сначала auto-cancel предыдущего с reason='superseded'.

## Метрики успеха

- 100% FM-002/003 эскалаций оформлены как approval'ы (не как ручная остановка).
- Среднее время до resolve approval'а — наблюдаемая метрика.
- 0 silently-skipped эскалаций (все попадают на видный inbox).

## Связанные

- 04 (run-id) — каждый approval хранит run_id запросившей операции.
- 05 (issue docs) — approval может ссылаться на конкретную ревизию `plan` (approve plan@rev-abc).
- 08 (status taxonomy) — `in_review` — основной статус для задач с pending approval.
- 09 (activity log) — события `approval.requested`, `approval.resolved`, `approval.expired`.
- 03 (wake-payload) — resolve approval'а — типичный источник wake'а.

## Замечания (контекст cod-doc)

- **FM-004/005 НЕ генерируют approval — критично.** Самая частая причина «approval fatigue» — превратить любую advisory-проверку в обязательный approval. Текущая раскладка (002/003 → escalation, 004/005 → advisory) — правильная, и сохранение её в этом proposal'е необходимо.
- **`expires_at` обязателен.** Зависший approval — это задача в `in_review` навсегда. Для single-user'а 7 дней (как у paperclip) — слишком долго; реалистичнее 48 часов с routine `approval_stale`, которая wake'ает оператора раньше истечения.
- **Inbox в UI — не Phase 2, а MVP.** Если approval-сущность есть, но видимости нет, оператор её просто не увидит, и pending'и накопятся. Минимально — счётчик pending в navbar и отдельная страница со списком.
- **Зависит от 04+05+08.** Без [08](08-status-taxonomy.md) `in_review` смешан с `blocked`; без [05](05-issue-documents.md) непонятно, что значит «approve plan@rev-abc»; без [04](04-run-id-audit.md) непонятно, какой run запросил approval. Делать после всех трёх.
- **«Не более 1 pending approval на task».** Без этого правила параллельные approvals на одну задачу создают неоднозначность. Auto-cancel предыдущего с `reason=superseded` — норм, но это надо явно показать в activity log, иначе будет выглядеть как баг.

## Открытые вопросы

- **Q1.** Default `expires_at` — 48 часов, 7 дней, или per-type (например, `risky_action` — 24h, `plan_review` — 7 дней)?
- **Q2.** Если оператор оффлайн на > expires_at — что делать: auto-deny (безопасно), auto-cancel с retry-wake (мягче), или просто `expired` с явным re-request от агента?
- **Q3.** «Частичный approve» — можно ли approve один task из `linked_task_ids`, оставив остальные pending? Или approval — атомарная единица?
- **Q4.** UI inbox — отдельная страница или встроенный виджет на главной? Счётчик pending в navbar — обязателен сразу?
- **Q5.** Уведомления — нужны ли (email/desktop) при появлении pending approval, или достаточно того, что оператор сам зайдёт в UI?
- **Q6.** Approval payload может содержать ссылки на ревизии doc'ов — что делать, если ревизия удалена/откачена до resolve? Auto-cancel approval с `reason=base_revision_gone`?
- **Q7.** Возможен ли approval без `linked_task_ids` (например, проектное решение, не привязанное к задаче) — допустимо или ошибка?
