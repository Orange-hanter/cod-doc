---
type: execution-plan
scope: cod-doc-refactor-large-files
status: done
principle: fix-first
created: 2026-05-02
last_updated: 2026-06-05
source_of_truth:
  cod_doc_plan: docs/system/roadmap/cod-doc-task-plan.md
  task_plan_standard: docs/system/standards/task-plan.md
---

# Refactor: Large Files — Execution Plan

> Подготовительный план разбиения файлов > 400 строк на компактные модули, согласованные по логике. **Только декомпозиция** — public API сервисов/команд/маршрутов остаётся прежним; изменения видны как новые внутренние модули и `from … import …` в местах сборки.
>
> Цель — снизить когнитивную нагрузку и упростить параллельную работу: после рефакторинга каждый модуль ≤ ~350 строк, с чёткой темой ответственности.
>
> **Не входит в скоуп:** тесты остаются зелёными байт-в-байт, никакого изменения поведения, никаких новых фич, никакого «попутного» рефакторинга. Все правки за пределами разбиения вносятся отдельными задачами.

## Navigation

- [Task-plan стандарт](../standards/task-plan.md)
- [Implementation roadmap](cod-doc-task-plan.md)
- [Web frontend roadmap](web-frontend-task-plan.md)

## Progress Overview

| Section | Total | Done | Remaining | Status |
|:--------|------:|-----:|----------:|:-------|
| A: Web layer (FastAPI) | 2 | 0 | 2 | pending |
| B: Services | 5 | 0 | 5 | pending |
| C: Infra (ORM models) | 1 | 0 | 1 | pending |
| D: CLI (Click groups) | 3 | 0 | 3 | pending |
| E: MCP server | 1 | 0 | 1 | pending |
| F: TUI screens | 1 | 0 | 1 | pending |
| G: Static assets (CSS) | 1 | 0 | 1 | pending |
| H: Tests | 6 | 0 | 6 | pending |
| **TOTAL** | **20** | **20** | **0** | ✅ done |

> **Status reconciliation 2026-06-05** (см. [ROADMAP](ROADMAP.md)): таблица выше была устаревшим черновиком («0 done») — сверка с кодом показала, что декомпозиция **уже выполнена**: пакеты `cod_doc/services/{plan_service,link_service,story_service,validation,projection_service}/`, `cod_doc/infra/models/` (20 файлов), `cod_doc/cli/{doc,plan,story}/`, `cod_doc/api/web/{pages,fragments}/`, `cod_doc/tui/screens/wizard/`, CSS-split (`static/app.css` → `static/css/_*.css`). Остаточный low-value пункт (standalone frontmatter-parser COD-050 — логика встроена в `projection_service/_frontmatter.py`) не блокирует. План закрыт.

## Принципы декомпозиции

Применяются ко всем задачам ниже без повторений.

1. **Public API не меняется.** Имена сервис-функций, CLI-команд, FastAPI-маршрутов, MCP-инструментов и моделей ORM остаются на тех же import-путях. Допускается re-export из `__init__.py` или из исходного «фасадного» модуля.
2. **Файлы — по теме, не по размеру.** Сначала находим связки (parse / resolve / verify; CRUD vs graph queries; pages vs htmx fragments) — затем выносим в отдельный модуль. Цель ≤ 350 строк, но 200 — нормально, 400 — допустимо для модуля с одной плотной темой.
3. **Никаких циклических импортов.** Если выделение порождает цикл — пересмотреть границу или ввести `_internals.py` с общими хелперами.
4. **Тесты не двигаем.** В Section H разбиваются ТОЛЬКО уже большие тестовые модули, и только если у них есть естественная граница (по фиче / по сценарию). Структура fixture'ов сохраняется.
5. **Каждая задача — одна PR.** PR содержит ровно перенос кода + минимальные импорт-правки + (если нужно) re-export. Никаких исправлений типов, форматирования, вынужденных правок поведения. Если по пути обнаружен баг — отдельная задача.
6. **Verify-loop:** `pytest -q` + `ruff check` + `mypy` зелёные ДО и ПОСЛЕ задачи. Diff коммита читается за пять минут.

## Naming convention

`PREFIX = RFL` (refactor large files). Нумерация по секциям: A → 001-009, B → 010-019, C → 020-029, …

---

## Section A — Web layer (FastAPI)

### RFL-001 — Refactor: split `cod_doc/api/web/pages.py` (828 LOC)

**Type:** refactor   **Priority:** high   **Section:** A-Web-Layer

**Текущее состояние.** Один файл с маршрутами под все страницы: index, project overview, docs (list/show/import), tasks (list/show), plans (list/show), revisions log, settings (GET/POST). Внутри сидят два самостоятельных хелпера (`_masked_api_key`, `_preview`) и константы лимитов.

**Целевая структура.** Создать пакет `cod_doc/api/web/pages/` с инициализацией и подмодулями:

