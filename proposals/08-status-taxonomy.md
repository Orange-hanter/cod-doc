# 08 — Status taxonomy: `in_review` ≠ `blocked`

> Категория: 🟡 Адаптация · Риск: низкий · Зависимости: —

## Контекст: как у paperclip

Полный набор: `backlog | todo | in_progress | in_review | done | blocked | cancelled`.

Скилл явно фиксирует семантику каждого:

| Статус        | Семантика                                                                                  |
| ------------- | ------------------------------------------------------------------------------------------ |
| `backlog`     | Парконуто, не сейчас. Не для активного heartbeat.                                          |
| `todo`        | Готово к работе, не взято. Переход в `in_progress` **только** через `checkout`.            |
| `in_progress` | Активно ведётся, есть owner с lock'ом.                                                     |
| `in_review`   | **Здоровая waiting-path** — ждёт ревью/апрува/ответа. НЕ синоним done.                     |
| `blocked`     | Не может двигаться, пока что-то не изменится. Обязательно `blockedByIssueIds` или owner.   |
| `done`        | Закрыто.                                                                                   |
| `cancelled`   | Отменено намеренно, не возобновится.                                                       |

Ключевой паттерн: `in_review` — это **explicit waiting posture**. Когда агент создаёт approval-request или ждёт человеческого решения, задача идёт в `in_review`, не в `blocked`.

`cancelled` blocker НЕ считается resolved — нужно явно убрать или заменить.

## Текущее состояние cod-doc

В [cod_doc/core/project.py](cod_doc/core/project.py) `TaskStatus` существует, но:
- `in_review` (если есть) семантически смешан с `blocked`,
- нет жёсткого правила «`todo → in_progress` только через checkout» (см. [06](06-atomic-checkout.md)),
- FM-002/FM-003 эскалации (из памяти агента) не имеют выделенного статуса — обычно живут в комментариях или ad-hoc «отложил пока спрошу».

## Предложение

### 1. Зафиксировать набор статусов как канонический

```python
class TaskStatus(StrEnum):
    BACKLOG = 'backlog'
    TODO = 'todo'
    IN_PROGRESS = 'in_progress'
    IN_REVIEW = 'in_review'      # waiting for human/approval
    BLOCKED = 'blocked'          # waiting for another task
    DONE = 'done'
    CANCELLED = 'cancelled'
```

Если в текущем коде части этих статусов не было — миграция с маппингом старых значений.

### 2. Жёсткие правила переходов

| From          | Допустимые To                                  | Условие                                |
| ------------- | ---------------------------------------------- | -------------------------------------- |
| `backlog`     | `todo`, `cancelled`                            | —                                      |
| `todo`        | `in_progress`                                  | **только** через `task_checkout` (06)  |
| `todo`        | `blocked`, `backlog`, `cancelled`              | прямой PATCH OK                        |
| `in_progress` | `in_review`, `blocked`, `done`, `cancelled`    | требует валидный checkout              |
| `in_review`   | `in_progress`, `done`, `cancelled`             | по resolve approval/ревью              |
| `blocked`     | `todo`, `in_progress`, `cancelled`             | `todo` авто при resolve `blockedBy`    |
| `done`        | `todo`, `in_progress`                          | reopen, требует подтверждение          |
| `cancelled`   | `todo`                                         | reopen, явно                           |

### 3. Привязка к существующим паттернам cod-doc

Из памяти проекта:
- **FM-002, FM-003 (структурная валидация → raise)** → задача переходит в `blocked` с `blockedByIssueIds=[<новая task на починку>]`.
- **FM-004, FM-005 (advisory)** → задача остаётся в `in_progress`, добавляется comment в activity log.
- **Approval-request** (см. [12](12-approvals.md)) → `in_review` с явным `pending_approval_id`.

### 4. Auto-wake правила

