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

.venv/bin/pytest tests/ -q --tb=short                       # весь прогон (~1639 тестов)
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

**«Гейт зелёный» = зелёный CI, а не локальный прогон** (ADO-070). Гейты
расходятся в обе стороны: CI не был зелёным ни разу с 2026-05-06 по 2026-09-03,
пока в DoD спринтов M1…M4 стоял «гейты зелёные» по прогону с ноутбука.
Проверяй `gh run list --branch main`. Правки, зависящие от окружения
(subprocess, пути, версии библиотек), прогоняй на свежем venv **до** пуша:

```bash
uv venv --python 3.12 /tmp/ci-repro
uv pip install --python /tmp/ci-repro/bin/python '.[dev]'
uvx ruff@latest check cod_doc/ tests/   # CI ставит свежий ruff, локальный venv отстаёт
```

Запуск поверхностей:

```bash
cod-doc --help                           # CLI (click); группы: task/plan/story/doc/link/revision/adr/project
cod-doc serve                            # REST API + web UI на :8765
cod-doc-mcp                              # MCP stdio; профиль по умолчанию agent (--profile / COD_DOC_PROFILE)
docker compose up -d                     # контейнер cod-doc, healthcheck /api/health
cod-doc doc drift --project cod-doc --all # дрейф БД ↔ markdown без перезаписи
cod-doc ctx docs|drift|search --json     # контекст для промпта в JSON (ctx docs --include-body — с телом)
cod-doc ingest ai_review -p cod-doc --from-pr 123   # findings из артефакта PR через gh; далее finding_promote
cod-doc ctx drift -p orakul --pr 562 --comment      # drift-гейт PR: находки → идемпотентный комментарий (--dry-run для проверки)
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
  интерфейс. Прямых SQL-запросов из presentation нет. Для мутаций задач это
  правило машинно проверяется (ADO-067):
  `tests/services/test_task_mutation_surface_parity.py` находит write-функции
  `task_service` по AST и требует вызова из `cod_doc/mcp/` и `cod_doc/cli/`.
- **Резолв БД** (`infra/db.py::resolve_db_url`): explicit override → env
  `COD_DOC_DB_URL` → embedded `<project_root>/.cod-doc/state.db`. Реестр
  проектов — `~/.cod-doc/config.yaml` (переопределяется `COD_DOC_HOME`),
  парсинг закэширован по (mtime, size).
- **MCP: один файл = одна семья тулов.** `mcp/tools/*_tools.py` экспортируют
  `register(mcp)`; `mcp/server.py` вызывает их в цикле, затем `apply_profile()`
  **фильтрует уже зарегистрированный** каталог (`mcp/profiles.py`). Профиль
  `agent` — **дефолтный**, 6 task-centric тулов, каждый возвращает
  самодостаточный payload; дальше `minimal` 21 / `standard` 112 / `full` 116.
  Счётчики зафиксированы тестом `test_server_profiles.py` и продублированы в
  ПЯТИ местах: `mcp/profiles.py` (docstring), `server.py --profile`,
  `AGENTS.md` §5.9, этот файл и `docs/mcp-integration.md` (строка семейства
  + ИТОГО) — меняешь набор тулов, правь все пять. Новые agent-фичи идут в `agent_*`, а не
  в расширение internal CRUD.
- **`mcp/tools/_db.py`** — общий вход в БД для тулов: `session_factory(project)`
  резолвит слаг (или workspace-default) → Config → engine. `project=None`
  падает с подсказкой, а не с None-ключом.
- **Слой services не смотрит вверх.** Ни одного импорта `cod_doc.mcp/api/cli/tui`
  из `services/` — общий код едет вниз (сериализаторы задач живут в
  `services/serializers.py`, `mcp/tools/_db.py` их только ре-экспортирует).
  Стережёт AST-гейт `tests/services/test_services_layering.py` (аналог
  `tests/api/test_web_layer_imports.py`).
- **Атомарный checkout — протокольное правило (ADO-039).** Переход
  `todo→in_progress` разрешён только через `task_checkout`: прямой
  `update_status` падает, если не передан `via_checkout=True`. `complete()`
  сам делает checkout-ногу (ADO-038), так что закрывать задачу из `todo`
  по-прежнему можно.
- **Write-path обязан оставлять след (ADO-040).** Мутирующие сервисы пишут
  revision и activity event одним атомарным вызовом
  `activity_service.write_revision_and_emit_event` (или `emit_for_write` там,
  где ревизии нет) внутри транзакции мутации; ошибки не глотаются, `actor_kind`
  выводится из `author`. Новый write-сервис без события — регресс, ловится
  `tests/services/test_activity_write_path.py`.
- **run_id — телеметрия, не контракт** (ADR-012). `run_scope`
  (`services/run_context.py`) открывает только встроенный оркестратор,
  которым не пользуются: на живой БД `revision` 2166/2166 и
  `activity_event` 1114/1114 с `run_id IS NULL`. Колонка оставлена
  nullable; не пиши код, рассчитывающий на её непустоту. Провенанс несёт
  `author` / `actor_id`, а роль выводится **только** через
  `domain.entities.actor_kind_for_author` — единственную точку вывода.
  Таблица `audit_log` удалена (миграция 0029), журнал write-операций —
  `activity_event`.
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
| `test_server_profiles.py` | counts профилей (6/21/112/116) в коде и доках совпадают |
| `test_actor_kind_single_source.py` | `actor_kind` выводится только через `domain.entities.actor_kind_for_author` (ADR-012) |
| `services/test_services_layering.py`, `api/test_web_layer_imports.py` | слои не импортируют вверх |
| `services/test_activity_write_path.py` | каждый write-сервис эмитит activity event |
| `services/test_task_mutation_surface_parity.py` | мутация задачи в `task_service` выставлена и в MCP, и в CLI (allowlist с обоснованиями внутри) |

## Тестовые фикстуры

- `tests/conftest.py` — autouse-изоляция: подменяет `COD_DOC_HOME` на tmp,
  глушит workspace-discovery, сбрасывает process-wide API state между кейсами.
- `tests/services/conftest.py::engine_with_schema` — прогоняет
  `alembic upgrade head` в tmp SQLite, поэтому новая миграция подхватывается
  автоматически, без правки фикстур.
- `asyncio_mode = "auto"` — async-тесты не требуют маркера.

## Инструментарий сессии

- MCP-сервер `cod-doc` (native stdio, `.mcp.json` явно ставит профиль
  `standard`, не дефолтный `agent`) — 112 тулов `task_*`/`doc_*`/`plan_*`/…;
  предпочитай их ad-hoc Python-скриптам.
- `/gate` — полный CI-гейт одной командой.
- Проектные скиллы `.claude/skills/`: `task-flow` (checkout → complete c sha,
  создание задач/секций, service-fallback), `doc-sync` (markdown ↔ БД,
  hash-реестр, drift-семантика).
- PostToolUse-хук напоминает про `doc import` после правки `.md` — это не шум,
  это закон репозитория.

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