```
cod_doc/api/web/pages/
├── __init__.py        # router = APIRouter(); include sub-routers (или re-export)
├── index.py           # GET /  + INDEX_*_LIMIT
├── project.py         # GET /p/{slug}, POST /p/{slug}/init  + OVERVIEW_*_LIMIT, _preview
├── docs.py            # GET /p/{slug}/docs, GET /p/{slug}/docs/{key}, POST /docs/import
├── tasks.py           # GET /p/{slug}/tasks, GET /p/{slug}/tasks/{task_id}
├── plans.py           # GET /p/{slug}/plans, GET /p/{slug}/plans/{plan_id} + PLAN_READY_LIMIT
├── revisions.py       # GET /p/{slug}/revisions  + REVISIONS_PAGE_LIMIT
└── settings.py        # GET/POST /settings  + _masked_api_key
```

`__init__.py`: создаёт `router = APIRouter()` и подключает суб-роутеры через `router.include_router(...)`. Старый импорт `from cod_doc.api.web.pages import router` продолжает работать. Шаблоны (`templates/...`) и пути URL — без изменений.

**Acceptance.**
- [ ] Все исходные маршруты доступны на тех же URL и возвращают тот же HTML.
- [ ] Импорт `from cod_doc.api.web.pages import router` (как в [cod_doc/api/server.py](../../../cod_doc/api/server.py)) работает без правок.
- [ ] Каждый файл ≤ 250 LOC.
- [ ] `pytest tests/api/` зелёный, ручной smoke `curl /` / `/p/<slug>` / `/settings` совпадает с baseline.

---

### RFL-002 — Refactor: split `cod_doc/api/web/fragments.py` (511 LOC)

**Type:** refactor   **Priority:** high   **Section:** A-Web-Layer

**Текущее состояние.** Один файл, в котором перемешаны три независимых HTMX-сценария: смена статуса задачи, инлайн-патчинг секций документов, инлайн-редактирование полей задачи (description / acceptance), плюс «complete» из ready-блока. Общие хелперы (`_is_htmx`, `_render_*`) живут рядом.

**Целевая структура.** Пакет `cod_doc/api/web/fragments/`:

```
cod_doc/api/web/fragments/
├── __init__.py        # router + include_router
├── _shared.py         # _is_htmx, _render_task_row (общая для status/complete)
├── tasks_status.py    # POST /tasks/{id}/status, POST /tasks/{id}/complete
├── tasks_fields.py    # GET/POST /tasks/{id}/fields/{field}/...  + _TASK_FIELDS map
└── sections.py        # GET/POST /docs/{key}/sections/{anchor}/...  + _resolve_section
```

`_TASK_FIELDS` — приватный реестр, переезжает в `tasks_fields.py`. `_resolve_section` — в `sections.py`. Re-export `router` из `__init__.py`.

**Acceptance.**
- [ ] HTMX swap-таргеты возвращают тот же HTML (визуально + diff на байтах).
- [ ] Cookie-flash и OOB-alert поведение сохранено (особенно в `task_complete` с Referer-fallback).
- [ ] Каждый файл ≤ 220 LOC.

---

## Section B — Services

### RFL-010 — Refactor: split `cod_doc/services/plan_service.py` (742 LOC)

**Type:** refactor   **Priority:** high   **Section:** B-Services

**Текущее состояние.** В одном файле живут пять разнотипных подсистем: dataclass-DTO, `recalc`-агрегации (вьюшки), `ready`-выборка, `audit` + цикл-детектор (DFS), `export`-рендеры markdown, `forward/reverse/critical_path` через CTE. Файл уже размечен `── Internals/recalc/ready/audit/export/Graph queries ──` — границы есть, осталось материализовать.

**Целевая структура.** Превратить в пакет `cod_doc/services/plan/`:

```
cod_doc/services/plan/
├── __init__.py            # re-export всего публичного API (см. __all__ снизу)
├── _types.py              # DerivedStatus, SectionProgress, PlanProgress,
│                          # PlanAuditReport, ChainEntry, CriticalPathResult,
│                          # PlanNotFoundError, TaskNotFoundInPlanError
├── _internals.py          # _require_plan, _derive_status, _PRIORITY_ORDER
├── reads.py               # get_for_project, list_for_project, recalc, ready
├── audit.py               # audit + _find_cycles
├── export.py              # export + _render_progress_overview / _render_next_batch /
│                          # _render_dependency_graph / _mermaid_node_id
└── graph.py               # forward_chain, reverse_chain, critical_path + SQL CTE-константы
```

Обратная совместимость: модуль `cod_doc/services/plan_service.py` остаётся как тонкий фасад с `from cod_doc.services.plan import *  # noqa: F401,F403` — все вызовы `from cod_doc.services import plan_service as plans` продолжают работать.

**Acceptance.**
- [ ] `tests/services/test_plan_service.py` — без изменений, зелёный.
- [ ] Каждый модуль ≤ 250 LOC; `graph.py` может быть до 320 LOC из-за двух CTE.
- [ ] `from cod_doc.services import plan_service` и `from cod_doc.services.plan_service import recalc` работают.

