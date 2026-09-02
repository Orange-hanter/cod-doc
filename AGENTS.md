# AGENTS.md — Гид для контрибьюторов (people & AI)

> **Кому это.** Любой автор PR'а в этом репо — человек или AI-агент. Прочесть до
> первого commit'а. Дополняет `MASTER.md` (что есть в проекте) ответом на «как
> с этим работать».

> ⚠️ **Cycle-5 implemented (2026-05-15, closed 2026-06-04 by AGN-001).** MCP API
> task-centric: agent profile экспонирует 6 тулов (`agent_pick`, `agent_report`,
> `agent_complete`, `agent_release`, `agent_get`, `agent_capabilities`).
> Bodies реализованы в `cod_doc/services/agent_service.py`; MCP-обёртки в
> `cod_doc/mcp/tools/agent_tools.py` (тонкий wrapper-слой, не stubs). Покрытие:
> `tests/services/test_agent_pick.py`, `tests/services/test_agent_workflow.py`,
> `tests/services/test_agent_profile_contract.py`. 112-tool CRUD surface
> (`task_*`, `doc_*`, `plan_*`, …) остаётся для `--profile standard|full`
> (admin / CLI / web). Tracked в plan
> `paperclip-adoption-task-plan` section H. Новые agent-features → секция H,
> не plan_create-style расширения internal surface.

## 1. Цель проекта

COD-DOC — система управления документацией с MCP-интеграцией: docs, tasks,
plans, stories, links, revisions — все живут в SQLite + Markdown проекций.

Текущая итерация — Phase 3 paperclip-adoption (Section C — atomic checkout,
routines, 7-state TaskStatus, AGENTS.md). См. `MASTER.md` → Project Status.

## 2. Прочесть в первую очередь

1. [`MASTER.md`](MASTER.md) — карта проекта, Quick Actions, навигация.
2. [`docs/system/MASTER.md`](docs/system/MASTER.md) — index системной документации.
3. [`docs/system/ARCHITECTURE.md`](docs/system/ARCHITECTURE.md) — слои и инварианты.
4. [`docs/system/DATA_MODEL.md`](docs/system/DATA_MODEL.md) — схема БД.
5. [`cod_doc/skills/orchestrator/SKILL.md`](cod_doc/skills/orchestrator/SKILL.md) —
   heartbeat-протокол агента (proposal 01).
6. Если работаешь с RFC — [`proposals/README.md`](proposals/README.md).

## 3. Карта репо

```
cod_doc/
├── agent/         # оркестратор + LLM, prompts, skills runtime
├── api/           # FastAPI + web pages + websocket
├── cli/           # click CLI
├── core/          # доменные модели и контракты (TaskStatus, EntityKind, …)
├── domain/        # entities (StrEnum + dataclasses)
├── infra/         # SQLAlchemy: models, migrations, repositories, sql helpers
├── mcp/           # MCP сервер + tools (один файл = одна tool-семья)
├── services/      # бизнес-логика — пишется в Python, тесты в tests/services/
├── skills/        # YAML-frontmatter Markdown инструкции для агента
└── tui/           # textual TUI (legacy)
proposals/         # RFC-проекты (numbered: 01-skills-layer.md, …)
docs/system/       # canonical system docs (audit/, capabilities/, roadmap/)
tests/             # pytest suites: services/ + mcp/ + api/ + agent/ + …
```

## 4. Dev setup

```bash
pip install -e .[dev]
alembic upgrade head            # init/upgrade local SQLite schema
pytest tests/ -v --tb=short     # run the suite
```

При первом старте задайте `COD_DOC_DB_URL` или используйте default
`sqlite:///./cod-doc.db`.

## 5. Core engineering rules

1. **Hash-verified docs.** Любое изменение `doc.body` → пересчёт sha →
   обновление `MASTER.md` секции с хэшами (через `update_master_hashes`).
2. **Snowball Protocol.** Грузить контекст по уровням L0/L1/L2 (см.
   `docs/system/capabilities/context-retrieval.md`).
3. **Атомарный checkout.** `todo → in_progress` только через
   `task_checkout` (proposal 06, PCA-200). **Enforce включён** (ADO-039,
   Phase 2): прямой `update_status(todo→in_progress)` бросает
   `StatusTransitionError` на всех поверхностях; web-форма идёт через
   `checkout_service.checkout`.
4. **Run-id на всех мутациях.** Внутри `run_scope(...)` все revisions /
   activity events / approvals тегаются `run_id` (proposal 04).
5. **Validate transitions.** `task_status_machine.validate_transition`
   вызывается в `task_service.update_status` — добавляешь новый статус →
   обнови `ALLOWED_TRANSITIONS`.
6. **Activity events на каждой мутации.** Любой новый MCP-write-tool
   эмитит `activity_service.emit(...)` в той же транзакции (proposal 09).
   Не покрытые сейчас тулы — Section F backlog (PCA-912).
