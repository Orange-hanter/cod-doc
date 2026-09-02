# 17 — Living Specification: ADR ↔ Tasks ↔ Code ↔ Docs drift detector

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: 07-routines, ADR-система (a73dcbb), OBI code-ref
> · **Примечание (2026-09-02):** внешняя часть (cross-repo structure/scenario
> contour) переносится в [proposal 24](24-structure-contracts-scenarios.md);
> routine `adr_drift` в cod-doc остаётся complementary.

## Контекст: дрейф между «как должно быть» и «как есть»

В cod-doc уже есть **3 уровня спецификации**:
1. **ADR** (`adr_*` тулы) — фиксированное архитектурное решение (Context / Decision / Alternatives / Consequences).
2. **Plan + Tasks** (`plan_*`, `task_*`) — декомпозиция работы.
3. **Code** — реальная имплементация.

Текущая боль: **никто не проверяет, что эти три уровня согласованы**. Типичные drift'ы:
- ADR-001 говорит «используем Snowball Protocol для контекста» — а половина `agent_*-tools` читают весь MASTER.md без level-filtering.
- Task `COD-456` помечена как `acceptance: обновить docs/system/DATA_MODEL.md` — а через месяц после `done` документация устарела.
- ADR-005 объявлен superseded, но в коде всё ещё используется старая абстракция.

## Текущее состояние cod-doc

- ADR-система (a73dcbb, ADR-001..008): web-страницы list/detail/new/graph, CLI, migrator, MCP tools (`adr_create`, `adr_supersede`, `adr_deprecate`, `adr_link_task`).
- 7-state TaskStatus (proposal 08), atomic checkout (proposal 06), run-id audit (proposal 04).
- Code-ref parser (OBI-020/021, OBI-030/040) — линкует файлы ↔ entities ↔ FTS5.
- `07-routines` (PCA-211) — cron-style health checks по расписанию.
- **Нет:** правила, которое **на постоянной основе** проверяет согласованность ADR ↔ задачи ↔ код ↔ docs.

## Предложение

Расширить routines (proposal 07) новым типом `routine.kind='adr_drift'`. Создать `cod_doc/services/adr_drift_detector.py`:

### 4.1. Что проверяется

| Проверка | Источник | Drift = |
|---|---|---|
| ADR-claim vs code | `adr_create` поле `claim_code_refs` vs `commit_link_service.search_by_adr` | В ADR-001 перечислены 3 модуля, которые обязаны использовать Snowball Protocol. Если новый `cod_doc/agent/*.py` не вызывает `context_get(level=...)` — drift. |
| ADR-superseded chain | `adr_supersede` linked ADR | Старый `ADR-NNN` объявлен superseded → новый `ADR-NNN`. Если в коде всё ещё импортируется модуль из старого ADR — drift. |
| Task acceptance vs doc actuality | `task.acceptance` vs `doc.body` sha | Task COD-789 acceptance: «обновить docs/system/DATA_MODEL.md». Если последний commit в этом файле был >7 дней назад, а task `done` — drift. |
| ADR-graph reachability | `adr_graph` API | ADR в статусе `ACCEPTED`, но не имеет ни одной linked task и ни одного `adr_link_task` за 30 дней → orphan. |
| Doc hash vs source | `update_master_hashes` (existing) | Уже работает; интегрировать с тем же routine. |

### 4.2. Severity

| Severity | Условие | Действие |
|---|---|---|
| 🔴 BLOCKER | ADR-superseded chain нарушена в коде | `approval_request(approval_type='adr_drift', severity='blocker', ...)` |
| 🟡 WARNING | Task done >7 дней, а doc не обновлён | `activity_log.emit(event='adr_drift_warning', ...)` + Telegram-оповещение (опц.) |
| 🟢 INFO | Orphan ADR (нет task за 30 дней) | `audit_*` собирает в `docs/system/audit/adr-drift-<date>.md` |

### 4.3. Routine registration