---

### RFL-011 — Refactor: split `cod_doc/services/link_service.py` (723 LOC)

**Type:** refactor   **Priority:** high   **Section:** B-Services

**Текущее состояние.** Файл совмещает три независимых ответственности: чистый regex-парсер ссылок (parse + классификация wiki-inner), DB-bound resolve/verify (с шестью `_resolve_*` хелперами), rename-cascade (две системы перезаписи: канонические `[[doc:OLD]]` и markdown-relative с path_map).

**Целевая структура.** Пакет `cod_doc/services/link/`:

```
cod_doc/services/link/
├── __init__.py            # re-export public API
├── _types.py              # ParsedLink, VerifyReport, RenameCascadeReport,
│                          # LinkNotFoundError
├── parser.py              # PURE: parse, _strip_fenced_code, _href_to_doc_key,
│                          # _classify_wiki_inner + regex-константы
├── resolver.py            # _resolve_canonical/_resolve_section_anchor/
│                          # _resolve_task/_resolve_story/_resolve_wiki +
│                          # _apply_resolution + sync_section/resolve/resolve_section/
│                          # verify_section/list_for_section
├── _section_helpers.py    # _section_or_raise, _project_id_for_section,
│                          # _link_or_raise (внутренние, общие)
└── rename_cascade.py      # rename_cascade, _rewrite_canonical_refs,
                           # _rewrite_markdown_relative_refs, _resolve_md_href,
                           # _make_relative_href
```

Фасад: `cod_doc/services/link_service.py` → `from cod_doc.services.link import *  # noqa`.

**Acceptance.**
- [ ] `tests/services/test_link_service.py` (932 LOC, 35+ тестов) — зелёный без правок.
- [ ] `parser.py` НЕ импортирует SQLAlchemy (чистая функция — это инвариант, который мы хотим закрепить).
- [ ] Каждый модуль ≤ 280 LOC.

---

### RFL-012 — Refactor: split `cod_doc/services/story_service.py` (479 LOC)

**Type:** refactor   **Priority:** medium   **Section:** B-Services

**Текущее состояние.** Один файл, но темы уже отчётливо разделены пунктирами: create/get/list, update_status, acceptance-criteria, link, coverage. Плюс утилита `_validate_link_target` (важная — предотвращает broken links).

**Целевая структура.** Пакет `cod_doc/services/story/`:

```
cod_doc/services/story/
├── __init__.py            # re-export
├── _types.py              # CoverageStatus, StoryCoverage,
│                          # StoryNotFoundError, StoryAlreadyExistsError,
│                          # AcceptanceNotFoundError, BrokenLinkError
├── _internals.py          # _require_story, _diff
├── crud.py                # create, get, list_for_project, list_acceptance,
│                          # list_links, list_tasks, update_status
├── acceptance.py          # add_criterion, set_criterion_met
├── links.py               # link, _validate_link_target
└── coverage.py            # coverage
```

Файл `cod_doc/services/story_service.py` остаётся как фасад с re-export.

**Acceptance.**
- [ ] `tests/services/test_story_service.py` (605 LOC) — зелёный.
- [ ] Каждый модуль ≤ 200 LOC.

---

### RFL-013 — Refactor: split `cod_doc/services/projection_service.py` (404 LOC)

**Type:** refactor   **Priority:** low   **Section:** B-Services

**Файл на грани (404 LOC).** Темы уже разделены: render_markdown (pure, с redaction-логикой и audience-rank), export_document, detect_drift, import_document, плюс `_safe_target` (security-критический хелпер) и `_parse_frontmatter`/`_apply_frontmatter_to_model`.

**Решение:** разбиваем — потому что:
1. `_safe_target` — security-guard, должен быть в одном модуле с тестами на path-escape (см. `test_export_refuses_*` в [test_projection_service.py](../../../tests/services/test_projection_service.py)).
2. Audience-redaction (`_audience_blocks_sensitivity`, `_REDACTION_MARKER`) — отдельный концепт (COD-025/SD-002), у него свой жизненный цикл.

**Целевая структура.** Пакет `cod_doc/services/projection/`:

```
cod_doc/services/projection/
├── __init__.py            # re-export
├── _types.py              # DriftStatus, ExportResult, DriftReport, PathEscapeError
├── _safety.py             # _safe_target, _sha256
├── _frontmatter.py        # _frontmatter_dict, _render_frontmatter,
│                          # _parse_frontmatter, _apply_frontmatter_to_model
├── _redaction.py          # _audience_blocks_sensitivity, _REDACTION_MARKER
├── render.py              # render_markdown (uses _frontmatter + _redaction)
├── export.py              # export_document
├── drift.py               # detect_drift
└── import_doc.py          # import_document  (имя `import.py` запрещено — keyword)
```