7. **MCP-tool контракты.** Регистрация в `cod_doc/mcp/server.py`
   синхронно с реализацией; docstring идёт в `tools/list`. Для тестов —
   `tests/test_mcp.py::test_mcp_lists_tools` smoke-проверяет имена.
8. **MCP echo-without-persist gap.** При добавлении новых полей в
   `task_create` / `doc_create` — проверь, что они **персистятся** в
   связанных таблицах (dependency / story_link / affected_file), а не
   только эхо-возвращаются. См. `tests/services/test_task_create.py`
   (полное покрытие после PCA-936).
9. **MCP server profiles** (PCA-951, cycle-4 default-switch). Запуск:
   ```
   cod-doc-mcp                                # agent (default)
   cod-doc-mcp --profile minimal              # 21-tool cold-start
   cod-doc-mcp --profile full                 # все 116, включая legacy
   COD_DOC_PROFILE=full cod-doc-mcp           # через env
   ```
   - ``agent`` — **default**: 6 task-centric тулов для AI-агентов
     (`agent_pick`, `agent_report`, `agent_complete`, `agent_release`,
     `agent_get`, `agent_capabilities`).
   - ``minimal`` — 21-tool cold-start surface для свежих интеграций.
   - ``standard`` — 112 DB-backed тулов без legacy YAML.
   - ``full`` — все 116 тулов, включая legacy. Только для админ-сценариев
     и обратной совместимости с до-cycle-3 интеграциями.
   Counts зафиксированы тестом
   `tests/test_server_profiles.py::test_profile_counts_match_documented_values` —
   при добавлении/удалении тула обнови числа там и здесь.
   См. `cod_doc/mcp/profiles.py`.

## 6. DB schema change workflow

1. Edit модель в `cod_doc/infra/models/<file>.py`.
2. Создать миграцию: `alembic revision -m "<name>"` →
   `cod_doc/infra/migrations/versions/<rev>.py`.
3. Заполнить `upgrade()` + `downgrade()` (обязательно симметрично).
4. `alembic upgrade head` локально + `alembic downgrade -1` smoke-test.
5. Если меняется enum / domain — обнови `cod_doc/domain/entities.py`.
6. Тесты: `tests/services/conftest.py::engine_with_schema` автоматом
   подхватит новую миграцию.

## 7. Verification before hand-off

```bash
ruff check cod_doc/ tests/
ruff format --check cod_doc/ tests/
mypy cod_doc/
pytest tests/ --tb=short --timeout=120
```

Всё зелёное → готов PR. Если что-то не запускалось — явно отметь в
PR-описании «not run, because <reason>».

Политика качества (запрет голого `Any`, магических чисел, потолок сложности,
ratchet существующего долга) — в
[`docs/system/standards/code-quality.md`](docs/system/standards/code-quality.md);
конфигурация-истина — `pyproject.toml`.

## 8. Validation pattern

Из memory project (validation_pattern.md):

- **Структурная** — `validate_*()` raise. FM-002, FM-003 escalate
  через `approval_request(approval_type='fm_escalation', ...)` (PCA-121).
- **Advisory** — `audit_*()` collect issues. FM-004, FM-005 пишутся в
  audit-report или activity_log, не блокируют.

## 9. Audit cadence

- **Закрытая секция плана** (например, Section A → Section B → Section C)
  → `docs/system/audit/<date>-section-<X>-<name>.md` с TL;DR / deliverables /
  findings / acceptance / next step.
- **Открытие новой фазы** → kickoff brief в `docs/system/roadmap/`.
- **N циклов аудита подряд** на одном направлении → ровно N audit-отчётов
  с findings F1/F2/...; findings → backlog (Section F) в следующем цикле.

## 10. PR requirements

Шаблон — `.github/PULL_REQUEST_TEMPLATE.md`. Обязательные поля:

- **Что изменено** — bullet list
- **Зачем** — мотивация (ссылка на task / RFC)
- **Как проверить** — шаги
- **Риски** — что может сломаться
- **Model used** — модель / автор (или `human-authored`)
- **Checklist** — все пункты Definition of Done

## 11. Definition of Done

- [ ] Поведение соответствует acceptance criterion'у задачи или RFC.
- [ ] `ruff`, `mypy`, `pytest` зелёные локально.
- [ ] Контракты синхронизированы (модель ↔ migration ↔ MCP ↔ docs).
- [ ] Если изменение видимо в UI — приложен скриншот / описание.
- [ ] Activity events эмитятся при write-операциях (proposal 09 / PCA-912).
- [ ] Закрытие задачи в БД через `task_complete` или `task_update_status`.
- [ ] Если закрыта секция плана — audit-report в `docs/system/audit/`.

## 12. Скиллы для агента

Файлы под `cod_doc/skills/<name>/SKILL.md` — единый источник инструкций для
LLM. Frontmatter `name: …` + `description: …` определяет, когда скилл
активируется matcher'ом (см. `skill_matcher.py`). Пишешь новое поведение
агента → новый скилл (или обнови существующий), не правь системный prompt.
