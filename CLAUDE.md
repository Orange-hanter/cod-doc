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

.venv/bin/pytest tests/ -n auto --dist loadfile -q --tb=short   # весь прогон (~2090 тестов)
.venv/bin/pytest tests/services/test_task_create.py -q      # один модуль
.venv/bin/pytest tests/services/test_task_create.py::test_create_auto_generates_task_id -v   # один тест
.venv/bin/pytest tests/ -k "checkout" -q                    # по подстроке
```

`-n auto --dist loadfile` — только для полного прогона, ровно как в CI.
Распараллеливание по файлам, а не дефолтное `load` по отдельным тестам: тесты
делят внутрипроцессные глобалы (каталог тулов `mcp._tool_manager._tools`,
process-wide состояние API в autouse-фикстуре), и файл целиком на одном
воркере сохраняет ту же последовательность, что и обычный прогон; разница по
времени с `load` — в пределах 8%.

Флаги нарочно **не** в `addopts`: под воркерами не работают `-s` и `--pdb`, а
на одном модуле накладные расходы на их старт больше выигрыша. Отлаживаешь
конкретный тест — зови pytest без `-n`.

Gate перед hand-off — ровно то, что гоняет CI (`.github/workflows/ci.yml`),
всё блокирующее:

```bash
.venv/bin/ruff check cod_doc/ tests/
.venv/bin/ruff format --check cod_doc/ tests/
.venv/bin/mypy cod_doc/                  # strict
.venv/bin/pytest tests/ -n auto --dist loadfile --tb=short --timeout=120
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
cod-doc doc tree show -p cod-doc          # разделы дерева документации + Инбокс
cod-doc doc tree classify -p cod-doc      # сухая раскладка по правилам; --apply записывает
cod-doc doc tree health -p cod-doc        # пробелы в наполненности разделов; --sync пишет findings
cod-doc ctx docs|drift|search --json     # контекст для промпта в JSON (ctx docs --include-body — с телом)
cod-doc ctx next -p cod-doc --json       # doc card куратора: очередь «что чинить» (зеркало MCP curator_next)
cod-doc ingest ai_review -p cod-doc --from-pr 123   # findings из артефакта PR через gh; далее finding_promote
cod-doc ctx drift -p orakul --pr 562 --comment      # drift-гейт PR: находки → идемпотентный комментарий (--dry-run для проверки)
cod-doc completion zsh                   # печатает готовый _cod-doc; установка — scripts/install-zsh-completion.sh
```

Zsh-дополнение (`docs/zsh-completion.md`): артефакт
`cod_doc/cli/completion/_cod-doc` **генерируется** из click-дерева
(`python -m cod_doc.cli.completion --write`) и коммитится. Правил CLI —
регенерируй, иначе падает `tests/cli/test_zsh_completion_drift.py`.
Значения (слаги проектов, task_id, doc_key, plan.scope…) берутся напрямую из
`~/.cod-doc/config.yaml` и read-only SQLite: звать из дополнения сам `cod-doc`
нельзя: даже после ADO-179 `--help` стоит ~180 мс против ~20 мс у прямого
чтения SQLite, а на нажатие TAB это разница между «мгновенно» и «заметно».

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
  интерфейс. Прямых SQL-запросов из presentation нет. Для мутаций задач,
  историй и документов это правило машинно проверяется (ADO-067, ADO-159,
  STO-017): сканер `tests/services/_surface_parity.py` находит write-функции
  по AST и требует вызова из `cod_doc/mcp/` и `cod_doc/cli/`; его зовут
  `test_task_mutation_surface_parity.py` (`task_service`, `story_service/`) и
  `test_doc_mutation_surface_parity.py` (`doc_service`). Ловится отсутствие
  функции на поверхности, но **не** расхождение сигнатур: одноимённый тул с
  другим набором параметров тест пройдёт.
- **Резолв БД** (`infra/db.py::resolve_db_url`): explicit override → env
  `COD_DOC_DB_URL` → embedded `<project_root>/.cod-doc/state.db`. Реестр
  проектов — `~/.cod-doc/config.yaml` (переопределяется `COD_DOC_HOME`),
  парсинг закэширован по (mtime, size).
- **MCP: один файл = одна семья тулов.** `mcp/tools/*_tools.py` экспортируют
  `register(mcp)`; `mcp/server.py` вызывает их в цикле, затем `apply_profile()`
  **фильтрует уже зарегистрированный** каталог (`mcp/profiles.py`). Профиль
  `agent` — **дефолтный**, 6 curator-тулов (RFC 25 §3.2/§3.5,
  CUR-007/008/016): `agent_capabilities`, `curator_next`, `ctx_search`,
  `ctx_drift`, `context_get`, `agent_report`. Роль оркестратора — куратор
  документации и поиска, не исполнитель задач; вход в работу —
  `curator_next(project=...)`: дрейф, битые ссылки, протухшие хэши MASTER.md
  и открытые findings одной очередью с готовой командой на каждый пункт
  (`services/curator_service.py`, зеркало CLI — `cod-doc ctx next`).
  `agent_capabilities()` отдаёт `role: "doc-curator"` и
  `forbidden: [agent_pick, task_checkout, task_complete]`. Старые
  task-centric тулы (`agent_pick`, `agent_get`, `agent_complete`,
  `agent_release`) остались зарегистрированы, но видны только на
  `standard`/`full` — для coding-агента. Дальше `minimal` 21 / `standard` 141
  / `full` 145.
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
- **Куратор видит неразобранный Инбокс.** `curator_service.next` отдаёт один
  пункт очереди на весь Инбокс (`doc_tree_classify`), а не по документу на
  строку, и ранжирует его ниже любой проблемы целостности: неразложенный
  документ находим поиском, сломанная ссылка — нет. Рутина `doc_unplaced` в
  `CHECK_CATALOG` делает то же самое находкой; на проекте без дерева она
  молчит. Роли разделов и grooming после хаотичного импорта — скилл
  `doc-structure`.
- **Дерево отвечает «где лежит», здоровье — «чего не написано».**
  `doc_node_health` считает пробелы детерминированно по «Критериям живого
  дерева» из скилла `doc-structure`: раздел пуст при `min_docs > 0`, ниже
  порога, без `intent`; доля самого частого типа выше 60%; от трёх одноимённых
  индексов в разделе. Пороги откалиброваны замером на живом корпусе — на нём
  правила молчат, срабатывают на вырожденном. Одна находка на раздел, а не на
  условие. `sync` пишет их в общую таблицу `finding` как `source="routine"` с
  `source_ref="doc_node_health"`; этот же `source_ref` — партиция
  автозакрытия, потому что упавшая проверка не вправе закрывать чужие находки.
  Дерева нет — не только не пишем, но и **не сверяем**: пустой набор
  отпечатков закрыл бы всё как «вылеченное».
- **Находки умеют закрываться и возвращаться.**
  `finding_service.reconcile_partition` закрывает открытые находки партиции,
  которых производитель больше не видит, и переоткрывает вернувшиеся. Без
  второго рецидив пропадал бы навсегда: `ingest_findings` на конфликте
  поднимает только `times_seen`, а `curator_next` фильтрует по `open`.
  `dismissed` и `promoted` терминальны — решения человека автоматика не
  отменяет. `resolved` не индексируется в FTS: закрытая находка не работа.
  **Закрытие с гистерезисом**: `close_after_misses` задаёт, сколько прогонов
  подряд находку должны не увидеть. Детерминированная партиция идёт с
  дефолтной единицей (правила не промахиваются), LLM-партиция просит два —
  вердикт субъективен и мигает, а без отсрочки каждый хвостовой вердикт давал
  бы пару событий `resolved`/`reopened` на прогон. Серия живёт в
  `finding.miss_streak`, пишет её только `reconcile_partition`, обнуляет
  `_set_status`; возврат отпечатка обнуляет серию целиком, а не уменьшает её.
  **Молчание производителя — не «вылечено».** Промпт требует вердикт на каждый
  раздел, но ответ приходит валидным и неполным, а раздел без вердикта
  выглядит в точности как вылеченный: отпечатка нет ни там, ни там.
  `unjudged_fingerprints` выводит из сверки то, о чём прогон судить не смог;
  остальное не трогается — ни закрытия, ни счётчика. Частично полезный прогон
  при этом не пропадает: разделы с вердиктом сверяются как обычно. Список
  именно **запретный**: производитель знает, о чём промолчал, но не знает,
  какие ещё находки лежат в партиции, а находку о выпавшем из промпта разделе
  закрывать как раз законно — её предмет исчез. Мягче, чем `can_close` из
  `structure_drift`, и точнее: там сигнал про весь прогон, здесь про каждую
  находку.
- **Дерево документации — данные, не вёрстка** (ADO-116). Разделы живут в
  `doc_node` (паттерн `plan_section`/`story_section` плюс `parent_id` и
  `intent`), документ ссылается на раздел через `document.node_id`. Правила
  раскладки — `services/doc_taxonomy.py`: конъюнкция условий внутри правила,
  побеждает первое совпадение. Это граница с `nav_service._JOURNEY`, где
  `type_ok or pat_ok` загоняет все `module-spec` в шаг «Data Model». Что
  правилам не подошло, лежит в Инбоксе — `node_id IS NULL`, единственное
  представление состояния «не разложен»; привязка к разделу-инбоксу
  нормализуется в NULL. Раскладка не двигает `document.last_updated`: это
  отметка о свежести содержимого, и `classify --apply` обнулил бы её всему
  корпусу разом.
- **Миграции, трогающие `document`, — без `batch_alter_table`.** На SQLite
  batch пересоздаёт таблицу, а DROP старой уносит по CASCADE все `section` и
  висящие на них `link`. Проверено: 1379 секций и 841 ссылка. На пустой
  тестовой БД такая миграция зеленеет.
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
| `test_server_profiles.py` | counts профилей (6/21/141/145) в коде и доках совпадают |
| `test_actor_kind_single_source.py` | `actor_kind` выводится только через `domain.entities.actor_kind_for_author` (ADR-012) |
| `infra/test_totals_status_aliases.py` | `section_totals`/`plan_totals`/`ready_tasks` перечисляют все написания статуса из `TASK_STATUS_ALIASES` (миграция 0035) |
| `infra/test_task_status_canonicalisation_migration.py` | бэкфилл 0037 сводит легаси-написания в канон, ready-множество при этом не гаснет |
| `services/test_task_status_write_canonicalisation.py` | ни один write-путь не пишет легаси-написание статуса в `task.status` |
| `services/test_services_layering.py`, `api/test_web_layer_imports.py` | слои не импортируют вверх |
| `services/test_activity_write_path.py` | каждый write-сервис эмитит activity event |
| `services/test_task_mutation_surface_parity.py` | мутация в `task_service` и `story_service/` выставлена и в MCP, и в CLI (allowlist с обоснованиями внутри) |
| `services/test_doc_mutation_surface_parity.py` | то же для `doc_service` (STO-017) и `doc_tree_service` (ADO-116); незакрытый долг — `update_status` и `delete`, каждый с обоснованием |
| `services/test_migration_0035_preserves_data.py` | миграция не теряет секции и ссылки: наливает данные на предыдущей ревизии, потом гонит upgrade. На пустой БД такая потеря не видна |
| `cli/test_zsh_completion_drift.py` | `_cod-doc` = живое click-дерево; новая команда роняет CI до регенерации |
| `cli/test_zsh_completion_queries.py` | SQL дополнения выполняется на свежей схеме (ловит переименование колонки) |
| `cli/test_zsh_completion_runtime.py` | prelude в настоящем zsh: WAL-БД без `-shm`, Postgres-проект, нет файла — молчат, а не шумят |
| `cli/test_cli_startup_is_light.py` | `import cod_doc.cli` не тянет SQLAlchemy/Alembic; импорты `infra`/`services` живут в телах команд (ADO-179) |
| `cli/test_json_output_is_parseable.py` | `--json` печатается через `click.echo`, а не rich: иначе перенос и разметка молча портят значения (ADO-176) |

## Тестовые фикстуры

- `tests/conftest.py` — autouse-изоляция: подменяет `COD_DOC_HOME` на tmp,
  глушит workspace-discovery, сбрасывает process-wide API state между кейсами.
- `tests/services/conftest.py::engine_with_schema` — прогоняет
  `alembic upgrade head` в tmp SQLite, поэтому новая миграция подхватывается
  автоматически, без правки фикстур.
- `tests/_alembic.py::run_alembic` — единственная точка запуска alembic из
  тестов. `upgrade head` по ещё не существующему файлу SQLite обслуживается
  **копией шаблона**: настоящий alembic гоняется один раз за процесс, дальше
  `shutil.copyfile` (~1 мс вместо ~0.5 с). Один этот кэш срезал
  последовательный прогон с 644 с до 78 с; вместе с `-n auto --dist loadfile`
  (см. §«Команды») — ~60 с локально и 124 с на джобе `Test py3.13` против
  934 с до обеих правок.
  Кэш инвалидируется по размеру/mtime файлов
  `cod_doc/infra/migrations/versions/*.py`. Всё остальное — конкретная ревизия,
  `downgrade`, non-SQLite URL, уже существующий файл (миграционные тесты
  наливают данные на старой ревизии, потом гонят upgrade) — идёт в подпроцесс,
  как раньше. Пишешь фикстуру со схемой — зови `run_alembic`, а не
  `subprocess.run` напрямую.
- `asyncio_mode = "auto"` — async-тесты не требуют маркера.

## Инструментарий сессии

- MCP-сервер `cod-doc` — **один постоянный HTTP-демон на машину**, а не
  субпроцесс на сессию (ADO-171). `com.cod-doc.mcp` на `127.0.0.1:8801`
  (профиль `standard`, 141 тулов `task_*`/`doc_*`/`plan_*`/…) и
  `com.cod-doc.mcp-agent` на `:8802` (профиль `agent`, 6 curator-тулов —
  `curator_next`/`ctx_*`/`context_get`/`agent_capabilities`/`agent_report`).
  Тем же launchd и тем же рантаймом живёт веб-UI — `com.cod-doc.web`. Управление и
  доставка ревизий — `deploy/launchd/cod-doc-services.sh upgrade`
  (собирает `origin/main` свежим venv, свапает, перезапускает; откат —
  `rollback`). Предпочитай тулы ad-hoc Python-скриптам.
  **`project` обязателен в каждом DB-туле:** демон общий для всех харнессов,
  поэтому дефолтного проекта у него нет вовсе, а `set_default_project`
  отказывает (`mcp/tools/_workspace.py`). Под stdio поведение прежнее.
  Профиль задаётся портом, не флагом клиента; бинарь — пиннованная
  non-editable сборка в `~/.cod-doc/runtime`, чтобы грязное рабочее дерево
  не роняло все харнессы разом.
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