**Acceptance.**
- [ ] `tests/services/test_projection_service.py` (411 LOC) — зелёный.
- [ ] Каждый модуль ≤ 130 LOC.
- [ ] `_safety.py` импортируется только из `export.py` / `drift.py`; парсер frontmatter не знает про путь к диску.

---

### RFL-014 — Refactor: split `cod_doc/services/validation.py` (402 LOC)

**Type:** refactor   **Priority:** low   **Section:** B-Services

**Файл на грани (402 LOC).** Внутри две явные категории: structural validators (raise `ValidationError`) и advisory validators (return `list[ValidationIssue]`). Согласно memory `validation_pattern.md` разделение по этой границе — основной паттерн проекта; материализация в коде усилит его.

**Целевая структура.** Пакет `cod_doc/services/validation/`:

```
cod_doc/services/validation/
├── __init__.py            # re-export всего, чтобы from cod_doc.services import validation сохранилось
├── _errors.py             # ValidationError, ValidationIssue
├── _patterns.py           # _TASK_ID_RE, _STORY_ID_RE, _SECTION_SLUG_RE,
│                          # _ID_PREFIX_RE, _VERB_PATTERNS, _FORBIDDEN_TYPE_ALIASES,
│                          # _FM007_REQUIRED_TYPES
├── structural.py          # validate_task_id / _id_prefix / _story_id /
│                          # _section_slug / _task_type / _doc_path
└── advisory.py            # audit_task_title, audit_frontmatter, audit_sensitivity
```

Все импорты вида `from cod_doc.services import validation` и `from cod_doc.services.validation import ValidationError` продолжают работать.

**Acceptance.**
- [ ] Все вызовы `validation.validate_*` / `validation.audit_*` в [doc_service](../../../cod_doc/services/doc_service.py), [story_service](../../../cod_doc/services/story_service.py), [task_service](../../../cod_doc/services/task_service.py) работают без правок импортов.
- [ ] Тесты на validation (если есть отдельные) и интеграционные тесты сервисов — зелёные.

---

## Section C — Infra (ORM models)

### RFL-020 — Refactor: split `cod_doc/infra/models.py` (521 LOC)

**Type:** refactor   **Priority:** medium   **Section:** C-Infra

**Текущее состояние.** Один файл с 17 SQLAlchemy-моделями. Они уже группируются по доменам, но визуально это «стена кода» — найти `LinkModel` или `RevisionModel` глазами сложно.

**Особенность:** SQLAlchemy чувствителен к порядку загрузки моделей (relationships ссылаются на классы по имени-строке, но `Base.metadata` должна знать обо всех таблицах ДО первого `create_all`). Поэтому `__init__.py` пакета **обязан импортировать все подмодули** для side-effect регистрации в `Base.metadata`.

**Целевая структура.** Пакет `cod_doc/infra/models/`:

```
cod_doc/infra/models/
├── __init__.py            # from .base import Base
│                          # from .project import ProjectModel
│                          # from .documents import DocumentModel, SectionModel, LinkModel
│                          # ...  (импорт ради регистрации + re-export)
├── base.py                # Base, _utcnow
├── project.py             # ProjectModel
├── documents.py           # DocumentModel, SectionModel, LinkModel
├── plans.py               # PlanModel, PlanSectionModel, TaskModel,
│                          # DependencyModel, AffectedFileModel
├── stories.py             # UserStoryModel, StoryAcceptanceModel, StoryLinkModel
├── modules.py             # ModuleModel, ModuleDependencyModel, ModuleCodeModel
├── revisions.py           # RevisionModel, AuditLogModel
└── tags.py                # TagModel, DocumentTagModel, TaskTagModel, StoryTagModel
```

`__init__.py` ре-экспортирует ВСЕ модели и сам `Base`. Импорт `from cod_doc.infra.models import DocumentModel` (как в [doc_service.py](../../../cod_doc/services/doc_service.py)) сохраняется.

**Особое внимание.**
- Alembic-миграции сравниваются с `Base.metadata.tables` — после рефакторинга `alembic check` (или `alembic revision --autogenerate --dry-run`) НЕ должен показывать diff.
- Поведение каскадов и string-based `relationship(...foreign_keys="DependencyModel.from_task_id")` не меняется (имена классов те же).

**Acceptance.**
- [ ] `pytest` зелёный (включая alembic-fixture тесты в `tests/services/`).
- [ ] `alembic check` ↔ `Base.metadata` — без diff.
- [ ] Каждый модуль ≤ 130 LOC.

---

## Section D — CLI (Click groups)

### RFL-030 — Refactor: split `cod_doc/cli/doc.py` (519 LOC)

**Type:** refactor   **Priority:** medium   **Section:** D-CLI

**Текущее состояние.** `@click.group()` с восемью командами: list, show, create, rename, body, export, drift, import. Каждая команда — самодостаточна, у группы общий префикс `--project` и три helper-функции (`_make_session`, `_require_project_id`, `_get_root_path`).