- При закрытии задачи (`status=done`) — все её `blockedBy`-зависимые автоматически проверяются: если все блокеры resolved → wake assignee dependent-задачи (см. [03](03-wake-payload.md)).
- `cancelled` НЕ resolves blocker. UI и MCP-tool `task_set_blocker` warn'ят, если в blocker'ах есть cancelled задача.

### 5. UI

- Колонки kanban: `backlog | todo | in_progress | in_review | done`. `blocked` — оверлей-бейдж (показывает блокеры), `cancelled` — отдельный фильтр.
- Карточка `in_review` явно показывает «waiting for: <approval/user/review>».

## План внедрения

1. **Аудит текущего перечня TaskStatus.** Что уже есть, что добавить.
2. **State-machine.** Чистая функция `validate_transition(from, to, context) -> Result`. Тесты на каждый переход.
3. **Refactor MCP-тулов.** `task_update_status` использует state-machine.
4. **Auto-wake hook.** При переходе в `done` или `cancelled` — событие, обработчик пробуждает зависимых.
5. **UI.** Обновление kanban-колонок.
6. **Документация.** Раздел в скилле `validation` (см. [01](01-skills-layer.md)).

## Риски

- **Backward compatibility.** Если какие-то задачи уже в "не из списка" статусах — миграция должна их явно смаппить. Скрипт миграции с предпросмотром.
- **`in_review` overload.** Соблазн положить туда всё «не готово, но не блокировано». Решение: правило «у `in_review` всегда есть `pending_*` поле — approval, comment, doc-revision-pending».

## Метрики успеха

- 0 задач в `in_progress` без валидного checkout'а.
- Каждая `blocked`-задача имеет либо `blockedByIssueIds`, либо `blocker_owner: <user>`.
- Среднее время в `in_review` сокращено (видимость → быстрее реакция).

## Связанные

- 06 (checkout) — определяет, как происходит `todo → in_progress`.
- 12 (approvals) — типичный источник `in_review` статуса.
- 09 (activity log) — переходы статусов — события первого класса.

## Замечания (контекст cod-doc)

- **Аудит существующего перечня — первая задача.** Прежде чем фиксировать набор, нужно посмотреть в [cod_doc/core/project.py](cod_doc/core/project.py): возможно `in_review` уже есть, возможно нет. От этого зависит объём миграции.
- **`hypothesis` уже в dev-deps.** Идеально подходит для покрытия state-machine — генерируем (from, to) пары, проверяем закон «либо разрешён, либо отказ с причиной». Не упустить негативные кейсы.
- **Migration с предпросмотром.** Скрипт миграции должен сначала показать, какие задачи изменят статус и в какой, и только при confirm применить. Отдельная команда `cod-doc migrate-statuses --dry-run`.
- **`in_review` overload — реальный риск.** Соблазн положить туда «ну, не блокировано, но не активно». Правило «у `in_review` всегда есть `pending_*` поле» (approval_id / comment_id / doc_revision_id) — обязательная инвариант.
- **Auto-wake при resolve блокеров.** Сейчас `task_clear_blocker` уже есть, но не пробуждает зависимых. Связать с [03](03-wake-payload.md): clear → emit event → собрать wake-context для зависимой задачи.

## Открытые вопросы

- **Q1.** Что делать с задачами, чей текущий статус не из канонического списка (если такие найдены)? Маппинг по эвристике или ручной триаж?
- **Q2.** `done → todo` reopen — требует чего: флага `force=True`, approval'а, или явного комментария в audit?
- **Q3.** При `cancelled` — что с TaskDocuments ([05](05-issue-documents.md)): freeze (read-only), оставить editable, или soft-delete?
- **Q4.** Колонка `cancelled` в kanban — отдельная колонка, фильтр «Show cancelled», или скрыто всегда?
- **Q5.** `blocked` без `blockedByIssueIds` (только `blocker_owner: <user>`) — допустимо или ошибка валидации?
- **Q6.** Тайм-аут на `in_review` — есть ли smart-default (например, 7 дней без resolve → wake оператору), или только через [12](12-approvals.md)?
