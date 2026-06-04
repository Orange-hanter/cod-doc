# 21 — Degraded-Path Auditability + Error Audit Trail (гибрид)

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: proposal 04 (run-id), proposal 09 (activity log), proposal 17 (Living Specification)

## Контекст: что показало сравнение двух ревью

В 2026-06-04 проведено **параллельное** ревью COD-DOC двумя независимыми моделями (`miniMax-m3` и `deepseek-v4-pro`). Обе нашли одну и ту же центральную проблему — **видимость degraded paths и ошибок в production**:

- В коде **8 мест** с `# pragma: no cover` (degraded path / defensive guard).
- Degraded paths **никогда не выполняются** в тестах, но критичны для production (event_bus deferred commit, run_context project resolution, websocket cleanup).
- Service-level exceptions (`TaskNotFoundError`, `StatusTransitionError`, `TaskBlockedError`) **не пишутся** структурно — только HTTP 500 / MCP error response.
- `activity_log` (proposal 09) пишет **успешные** события, но не ошибки.

**Совпало в обоих отчётах:**
- 5/5 top weaknesses (cycle-5 stubs, legacy YAML/DB дубли, pragma:no cover, skill matcher fragility, Config.load I/O).
- 11/15 top observations включая: AGT-003..007 stubs, 5 legacy_*.py файлов (863 строки dead code), `task: Any` workaround в `agent_service.py:119`, `event_bus._subscribers` global state leak, `Config.load()` без кеша.

**Расхождение в подходе к RFC #21:**

| Подход | Pro | Mine | Компромисс |
|---|---|---|---|
| Storage для hard errors (TaskNotFound, StatusTransition) | DB-таблица `error_audit` | (не предлагал) | **DB-таблица** — для post-mortem |
| Storage для soft failures (degraded paths, defensive guards) | (не предлагал) | In-memory ring buffer 100 | **Ring buffer** — дешёво, не раздувает БД |
| Trigger | Декоратор / context manager | `@degraded_path("CODE")` | **Оба** |
| Visibility | Web UI + MCP | WebSocket через activity event | **Оба** |

Этот RFC — **гибрид** двух подходов: разные storage tiers для разных классов ошибок.

## Текущее состояние

Найдено 8 мест с `pragma: no cover`:

| File:line | Code (новое) | Severity | Назначение |
|---|---|---|---|
| `event_bus.py:123` | `EVENT_FLUSH_FAILED` | warning | deferred event flush после commit |
| `event_bus.py:132` | `EVENT_DROP_FAILED` | warning | deferred event drop после rollback |
| `run_context.py:140` | `RUN_CONTEXT_DB_MISSING` | warning | orchestrator не нашёл БД проекта |
| `run_context.py:179` | `RUN_START_DEGRADED` | warning | run_scope стартовал degraded |
| `run_context.py:214` | `RUN_FINALIZE_DEGRADED` | warning | run_scope финишировал degraded |
| `websocket.py:48` | `WS_CLEANUP_FAILED` | warning | WS connection cleanup |
| `routine_service.py:463` | `ROUTINE_DEFENSIVE_GUARD` | info | routine defensive guard |
| `adapters/registry.py:93` | `PLUGIN_LOAD_FAILED` | info | best-effort plugin loading |

**Существующая инфраструктура:**
- `cod_doc/services/activity_service.py` — `emit/list/get` (proposal 09).
- `cod_doc/infra/models/traces.py` — `ToolTraceModel` (успешные tool calls).
- `cod_doc/services/run_context.py` — `run_scope` с contextvar `run_id`.

## Предложение

### 5.1. Два storage tier'а

**Tier 1: In-memory ring buffer для soft failures (degraded paths).**