**Целевая структура.** Пакет `cod_doc/cli/doc/`:

```
cod_doc/cli/doc/
├── __init__.py            # group `doc` определяется здесь;
│                          # импортирует сub-команды для регистрации
├── _common.py             # _make_session, _require_project_id, _get_root_path,
│                          # _STATUS_ICON, _DRIFT_ICON
├── cmd_list.py            # @doc.command("list")
├── cmd_show.py            # @doc.command("show")
├── cmd_create.py          # @doc.command("create")
├── cmd_rename.py          # @doc.command("rename")
├── cmd_body.py            # @doc.command("body")
├── cmd_export.py          # @doc.command("export")
├── cmd_drift.py           # @doc.command("drift")
└── cmd_import.py          # @doc.command("import")
```

Паттерн регистрации — как у `cod_doc/mcp/tools/` (уже работающий precedent в кодовой базе).

`__init__.py`:
```python
import click
@click.group()
def doc() -> None: ...
from . import cmd_list, cmd_show, cmd_create, cmd_rename, cmd_body, cmd_export, cmd_drift, cmd_import  # noqa: E402, F401
```

Каждый `cmd_*.py` начинается с `from . import doc` (или `from cod_doc.cli.doc import doc`) и регистрируется через `@doc.command(...)`.

**Acceptance.**
- [ ] `cod-doc doc --help` показывает те же подкоманды.
- [ ] `cod-doc doc list -p X`, `cod-doc doc create ...`, `cod-doc doc drift ...` работают как раньше.
- [ ] Импорт `from cod_doc.cli.doc import doc` (где бы он ни был — через `entry_points` или через `cli/__main__.py`) сохраняется.
- [ ] Каждый файл команды ≤ 100 LOC.

---

### RFL-031 — Refactor: split `cod_doc/cli/story.py` (489 LOC)

**Type:** refactor   **Priority:** medium   **Section:** D-CLI

**Аналогично RFL-030.** Семь команд: list, show, create, status, add-criterion, link, coverage. `_STATUS_ICON`, `_COVERAGE_ICON`, `_make_session`, `_require_project_id` → `_common.py`.

**Целевая структура.** `cod_doc/cli/story/__init__.py` + `_common.py` + `cmd_<name>.py` × 7.

**Acceptance.** Аналогично RFL-030: команды и импорты работают, каждый `cmd_*.py` ≤ 100 LOC.

---

### RFL-032 — Refactor: split `cod_doc/cli/plan.py` (428 LOC)

**Type:** refactor   **Priority:** medium   **Section:** D-CLI

**Аналогично RFL-030.** Семь команд: show, ready, audit, export, critical-path, forward, reverse. Дополнительно — общий хелпер `_render_chain` (используется forward + reverse) → в `_common.py`.

**Целевая структура.** `cod_doc/cli/plan/__init__.py` + `_common.py` (с `_render_chain`) + `cmd_<name>.py` × 7.

**Acceptance.** Аналогично RFL-030.

---

## Section E — MCP server

### RFL-040 — Refactor: split `cod_doc/mcp/server.py` (583 LOC)

**Type:** refactor   **Priority:** medium   **Section:** E-MCP

**Текущее состояние.** Файл регистрирует ~18 inline-инструментов и три ресурса/три промпта на FastMCP-инстансе. При этом DB-tools уже вынесены в [cod_doc/mcp/tools/](../../../cod_doc/mcp/tools/) — но «легаси»-инструменты (project/task management, MASTER.md hashes, context delivery, agent orchestration, config, semantic search) остались в `server.py`.

**Целевая структура.** Доперенос в существующий пакет `cod_doc/mcp/tools/`:

```
cod_doc/mcp/
├── server.py              # ТОЛЬКО создание FastMCP-инстанса, регистрация всех tools/
│                          # пакетов, click main()  — ~80 LOC
└── tools/
    ├── _legacy_helpers.py # _config, _project, _project_summary  (внутр. utils)
    ├── project_tools.py   # NEW: list_projects, get_project_status, add_project, remove_project
    ├── task_tools_legacy.py # NEW: list_tasks, add_task, update_task, next_pending_task
    │                        #     (НЕ путать с существующим task_tools.py — DB-side)
    ├── master_tools.py    # NEW: get_master, update_master_hashes, check_stale_refs, generate_ref
    ├── context_tools.py   # NEW: read_context, read_file, list_files
    ├── hash_tools.py      # NEW: hash_file, verify_hash
    ├── search_tools.py    # NEW: search_docs, reindex
    ├── agent_tools.py     # NEW: run_agent_once, get_agent_context, clear_agent_context
    ├── config_tools.py    # NEW: check_config
    ├── resources.py       # NEW: cod-doc://config | projects | project/{name}/master | tasks
    ├── prompts.py         # NEW: doc_review, doc_plan, onboard_project
    └── (existing: _db.py, doc_tools.py, link_tools.py, plan_tools.py,
                  revision_tools.py, story_tools.py, task_tools.py)
```

