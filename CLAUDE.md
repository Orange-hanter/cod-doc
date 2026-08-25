# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

COD-DOC — MCP-сервер + автономный агент для управления проектной документацией.
Документы, задачи, планы, истории, ссылки и ревизии живут **в БД**
(`<project>/.cod-doc/state.db`), markdown — вторичная проекция.

Обязательное чтение перед первым коммитом: [`AGENTS.md`](AGENTS.md) — правила
контрибьюции (DB-workflow, Definition of Done, PR-требования, validation
pattern, audit cadence). Этот файл их не дублирует.
Навигация: [`MASTER.md`](MASTER.md) (L0) → [`docs/system/MASTER.md`](docs/system/MASTER.md)
(canonical) → [`docs/system/roadmap/ROADMAP.md`](docs/system/roadmap/ROADMAP.md) (приоритеты).

## Команды

Виртуальное окружение — `.venv/` в корне (тесты сами ищут `.venv/bin/alembic`).

```bash
pip install -e '.[dev]'
alembic upgrade head                     # схема локальной SQLite

.venv/bin/pytest tests/ -q --tb=short                       # весь прогон (~1350 тестов)
.venv/bin/pytest tests/services/test_task_create.py -q      # один модуль
.venv/bin/pytest tests/services/test_task_create.py::test_create_auto_generates_task_id -v   # один тест
.venv/bin/pytest tests/ -k "checkout" -q                    # по подстроке
```

Gate перед hand-off — ровно то, что гоняет CI (`.github/workflows/ci.yml`),
всё блокирующее:

```bash
.venv/bin/ruff check cod_doc/ tests/
.venv/bin/ruff format --check cod_doc/ tests/
.venv/bin/mypy cod_doc/                  # strict
.venv/bin/pytest tests/ --tb=short --timeout=120
```

Запуск поверхностей:

```bash
cod-doc --help                           # CLI (click); группы: task/plan/story/doc/link/revision/adr/project
cod-doc serve                            # REST API + web UI на :8765
cod-doc-mcp --profile agent              # MCP stdio; agent|minimal|standard|full (env COD_DOC_PROFILE)
docker compose up -d                     # контейнер cod-doc, healthcheck /api/health
cod-doc doc drift --project cod-doc --all # дрейф БД ↔ markdown без перезаписи
```

Миграции: `alembic revision -m "<name>"` → заполнить симметричные
`upgrade()`/`downgrade()` → `alembic upgrade head` + `alembic downgrade -1`
как smoke-тест.

Git-хуки (проверка формата гибридных ссылок `📁 … | 🗃️ … | 🔑 sha:…`):
`bash hooks/install.sh`.

## Архитектура

Слои строго однонаправленные, ни один не ссылается на слой выше:

```
cli/ tui/ api/ mcp/   → services/   → domain/   ← infra/
(presentation)          (бизнес-логика)  (dataclass+StrEnum)  (SQLAlchemy, миграции, репозитории)
```

Вещи, которые не видно из одного файла:

- **Четыре равные поверхности.** Новая функциональность в `services/` обязана
  появиться и в CLI, и в MCP — агент и человек должны иметь тождественный
  интерфейс. Прямых SQL-запросов из presentation нет.
- **Резолв БД** (`infra/db.py::resolve_db_url`): explicit override → env
  `COD_DOC_DB_URL` → embedded `<project_root>/.cod-doc/state.db`. Реестр
  проектов — `~/.cod-doc/config.yaml` (переопределяется `COD_DOC_HOME`),
  парсинг закэширован по (mtime, size).
- **MCP: один файл = одна семья тулов.** `mcp/tools/*_tools.py` экспортируют
  `register(mcp)`; `mcp/server.py` вызывает их в цикле, затем `apply_profile()`
  **фильтрует уже зарегистрированный** каталог (`mcp/profiles.py`). Профиль
  `agent` — 6 task-centric тулов, каждый возвращает самодостаточный payload;
  ~103 CRUD-тула остаются в `standard`/`full`. Новые agent-фичи идут в
  `agent_*`, а не в расширение internal CRUD.