```python
# cod_doc/services/degraded_audit.py
@dataclass(slots=True)
class DegradedPathRecord:
    code: str          # "EVENT_FLUSH_FAILED", "RUN_CONTEXT_DB_MISSING", ...
    location: str      # "cod_doc/services/event_bus.py:123"
    severity: str      # "info" | "warning"
    context: dict      # {project, run_id, agent_id, ...}
    ts: float

class DegradedPathAudit:
    """Process-local ring buffer (capacity 100) + counter by code.
    
    Потеряется при рестарте — это намеренно: это **runtime metric**,
    а не audit log. Не раздувает БД.
    """
    def record(self, code: str, location: str, **context) -> None: ...
    def stats(self) -> dict[str, int]: ...         # code → count (с накоплением)
    def recent(self, limit: int = 50) -> list[DegradedPathRecord]: ...
    def reset(self) -> None: ...                   # для тестов
```

**Tier 2: DB-таблица `error_audit` для hard exceptions.**

```sql
CREATE TABLE error_audit (
    row_id          INTEGER PRIMARY KEY,
    run_id          TEXT,                        -- FK to agent_run (proposal 04)
    project_id      INTEGER REFERENCES project(row_id),
    task_id         TEXT,                        -- nullable
    error_code      TEXT NOT NULL,               -- "TASK_NOT_FOUND", "TRANSITION_DENIED", "DB_DEGRADED"
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    message         TEXT NOT NULL,
    context_json    TEXT,                        -- JSON blob
    traceback       TEXT,                        -- stack trace, truncated to 4KB
    created         TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_error_audit_run_id ON error_audit(run_id);
CREATE INDEX idx_error_audit_project ON error_audit(project_id, created);
```

```python
# cod_doc/services/error_audit_service.py
class ErrorAuditService:
    def record(
        self,
        session: Session,
        *,
        error_code: str,
        severity: str,
        message: str,
        project_id: int | None = None,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
        traceback: str | None = None,
    ) -> ErrorAuditRecord: ...
```

### 5.2. Декораторы / context managers

**Soft (Tier 1):**
```python
@degraded_path("EVENT_FLUSH_FAILED")
def _flush_pending_events(session: Session) -> None: ...

@contextmanager
def degraded_path(code: str, *, severity: str = "warning", **context):
    """Catches exceptions, records degraded-path event, **re-raises**."""
    try:
        yield
    except Exception as exc:
        DegradedPathAudit().record(
            code=code,
            location=f"{__name__}:{sys._getframe(1).f_code.co_name}",
            severity=severity,
            error_type=type(exc).__name__,
            error_msg=str(exc),
            **context,
        )
        raise
```

**Hard (Tier 2):**
```python
@contextmanager
def audit_errors(
    session: Session,
    *,
    error_code_prefix: str = "SVC",
    project_id: int | None = None,
    task_id: str | None = None,
):
    """Wraps service call — catches exceptions, writes error_audit row, re-raises."""
    try:
        yield
    except Exception as exc:
        # traceback truncated to 4KB
        tb = traceback.format_exc()[:4096]
        ErrorAuditService.record(
            session, error_code=f"{error_code_prefix}_{type(exc).__name__}",
            severity="error", message=str(exc),
            project_id=project_id, task_id=task_id, traceback=tb,
        )
        raise
```

### 5.3. Применение к существующим точкам

**Tier 1 (degraded paths) — обернуть 8 мест:**

| File:line | Wrap as |
|---|---|
| `event_bus.py:123` | `@degraded_path("EVENT_FLUSH_FAILED")` |
| `event_bus.py:132` | `@degraded_path("EVENT_DROP_FAILED")` |
| `run_context.py:140` | `with degraded_path("RUN_CONTEXT_DB_MISSING", project_path=project_path):` |
| `run_context.py:179` | `with degraded_path("RUN_START_DEGRADED", run_id=run_id):` |
| `run_context.py:214` | `with degraded_path("RUN_FINALIZE_DEGRADED", run_id=run_id):` |
| `websocket.py:48` | `@degraded_path("WS_CLEANUP_FAILED")` |
| `routine_service.py:463` | `@degraded_path("ROUTINE_DEFENSIVE_GUARD", severity="info")` |
| `adapters/registry.py:93` | `@degraded_path("PLUGIN_LOAD_FAILED", severity="info")` |

**Tier 2 (hard exceptions) — обернуть service-слоевые raise:**