Каждый новый модуль экспортирует `register(mcp)`-функцию (как существующий [doc_tools.py](../../../cod_doc/mcp/tools/doc_tools.py)). `server.py` сводится к:

```python
mcp = FastMCP("COD-DOC", json_response=True)
for mod in (project_tools, task_tools_legacy, master_tools, context_tools,
            hash_tools, search_tools, agent_tools, config_tools,
            doc_tools, task_tools, plan_tools, story_tools, link_tools,
            revision_tools, resources, prompts):
    mod.register(mcp)
```

**Внимание:** `task_tools.py` (DB-side) и `task_tools_legacy.py` (YAML-side, через `Project`) — РАЗНЫЕ инструменты, нельзя сливать. Подобрать имя получше — например, `project_tasks_tools.py` или `legacy/task_tools.py` (вложенная папка `tools/legacy/` если их в итоге много).

**Acceptance.**
- [ ] `cod-doc-mcp` (или эквивалентный entry-point) поднимается, список tools/resources/prompts через MCP `list_tools` совпадает с baseline (захватить до и после).
- [ ] `server.py` ≤ 100 LOC.
- [ ] Каждый новый `*_tools.py` ≤ 200 LOC.

---

## Section F — TUI screens

### RFL-050 — Refactor: split `cod_doc/tui/screens/wizard.py` (450 LOC)

**Type:** refactor   **Priority:** low   **Section:** F-TUI

**Текущее состояние.** Один экран Textual со встроенным `_StepBar`-виджетом, четырьмя шагами (welcome / API / project / done) и валидацией. ~165 LOC из 450 — это `DEFAULT_CSS` (стили).

**Целевая структура.**

```
cod_doc/tui/screens/wizard/
├── __init__.py            # re-export WizardScreen
├── screen.py              # class WizardScreen + compose() + on_mount()
├── _stepbar.py            # class _StepBar
├── _styles.py             # WIZARD_CSS (string constant)
├── _steps.py              # statelessные render-функции для каждого шага:
│                          # render_welcome(), render_api_step(), render_project_step(),
│                          # render_done_step()  — возвращают list[Widget]
├── _validation.py         # validate_and_save_api(config, ...),
│                          # validate_and_save_project(config, ...)
└── _models.py             # MODELS-list, STEPS-list, _model_widget_id()
```

`screen.py` импортирует `WIZARD_CSS` и присваивает `DEFAULT_CSS = WIZARD_CSS`. Метод `compose()` вызывает render-функции из `_steps.py`. Валидаторы изолированы от UI и тестируемы в unit-тестах (если они появятся).

**Acceptance.**
- [ ] `cod-doc tui` запускает wizard, четыре шага навигируются как раньше.
- [ ] Сохранение конфигурации (`config.save()`) выполняется в тех же точках.
- [ ] `screen.py` ≤ 180 LOC; `_steps.py` ≤ 130 LOC.

---

## Section G — Static assets (CSS)

### RFL-060 — Refactor: split `cod_doc/static/app.css` (768 LOC)

**Type:** refactor   **Priority:** low   **Section:** G-Static

**Текущее состояние.** Один монолитный CSS с уже размеченными комментариями-секциями: layout/topbar, grid/tables, tabs, cards, master-preview, doc viewer (split + sections-nav + section bodies), overview agg blocks, settings form, section inline edit (WEB-012), overview tightening, **task detail page** (≈ 350 LOC — самая большая зона), alerts, pagination. Ключевая проблема — навигация: «найти стили задачной hero-зоны» = scroll до line 370.

**Решение.** Раздробить на партиалы и собирать через CSS-import (или конкатенацией при сборке).

**Целевая структура.**

```
cod_doc/static/
├── app.css                # точка входа; @import-ит партиалы в правильном порядке:
│                          #   tokens → base → layout → components/* → pages/*
├── htmx.min.js
└── css/
    ├── tokens.css         # :root { --bg, --fg, --accent, ... }
    ├── base.css           # html, body, a, h1, h2, .mono, .muted, .warn
    ├── layout.css         # .topbar, .content, .crumbs, .tabs, .split, .grid
    ├── components/
    │   ├── cards.css      # .cards, .card, .card-label, .card-value
    │   ├── md-preview.css # .md-preview, .doc-meta, .master-preview, .master-body
    │   ├── overview.css   # .overview-grid, .overview-block + tightening overrides
    │   ├── sections.css   # .doc-section, .sections-nav, .section-edit-*
    │   ├── alerts.css     # .alert, OOB-styles
    │   ├── pagination.css # index-page pagination
    │   └── settings.css   # settings form
    └── pages/
        └── task-detail.css # task-hero, status-tone-*, prio-stripe-*, hero-meta, …
                            # (≈ 350 LOC, самая большая часть)
```