```python
# cod_doc/services/adr_drift_detector.py
class ADRSpec(Protocol):
    def check_claim_vs_code(self, adr: ADR) -> list[DriftIssue]: ...
    def check_superseded_chain(self) -> list[DriftIssue]: ...
    def check_task_acceptance(self) -> list[DriftIssue]: ...

# В routines:
routine_register(
    name="adr_drift_daily",
    schedule="0 6 * * *",  # каждый день в 6 утра
    handler="cod_doc.services.adr_drift_detector:run_daily",
    severity_threshold="warning",  # < этого severity молчит
)
```

### 4.4. MCP surface (опц.)

```
adr_drift_check(project?, since_days=7) -> list[DriftIssue]
adr_drift_register_check(spec_name, spec_body_yaml)  # кастомные проверки
```

## Эффект

- **Audit на автопилоте.** Раз в сутки — отчёт «что разъехалось между ADR и кодом».
- **Блокирующие drift'ы не проходят silently.** Если ADR-002 superseded, а в `agent_service.py` импортируется `LegacyAgentAdapter` — CI / pre-commit ловит.
- **Привязка ADR к живой документации.** Каждый ADR имеет evidence: «вот код, который его реализует; вот задачи, которые его закрыли; вот docs, которые его объясняют».

## Зависимости

| Proposal / компонент | Нужно для |
|---|---|
| `07-routines` (PCA-211) | инфраструктура cron-style health checks |
| ADR-система (a73dcbb) | источник ADR'ов и их статусов |
| `09-activity-log` (PCA-912) | куда писать `adr_drift_warning` |
| `12-approvals` (PCA-121) | канал для blocker'ов |
| OBI-020/021 (code-ref) | связь ADR ↔ файлы ↔ задачи |

## Структура

```
cod_doc/services/
├── adr_drift_detector.py        # core
├── drift_specs/                 # каталог spec'ов
│   ├── claim_vs_code.py
│   ├── superseded_chain.py
│   ├── task_acceptance.py
│   └── orphan_adr.py
tests/services/
└── test_adr_drift_detector.py
cod_doc/mcp/tools/
└── adr_drift_tools.py           # MCP surface (опц.)
```

## Риски и митигация

| Риск | Митигация |
|---|---|
| False positives в `claim_vs_code` | Spec'и пишутся вручную, не auto-extract из ADR-body. ADR-author явно перечисляет `claim_code_refs: ["cod_doc/agent/..."]` |
| Routine начинает тормозить проект (долгие git-операции) | Rate-limit: routine запускается не чаще 1 раза / 6 часов; ручной `adr_drift_check` для ad-hoc |
| Severity inflation (всё становится «blocker») | `severity_threshold` в routine; первые 2 недели — WARN, потом повышение |
| Drift между spec'ами и реальностью | spec — это код, покрывается pytest |

## Acceptance criteria

1. `adr_drift_daily` routine зарегистрирована, выполняется по расписанию, не падает на пустой БД.
2. `check_superseded_chain` ловит реальный тест-кейс (fixture: `ADR-NNN` superseded → `ADR-NNN`, код импортирует старую абстракцию).
3. `check_task_acceptance` пишет в `activity_log` для task'ов со status=done >7 дней.
4. MCP `adr_drift_check` возвращает структурированный список issues с severity.
5. `docs/system/audit/<date>-adr-drift.md` генерируется автоматом при `INFO`-severity issues >0.

## Альтернативы

- **Раз в неделю manual review всех ADR'ов** — не масштабируется, через месяц забиваешь.
- **Парсить ADR текст через LLM** — fragile, дорого, не deterministic.
- **Strict typed links (ADR-claim → function-name)** — уже частично есть через OBI; расширяем постепенно.

## Источники

- Реальная боль: cod-doc сам (`ADR-NNN` superseded → `ADR-NNN`, но `arch/architecture.md` до сих пор упоминает старую структуру).
- Paperclip [`routines/`](https://github.com/paperclipai/paperclip) — паттерн cron-style health checks.