| File | Изменение |
|---|---|
| `task_service.py` (20+ мест) | `with audit_errors(session, error_code_prefix="TASK", project_id=..., task_id=...):` |
| `adr_service.py` | `with audit_errors(session, error_code_prefix="ADR"):` |
| `doc_service.py` | `with audit_errors(session, error_code_prefix="DOC"):` |
| `plan_service/` | `with audit_errors(session, error_code_prefix="PLAN"):` |
| `infra/repositories/base.py: get()/add()` | Wrap DB-raises |

### 5.4. MCP surface

```
# Tier 1 (in-memory, дешёвые вызовы)
degraded_audit_stats() -> {code: count}
degraded_audit_recent(limit=50, code?) -> list[DegradedPathRecord]
degraded_audit_reset() -> None  # admin only

# Tier 2 (DB-backed, для post-mortem)
error_audit_list(project?, run_id?, task_id?, severity?, limit=50, offset=0) -> list[ErrorAuditRecord]
error_audit_get(row_id) -> ErrorAuditRecord
error_audit_count_by_severity(project, since_days=7) -> {severity: count}
```

### 5.5. Web UI

- `/admin/health` — виджет с degraded_audit stats (counter + last trigger).
- `/p/<project>/errors` — таблица error_audit с filter-bar (по severity, error_code, task_id, дате).
- WebSocket event `error.occurred` при `severity='critical'`.

### 5.6. Activity events

Каждое degraded-path / hard-error срабатывание эмитит activity event:
```python
activity_service.emit(
    kind="degraded_path.triggered",  # или "error.occurred"
    project=project_id,
    payload={"code": code, "location": location, "severity": severity, "context": context},
    run_id=get_current_run_id(),
)
```

WebSocket-подписчики получают эти события в real-time → live UI показывает красный индикатор.

## Эффект

| Метрика | До | После |
|---|---|---|
| Видимость degraded path | 0 (silent) | Real-time counter + last 100 in-memory |
| Post-mortem через 3 месяца | Невозможно (нет persistence) | DB-таблица `error_audit` |
| MTTD (mean time to detect) | Часы (tail -f logs) | Минуты (`degraded_audit_stats()`) |
| CI-сигнал на регрессию в degraded path | Нет | Тесты на каждый из 8 кодов |
| WebSocket "stale state" debugging | Непонятно | `error_audit` показывает `EVENT_FLUSH_FAILED` |
| Покрытие кода (coverage) | `# pragma: no cover` × 8 | 100% (degraded paths тестируются) |
| Storage growth | Нулевой | Минимальный: in-memory ring buffer + 1 DB-таблица для hard errors |

## Зависимости

| Proposal / компонент | Нужно для |
|---|---|
| `04-run-id` (proposal 04) | `run_id` в contextvar, пробрасывается в context |
| `09-activity-log` (proposal 09) | `activity_service.emit(kind='error.occurred')` |
| `17-Living-Specification` (hackathon-track 17) | Drift detector опирается на degraded_audit stats |
| `event_bus._subscribers` dispose (из analysis) | Корректный shutdown, иначе ring buffer теряется странно |

## Структура

```
cod_doc/services/
├── degraded_audit.py                # Tier 1: in-memory ring buffer + decorator
├── error_audit_service.py           # Tier 2: DB-backed ErrorAuditService + audit_errors()
cod_doc/infra/models/
├── degraded_path.py                 # (опц.) DB-модель для degraded path, если решим в Tier 1 тоже persist
├── error_audit.py                   # SQLAlchemy модель
cod_doc/infra/migrations/versions/
└── <rev>_add_error_audit.py         # alembic
cod_doc/mcp/tools/
├── degraded_audit_tools.py          # MCP surface Tier 1
└── error_audit_tools.py             # MCP surface Tier 2
cod_doc/api/
└── routes_errors.py                 # /p/<project>/errors web page
cod_doc/templates/web/
└── errors.html                      # filter-bar table
tests/services/
├── test_degraded_audit.py           # 8 unit-тестов (по одному на каждый код)
├── test_error_audit_service.py      # commit-rollback изоляция
└── test_audit_errors_decorator.py   # проверка re-raise
```