`app.css` после рефакторинга:
```css
@import "css/tokens.css";
@import "css/base.css";
@import "css/layout.css";
@import "css/components/cards.css";
@import "css/components/md-preview.css";
@import "css/components/overview.css";
@import "css/components/sections.css";
@import "css/components/alerts.css";
@import "css/components/pagination.css";
@import "css/components/settings.css";
@import "css/pages/task-detail.css";
```

**Альтернатива (если CSS @import создаёт лишние HTTP-роундтрипы):** оставить один `app.css`, но генерировать его конкатенацией в Makefile / build-step. На решение влияет факт раздачи статики FastAPI без HTTP/2 push — оценить нагрузочно отдельно.

**Внимание.**
- Селекторы и каскадный порядок ДОЛЖНЫ совпадать (порядок @import = порядок исходного файла, плюс «overview tightening» ПОСЛЕ исходного `.cards`/`.overview-grid`).
- Включить в `package_data` (см. [pyproject.toml](../../../pyproject.toml)) папку `css/**`.
- Проверить визуально все пять страниц через playwright-скрипты (`.cod-doc-pw-*.py` уже есть в репозитории) до и после.

**Acceptance.**
- [ ] Все страницы рендерятся идентично (скриншоты совпадают, baseline в `/tmp/cod-doc-playwright-shots/`).
- [ ] Каждый партиал ≤ 200 LOC; `pages/task-detail.css` допускается до 380 LOC.
- [ ] Раздача через `/static/css/*` работает (StaticFiles mount уже есть).

---

## Section H — Tests

> Тесты режутся только если у них есть **естественная сценарная граница**. Цель — НЕ уменьшить файл во что бы то ни стало, а сгруппировать тесты по поведению, чтобы при падении ясно, какая фича сломалась.
>
> Везде сохраняется паттерн `engine_with_schema` / `_run_alembic_upgrade` / `db_url` fixture — они переносятся в `tests/services/conftest.py` (если ещё не там) ДО разбиения. Это отдельная подзадача внутри RFL-070.

### RFL-070 — Refactor: extract shared fixtures into `tests/services/conftest.py`

**Type:** refactor   **Priority:** medium   **Section:** H-Tests

**Зачем.** Все тестовые модули в `tests/services/` повторяют:
```python
def _run_alembic_upgrade(db_url: str) -> None: ...
@pytest.fixture
def db_url(tmp_path: Path) -> str: ...
@pytest.fixture
def engine_with_schema(db_url: str): ...
```

(исторически одинаковые блоки жили в `test_doc_service.py`, `test_task_service.py`,
`test_plan_service.py`, `test_story_service.py`, `test_link_service.py`; после
разрезания см. текущие focused tests: [test_doc_create.py](../../../tests/services/test_doc_create.py),
[test_task_create.py](../../../tests/services/test_task_create.py),
[test_plan_recalc.py](../../../tests/services/test_plan_recalc.py),
[test_story_crud.py](../../../tests/services/test_story_crud.py),
[test_link_parser.py](../../../tests/services/test_link_parser.py),
[test_projection_service.py](../../../tests/services/test_projection_service.py)).

**Действие.** Создать (или дополнить) `tests/services/conftest.py` с этими тремя элементами. Удалить копии из шести модулей. ⚠️ ПРЕДВАРЯЕТ задачи RFL-071..RFL-075 — без неё каждое последующее разбиение раздувает дублирование.

**Acceptance.**
- [ ] `pytest tests/services/ -q` зелёный.
- [ ] В каждом из шести `test_*.py` фикстуры удалены, а тесты остаются на месте.
- [ ] Сокращение строк по каждому файлу ~25 LOC (= общий минус ~150 LOC до основного разбиения).

---

### RFL-071 — Refactor: split `tests/services/test_link_service.py` (932 LOC)

**Type:** refactor   **Priority:** medium   **Section:** H-Tests

**Существующая разметка** (см. `# ====` маркеры): parser tests / sync_section / resolve / verify / rename_cascade / rename_cascade with path_map / DocService↔link cascade.

**Целевая структура.**

```
tests/services/link/
├── __init__.py
├── _helpers.py              # _seed_project, _add_doc, _add_doc_with_path
├── test_parser.py           # block 1: чистые парсер-тесты (без БД)
├── test_sync_section.py     # block 2
├── test_resolve.py          # block 3
├── test_verify.py           # block 4
├── test_rename_cascade.py   # blocks 5+6 (key rename + path_map)
└── test_rename_cascade_integration.py  # block 7 (DocService rename → cascade)
```

**Acceptance.**
- [ ] `pytest tests/services/link/ -q` — то же количество тестов, тот же результат.
- [ ] Каждый файл ≤ 250 LOC.

---

### RFL-072 — Refactor: split `tests/services/test_story_service.py` (605 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Существующая разметка:** create / update_status / acceptance / link / coverage.

**Целевая структура.**

```
tests/services/story/
├── __init__.py
├── _helpers.py              # _seed_project, _seed_plan_with_section, _make_story
├── test_create.py
├── test_update_status.py
├── test_acceptance.py
├── test_link.py
└── test_coverage.py
```