- **`mcp/tools/_db.py`** — общий вход в БД для тулов: `session_factory(project)`
  резолвит слаг (или workspace-default) → Config → engine. `project=None`
  падает с подсказкой, а не с None-ключом.
- **run_id через contextvar** (`services/run_context.py::run_scope`): внутри
  скоупа все revisions / activity events / approvals штампуются `run_id`;
  вне — колонка NULL.
- **Статусы задач** — 7 канонических bucket'ов плюс legacy-алиасы
  (`pending`≡`todo`, `in-progress`≡`in_progress`), нормализация и
  `ALLOWED_TRANSITIONS` в `services/task_status_machine.py`. Единственный
  источник истины; docstring'и и скиллы проверяются тестом на соответствие.
- **Снежный ком контекста** (`services/context_service.py`): L0 = только
  MASTER, L1 = + прямые связи, L2 = + цепочки зависимостей. Возвращает JSON +
  markdown-выдержки под token budget.
- **Скиллы** — `cod_doc/skills/<name>/SKILL.md` (YAML-frontmatter + Markdown),
  подбираются `agent/skill_matcher.py`. Новое поведение агента → новый/правленый
  скилл, **не** правка системного промпта.
- **Проекция markdown** — артефакт, не исходник: `Document.projection_hash`
  ловит edit-in-place (`cod-doc doc drift`), а `MASTER.md` держит отдельный
  реестр хэшей файлов — пересчёт через `cod-doc hash update`
  (`core/hash_calc.py::update_hashes`). Правил `doc.body` — обнови реестр.

## Anti-drift тесты

В `tests/` есть мета-тесты, которые падают при рассинхроне кода и документации —
если правишь одну сторону, правь обе:

| Тест | Что стережёт |
|---|---|
| `test_mcp.py::test_mcp_lists_tools` | имена тулов в каталоге |
| `test_tool_naming_style.py` | только `name="snake_case"`, никаких `name="doc.list"` |
| `test_task_status_docstring_alignment.py` | docstring'и task-тулов и скилл `task-standard` перечисляют все 7 статусов |
| `test_orchestrator_skill_refs.py` | orchestrator SKILL.md не зовёт несуществующие тулы |
| `test_mcp_integration_doc.py` | числа в `docs/mcp-integration.md` = реальный `len(list_tools())` |
| `test_web_routes_audit.py` | живые web-роуты задокументированы |

## Тестовые фикстуры

- `tests/conftest.py` — autouse-изоляция: подменяет `COD_DOC_HOME` на tmp,
  глушит workspace-discovery, сбрасывает process-wide API state между кейсами.
- `tests/services/conftest.py::engine_with_schema` — прогоняет
  `alembic upgrade head` в tmp SQLite, поэтому новая миграция подхватывается
  автоматически, без правки фикстур.
- `asyncio_mode = "auto"` — async-тесты не требуют маркера.

## Конвенции

- **Русский текст в коде — норма.** `RUF001/002/003` и `E501` отключены для
  `cod_doc/**` и `tests/**` именно поэтому; не «чини» кириллицу в докстрингах.
- ruff: line-length 100, `select = E,W,F,I,UP,B,SIM,TCH,RUF` + политика
  качества `ANN,C901,PLR2004,RET,PERF,PTH` (голый `Any` запрещён, магические
  числа запрещены, сложность ≤15); mypy `strict` + `warn_unreachable` +
  `disallow_any_unimported`. Существующий долг — в ratchet-списке
  `per-file-ignores` (может только уменьшаться); правила и обоснования —
  `docs/system/standards/code-quality.md`.
  FastAPI/Pydantic/SQLAlchemy-типы намеренно живут вне `TYPE_CHECKING`
  (см. `runtime-evaluated-*` в `pyproject.toml`).
- Прогоняя тесты из окрашенного терминала — `env -u FORCE_COLOR`: rich красит
  вывод CLI в `CliRunner`, строковые ассерты падают на ANSI-кодах.
- Коммиты — conventional + ID задачи: `feat(flow): STB-014 (COD-052) — …`.
- Закрытие задачи — записью в БД (`task_complete` / `task_update_status`),
  не только правкой markdown. Закрытие секции плана → audit-отчёт в
  `docs/system/audit/`.