## Риски и митигация

| Риск | Митигация |
|---|---|
| Ring buffer переполняется (capacity 100) при шторме | FIFO-вытеснение + счётчик `dropped: N` в stats |
| `audit_errors` сам упадёт при записи → recursive error | Обёртка в `try/except` с `logging.exception()` как last resort |
| `degraded_path` decorator маскирует bug (вместо fix — обёртка) | Комментарий в коде: «# degraded path: фиксировать срабатывание, потом чинить root cause» |
| DB-таблица `error_audit` раздувается | TTL-чистка: `DELETE FROM error_audit WHERE created < datetime('now', '-90 days')` через routine (proposal 07) |
| Web UI на `/admin/health` раскрывает security info | Через существующий `task_role` check; degraded_audit counter — open, hard errors — admin-only |
| Ложное чувство безопасности (тесты на degraded paths) | Тесты проверяют **срабатывание**, а не отсутствие. Если degraded path не нужен — удалить, а не тестировать. |

## Acceptance Criteria

### Tier 1 (in-memory)
- [ ] `cod_doc/services/degraded_audit.py` существует, покрыт unit-тестами.
- [ ] 8 мест с `pragma: no cover` обёрнуты в `@degraded_path(...)` или `with degraded_path(...):`.
- [ ] `tests/services/test_degraded_audit.py` — тест на каждый из 8 кодов.
- [ ] `degraded_audit_stats()` через MCP возвращает `{code: count}` после срабатывания.
- [ ] `degraded_audit_recent(limit=10)` возвращает последние 10 записей в FIFO порядке.
- [ ] Ring buffer capacity 100: 200 срабатываний → `dropped: 100` в stats.
- [ ] `activity_log` содержит `kind='degraded_path.triggered'` при срабатывании.
- [ ] WebSocket получает degraded_path event в real-time.

### Tier 2 (DB)
- [ ] `alembic upgrade head` создаёт таблицу `error_audit` с индексами.
- [ ] `ErrorAuditService.record()` пишет строку при вызове.
- [ ] `audit_errors(...)` context manager пишет строку + re-raises (caller всё ещё видит исключение).
- [ ] `error_audit_list(project, severity='critical')` возвращает корректный список.
- [ ] Web UI `/p/<project>/errors` показывает таблицу с фильтрацией.
- [ ] Тест на commit-rollback изоляцию: ошибка внутри транзакции → rollback → `error_audit` записалась в **отдельной** транзакции (не откатилась вместе).
- [ ] TTL-чистка через routine: `DELETE FROM error_audit WHERE created < now-90d`.

## Альтернативы

1. **Только logging (текущее состояние)** — решает «увидеть», но не «посчитать» и не «сохранить».
2. **Только DB-таблица (proposal pro)** — решает persistence, но раздувает БД на degraded paths, которые не стоят внимания.
3. **Только ring buffer (proposal mine)** — решает runtime-visibility, но не даёт post-mortem через 3 месяца.
4. **Sentry / APM** — внешняя зависимость, не подходит для self-hosted.
5. **Расширение activity_log (proposal 09)** — дешевле, но activity log не хранит traceback и не предназначен для долгосрочного хранения ошибок.

## Источники

- `cod_doc/services/event_bus.py:122-132` — deferred commit/rollback хуки.
- `cod_doc/services/run_context.py:140,179,214` — degraded paths.
- `cod_doc/services/task_service.py` (851 строка, 20+ raise) — service exceptions.
- `cod_doc/infra/models/traces.py` — `ToolTraceModel` (паттерн для SQLAlchemy model).
- `cod_doc/agent/adapters/base.py:115-137` — `@runtime_checkable Protocol` (паттерн для type-safe wrappers).
- 2026-06-04 self-improvement comparison (miniMax-m3 vs deepseek-v4-pro) — audit-отчёт `2026-06-04-self-improvement-compared.md`.
- Paperclip `wake-payload pattern` — паттерн «agent получает structured error context, не текстовый лог».