**Acceptance.** Каждый файл ≤ 200 LOC; счётчик тестов сохранён.

---

### RFL-073 — Refactor: split `tests/services/test_doc_service.py` (566 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Группы:** create + create-validation / sections (add/list) / render_body / patch_section + concurrency / rename / path-validation.

**Целевая структура.**

```
tests/services/doc/
├── __init__.py
├── _helpers.py              # _add_project, _new_doc
├── test_create.py
├── test_sections.py         # add_section, get_sections
├── test_render_body.py
├── test_patch_section.py    # включая concurrency conflict
├── test_rename.py
└── test_path_validation.py  # SD-100, абсолютные/traversal-пути
```

**Acceptance.** Каждый файл ≤ 200 LOC.

---

### RFL-074 — Refactor: split `tests/services/test_plan_service.py` (490 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Группы (по разметке `# ====`):** recalc / ready / audit / export.

**Целевая структура.**

```
tests/services/plan/
├── __init__.py
├── _helpers.py              # _seed_plan_with_sections, _seed_task
├── test_recalc.py
├── test_ready.py
├── test_audit.py
└── test_export.py
```

После выполнения RFL-074 имеет смысл также вынести `forward_chain` / `reverse_chain` / `critical_path` тесты в `test_graph.py` (если они существуют — найти и доперенести).

**Acceptance.** Каждый файл ≤ 200 LOC.

---

### RFL-075 — Refactor: split `tests/services/test_task_service.py` (444 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Группы:** create (+ id-generation, validation) / update_status / complete (с deps + concurrency) / list_for_plan.

**Целевая структура.**

```
tests/services/task/
├── __init__.py
├── _helpers.py              # _seed_plan, _task
├── test_create.py
├── test_update_status.py
├── test_complete.py
└── test_list.py
```

**Acceptance.** Каждый файл ≤ 200 LOC.

---

## Out of scope (не трогаем сейчас)

Файлы > 400 LOC, которые НЕ попадают в этот план — с обоснованием:

| Файл | LOC | Причина не разбивать |
|:-----|----:|:---------------------|
| `docs/system/roadmap/web-frontend-task-plan.md` | 1339 | Документ-план — намеренно один файл, разбиение разорвёт связность Progress Overview / Next Batch. |
| `docs/system/roadmap/cod-doc-task-plan.md` | 815 | Аналогично. |
| `docs/HANDBOOK.md` | 759 | Single-document product guide (см. коммит f897bc0). Разбиение противоречит замыслу. |
| `docs/system/roadmap/audit-followups-task-plan.md` | 511 | Активный план, см. выше. |
| `docs/system/DATA_MODEL.md` | 499 | Архитектурный документ; рекомендации по разбиению — отдельный аудит. |
| `docs/cod-doc-guide.md` | 412 | Гайд для пользователя; разбиение возможно, но требует UX-решения. |
| `tests/services/test_projection_service.py` | 411 | На грани, темы хорошо размечены, но всего 411 — разбиение даст 4 файла по ~100 LOC, что не оправдано. Если RFL-070 уберёт ~25 LOC fixture'ов — станет 386, граница. **Решение:** не делим в этой итерации. |

## Sequencing

Безопасный порядок:

1. **RFL-070** (вынос fixtures в conftest.py) — **первым**, иначе каждое разбиение тестов раздувает дублирование.
2. **RFL-014** (validation), **RFL-013** (projection) — самые маленькие сервисы, низкий риск, отрабатываем паттерн «сервис → пакет с фасадом».
3. **RFL-010** (plan_service), **RFL-011** (link_service), **RFL-012** (story_service) — крупные сервисы; используем уже отработанный паттерн.
4. **RFL-020** (models) — отдельной PR, с особым вниманием к alembic-diff.
5. **RFL-001** (pages), **RFL-002** (fragments) — web-слой; smoke-тестируем через playwright.
6. **RFL-040** (mcp/server) — много мелких регистраций, низкий риск.
7. **RFL-030**, **RFL-031**, **RFL-032** (CLI) — параллелятся.
8. **RFL-050** (TUI wizard) — изолированная зона, можно когда угодно.
9. **RFL-060** (CSS) — после визуального снапшота через playwright.
10. **RFL-071..RFL-075** (тесты) — последними, когда public API сервисов уже стабилизирован.

Каждая задача — отдельный PR, ≤ ~600 LOC diff, легко ревьюируется.

## Verify-loop (общее для всех задач)

```bash
# до разбиения — захватить baseline
pytest -q  > /tmp/baseline.txt
ruff check
mypy cod_doc

# после разбиения
pytest -q  > /tmp/after.txt
diff /tmp/baseline.txt /tmp/after.txt   # должно быть пусто (или только время)
ruff check
mypy cod_doc

# для web/CSS — playwright скриншоты до/после
python .cod-doc-pw-task-detail.py   # уже в репо
```
