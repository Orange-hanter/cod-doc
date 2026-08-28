---
type: capability
scope: web-frontend
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-04-28
last_updated: 2026-08-28
related_docs:
  - ../ARCHITECTURE.md
  - ../VISION.md
  - plan-management.md
  - doc-evolution.md
  - context-retrieval.md
  - ../audit/2026-05-02-section-web-frontend.md
related_code:
  - cod_doc/api/server.py
  - cod_doc/api/web/
  - cod_doc/services/
  - cod_doc/templates/web/
  - cod_doc/static/
---

# Capability — Web Frontend

> Чисто функциональный веб-интерфейс к COD-DOC: список проектов, документы, задачи, планы, ревизии, лог запусков агента. Без визуальной полировки и без отдельного фронтенд-стека: server-rendered Jinja + точечные HTMX-фрагменты, отдаваемые тем же FastAPI.

## 1. Цель и не-цели

### Цель

Дать человеку (автору документации, оператору агента) тот же набор операций, что доступен через CLI/MCP, в браузере, **без отдельного билд-пайплайна и без дублирования доменной модели на TS**. Поверхность — равная другим: всё, что доступно в сервисах, должно быть доступно в Web в течение одного PR-цикла после CLI/MCP (правило [ARCHITECTURE.md §1](../ARCHITECTURE.md)).

### Не-цели

- Не SPA. Никакого React/Vue/Svelte, никакого build-pipeline, никакого client-side router-а.
- Не визуальный продукт. Нет дизайн-системы, нет тёмной темы, нет анимаций. Минимальный CSS (один файл, ~200 строк, `pico.css`-подобный baseline или собственный).
- Не публичный. Аутентификация — отложена; интерфейс рассчитан на запуск локально либо за reverse-proxy с basic-auth.
- Не replacement для TUI. TUI остаётся для оффлайн-/быстрых сценариев. Web — для ситуаций «проще навести курсор».

## 2. Стек

| Слой | Решение | Почему |
|------|---------|--------|
| Сервер | FastAPI (тот же `cod_doc.api.server:app`) | Уже есть, общий lifespan и DI |
| Шаблоны | Jinja2 (`jinja2` уже в deps) | Server-rendered HTML; одна модель, никакой генерации схем |
| Интерактивность | HTMX (через `<script src="/static/htmx.min.js">`) | `hx-get`/`hx-post`/`hx-swap` для inline-редактирования и фрагментов; SSE для live-логов |
| Стили | Один `static/app.css` (~150-300 строк, raw CSS) | Без сборщика, без PostCSS, без Tailwind |
| Граф зависимостей | Mermaid через `<script type="module">` (CDN или локально) | Уже используется в task-plan markdown — переиспользуем |

Никаких новых зависимостей в `pyproject.toml` сверх уже имеющихся (`fastapi`, `jinja2`).

## 3. Маршруты

Web-маршруты живут в `cod_doc.api.web.*` и подключаются вторым роутером в `server.py`. Префикса нет — корень отдан под Web; API остаётся на `/api/*`. Статика (`/static/*`) отдаётся отдельным `StaticFiles` mount'ом и не входит в таблицу ниже.

Колонка **Status** показывает реальное состояние реализации (см. также §11):

- ✅ shipped, тесты зелёные;
- 🔄 in-progress / частично реализовано;
- ❌ pending — endpoint описан как целевой, но в коде его ещё нет.

| Метод + путь | Назначение | Сервис | Status | Task |
|--------------|-----------|--------|:------:|------|
| **Глобальные страницы**  | | | |
| `GET /` | Список проектов + пагинация + stats | `Config.list_projects()` + `Project.batch_stats()` + `task_service.summarize_for_project` | ✅ | WEB-001 |
| `GET /settings` | Просмотр конфига (API-ключ маскирован) | `Config.load` | ✅ | WEB-060 |
| `POST /settings` | Сохранение конфига | `Config.save` | ✅ | WEB-060 |
| `GET /standards` | Каталог встроенных skills/стандартов | `skill_service.list_skills` | ✅ | OBI-standards |
| `GET /standards/{name}` | Деталь skill'а / стандарта | `skill_service.get_skill` | ✅ | OBI-standards |
| **Проект — overview и bootstrap**  | | | |
| `GET /p/{slug}` | Дашборд проекта: KPI, MASTER preview, ready tasks, планы, ревизии, drift-health | `Project.stats/read_master` + `plan_service` + `revision_service` + `project_health_service` | ✅ | WEB-002 |
| `POST /p/{slug}/init` | Bootstrap БД (alembic upgrade + ProjectModel) | `project_service.init_project` | ✅ | WEB-080 |
| `POST /p/{slug}/import_master/scan` | AI-скан репо: preview MASTER.md + coverage tasks | `ai_generate.generate_master_from_folder` | ✅ | COD-060 |
| `POST /p/{slug}/import_master/save` | Запись MASTER.md и создание coverage tasks | `ai_generate` + `task_service.create` | ✅ | COD-060 |
| **Документы — список и CRUD**  | | | |
| `GET /p/{slug}/docs` | Список документов (tree/flat, фильтр по type/status/q) | `doc_service.list_for_project` | ✅ | WEB-003 |
| `GET /p/{slug}/docs/new` | Форма создания пустого документа | `doc_service` (только форма) | ✅ | COD-078 |
| `POST /p/{slug}/docs/new` | Создание пустого документа | `doc_service.create` | ✅ | COD-078 |
| `POST /p/{slug}/docs/suggest` | AI-подбор title/doc_key/type/preamble по описанию | `ai_text.suggest_doc_meta` | ✅ | COD-078 |
| `POST /p/{slug}/docs-accept` | Продвижение документа в статус ACTIVE (или др.) | `doc_service.update_status` | ✅ | COD-052 |
| `GET /p/{slug}/docs/{doc_key:path}` | Просмотр документа: preamble + sections + links + comments + suggestions | `doc_service.get/render_body/get_sections` + `link_service` + `comment_service` | ✅ | WEB-003 |
| `POST /p/{slug}/docs/{doc_key:path}/expand` | AI-генерация секций для пустого/разреженного документа | `ai_text.expand_doc_sections` + `doc_service.add_section` | ✅ | COD-078 |
| **Документы — импорт**  | | | |
| `GET /p/{slug}/docs/import` | Bulk import UI: форма + манифест | `import_service` (форма) | ✅ | PCA-401 |
| `POST /p/{slug}/docs/import` | Загрузка одного markdown-файла → Document + Sections | `import_service.import_markdown` | ✅ | WEB-081 |
| `GET /p/{slug}/docs/import/scan` | JSON-манифест файлов проекта vs БД | `import_service.scan_folder` | ✅ | PCA-400 |
| `POST /p/{slug}/docs/import/apply` | Применение выбранных файлов из bulk-манифеста | `import_service.import_or_update_markdown` | ✅ | PCA-401 |
| **Документы — AI-генерация из источников**  | | | |
| `GET /p/{slug}/docs/generate` | Форма выбора source-документов и intent | `doc_service.list_for_project` | ✅ | COD-078 |
| `POST /p/{slug}/docs/generate` | Preview сгенерированного документа | `ai_generate.generate_doc_from_sources` | ✅ | COD-078 |
| `POST /p/{slug}/docs/generate/save` | Сохранение сгенерированного документа + авто-ссылки | `doc_service.create/add_section` | ✅ | COD-078 |
| **Документы — семантические подсказки ссылок**  | | | |
| `POST /p/{slug}/suggestions/run` | Запуск генерации semantic link suggestions для документа | `link_service.semantic.suggest_for_section` | ✅ | PCA-422 |
| `POST /p/{slug}/suggestions/{row_id}/accept` | Принятие suggestion: добавление ссылки в See also | `doc_service.patch_section` + `link_service.semantic` | ✅ | PCA-422 |
| `POST /p/{slug}/suggestions/{row_id}/reject` | Отклонение suggestion | `link_service.semantic.update_suggestion_state` | ✅ | PCA-422 |
| **Документы — комментарии**  | | | |
| `POST /p/{slug}/docs/{doc_key:path}/comments` | Создание section-anchored или doc-level комментария | `comment_service.create` | ✅ | OBI-comments |
| `GET /p/{slug}/docs/{doc_key:path}/comments.json` | JSON-дамп комментариев (popover'ы) | `comment_service.list_for_document` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/{comment_id}/resolve` | Пометить комментарий resolved | `comment_service.update_status` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/{comment_id}/reopen` | Вернуть комментарий в open | `comment_service.update_status` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/{comment_id}/delete` | Удалить комментарий | `comment_service.delete` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/apply` | AI-rework preview по открытым комментариям | `comment_service.apply_open_with_ai` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/apply/commit` | Применение выбранных AI-правок | `doc_service.patch_section` + `comment_service.update_status` | ✅ | OBI-comments |
| **Документы — навигатор и анализ**  | | | |
| `GET /p/{slug}/docs/navigator` | Карта пути документации + кешированный gap-analysis | `nav_service.compute_journey` + `nav_service.peek_cached_analysis` | ✅ | OBI-navigator |
| `POST /p/{slug}/docs/navigator/analyze` | HTMX: запуск/загрузка AI gap-analysis | `nav_service.analyze_gaps` | ✅ | OBI-navigator |
| **Секции — HTMX-фрагменты**  | | | |
| `GET /p/{slug}/docs/{doc_key:path}/sections/{anchor}/edit` | Edit form fragment | `doc_service.get_sections` + `revision_service.head_for_entity` | ✅ | WEB-012 |
| `GET /p/{slug}/docs/{doc_key:path}/sections/{anchor}/view` | View fragment (cancel) | `doc_service.get_sections` | ✅ | WEB-012 |
| `POST /p/{slug}/docs/{doc_key:path}/sections/{anchor}` | HTMX-патч секции (form-encoded body) | `doc_service.patch_section` | ✅ | WEB-012 |
| **Планы**  | | | |
| `GET /p/{slug}/plans` | Список планов проекта с прогрессом | `plan_service.list_for_project` + `recalc` | ✅ | WEB-004 |
| `GET /p/{slug}/plans/{plan_id}` | Plan view: Progress Overview + Next Batch + Mermaid | `plan_service.recalc/ready/export` | ✅ | WEB-004 |
| `POST /p/{slug}/plans/{plan_id}/freeze` | Snapshot текущего плана в EXECUTION_LOG документ | `plan_service.freeze_projection` | ✅ | COD-052 |
| **Задачи — список и detail**  | | | |
| `GET /p/{slug}/tasks` | Список задач (kanban board / chains, фильтр plan/status) | `task_service.list_for_project` + `plan_service` | ✅ | WEB-010 |
| `GET /p/{slug}/tasks/{task_id}` | Деталь задачи (header + chains + history + trace) | `task_service.get` + `plan_service.forward/reverse_chain` + `revision_service` + `trace_service` | ✅ | WEB-070 |
| `POST /p/{slug}/tasks/audit` | AI-аудит consistency всех задач проекта | `ai_text._call_lite_raw` + `task_service.list_for_project` | ✅ | OBI-tasks |
| `GET /p/{slug}/tasks/legacy` | Список legacy YAML-задач (paginated) | `Project.get_tasks` | ✅ | PCA-410 |
| `POST /p/{slug}/tasks/legacy/import` | Миграция legacy YAML-задач → DB (с WebSocket прогрессом) | `restate_importer.import_legacy_tasks` | ✅ | PCA-410 |
| `POST /p/{slug}/tasks/legacy/archive` | Архивация tasks.yaml → tasks.archived.yaml | filesystem rename | ✅ | PCA-410 |
| **Задачи — HTMX-фрагменты полей и статуса**  | | | |
| `GET /p/{slug}/tasks/{task_id}/fields/{field}/edit` | HTMX edit-form для description / acceptance | `task_service.get` | ✅ | WEB-071 |
| `GET /p/{slug}/tasks/{task_id}/fields/{field}/view` | HTMX view-fragment (Cancel target) | `task_service.get` | ✅ | WEB-071 |
| `POST /p/{slug}/tasks/{task_id}/fields/{field}` | Inline-патч поля (description / acceptance) | `task_service.update_description / update_acceptance` | ✅ | WEB-071 |
| `POST /p/{slug}/tasks/{task_id}/fields/{field}/improve` | LLM "improve" draft поля (без записи в БД) | `ai_text.improve_text_traced` | ✅ | WEB-071 |
| `POST /p/{slug}/tasks/{task_id}/status` | HTMX-смена статуса | `task_service.update_status` | ✅ | WEB-011 |
| `POST /p/{slug}/tasks/{task_id}/complete` | HTMX-завершение задачи | `task_service.complete` | ✅ | WEB-014 |
| **User Stories**  | | | |
| `GET /p/{slug}/stories` | Список user stories (group by section/persona/none) | `story_service.list_for_project` + `doc_service.list_for_project` | ✅ | COD-068 |
| `GET /p/{slug}/stories/{story_id}` | Деталь story + acceptance + linked tasks | `story_service.get` + `list_acceptance` + `list_tasks` | ✅ | COD-069 |
| `POST /p/{slug}/stories/generate` | AI-генерация draft-историй из MASTER.md | `ai_generate.generate_stories` | ✅ | COD-068 |
| `POST /p/{slug}/stories/save` | Сохранение выбранных story drafts | `story_service.create` | ✅ | COD-068 |
| `POST /p/{slug}/stories/{story_id}/status` | Продвижение story между статусами | `story_service.update_status` | ✅ | COD-069 |
| `POST /p/{slug}/stories/{story_id}/tasks/generate` | AI-генерация задач для story | `ai_generate.generate_tasks_for_story` | ✅ | COD-069 |
| `POST /p/{slug}/stories/{story_id}/tasks/save` | Сохранение сгенерированных задач + линковка к story | `task_service.create` + `story_service.link` | ✅ | COD-069 |
| `POST /p/{slug}/stories/coverage/analyze` | AI-анализ покрытия документов для story-generation | `ai_text._call_lite_raw` + `doc_service.list_for_project` | ✅ | COD-068 |
| `POST /p/{slug}/stories/section/{section_key}/analyze` | AI-summary для одной секции stories | `section_summary_service.generate` | ✅ | COD-069 |
| **ADR — Architecture Decision Records**  | | | |
| `GET /p/{slug}/adr` | Список ADR с фильтром по статусу | `adr_service.list_for_project` | ✅ | ADR-004 |
| `GET /p/{slug}/adr/new` | Форма создания ADR | `adr_service` (форма) | ✅ | ADR-005 |
| `POST /p/{slug}/adr/new` | Создание ADR | `adr_service.create` | ✅ | ADR-005 |
| `GET /p/{slug}/adr/graph` | Supersede DAG в виде Mermaid | `adr_service.graph` | ✅ | ADR-006 |
| `GET /p/{slug}/adr/{adr_id}` | Деталь ADR + диаграммы + форма редактирования | `adr_service.get` + `adr_to_dict` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/edit` | Редактирование полей ADR | `adr_service.update` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/diagram` | Добавление Mermaid-диаграммы к ADR | `adr_service.add_diagram` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/supersede` | Запись supersede-ребра | `adr_service.supersede` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/deprecate` | Перевод ADR в DEPRECATED | `adr_service.deprecate` | ✅ | ADR-005 |
| **Ревизии, рутины, запуски агента**  | | | |
| `GET /p/{slug}/revisions` | Лог ревизий (фильтр по entity) | `revision_service.list_for_project` | ✅ | WEB-021 |
| `GET /p/{slug}/routines` | Список routines + история запусков | `routine_service.list_routines` + `history` | ✅ | PCA-919 |
| `POST /p/{slug}/routines/create` | Создание routine | `routine_service.create` | ✅ | PCA-919 |
| `POST /p/{slug}/routines/{name}/toggle` | Включение/выключение routine | `routine_service.update_status` | ✅ | PCA-920 |
| `POST /p/{slug}/routines/{name}/run` | Ручной запуск routine | `routine_service.run_now` | ✅ | PCA-920 |
| `POST /p/{slug}/routines/{name}/delete` | Удаление routine | `routine_service.delete` | ✅ | PCA-920 |
| `GET /p/{slug}/run` | Live agent console + история запусков | `run_service.list_recent` + `activity_service` | ✅ | WEB-030 |
| `GET /p/{slug}/run/{run_id}` | Деталь одного запуска агента | `run_service.get_one` + `activity_service.events_for_run` | ✅ | WEB-030 |
| **Поиск, коммиты, code-refs, метрики, затраты**  | | | |
| `GET /p/{slug}/search` | FTS5-поиск по tasks/docs/stories/ADRs | `search_service.search` | ✅ | OBI-040 |
| `POST /p/{slug}/search/reindex` | Перестроение FTS-индекса проекта | `search_service.reindex_all` | ✅ | OBI-040 |
| `GET /p/{slug}/commits` | Таблица commit↔task links | `commit_link_service.list_for_project` | ✅ | OBI-011 |
| `POST /p/{slug}/commits/import` | Рескан git log и импорт task-tagged commits | `commit_link_service.import_from_git_log` | ✅ | OBI-011 |
| `GET /p/{slug}/code-refs` | Список code-refs проекта | `link_service.list_code_refs` | ✅ | OBI-021 |
| `GET /p/{slug}/code-refs/preview` | Preview первых 20 строк файла (JSON) | `link_service.list_code_refs` + filesystem | ✅ | OBI-021 |
| `GET /p/{slug}/metrics` | Completion stats: sparkline + percentiles by type | `metrics_service.summary` + `sparkline_buckets` | ✅ | OBI-002 |
| `GET /p/{slug}/costs` | Dashboard затрат по моделям (trace calls + pricing) | `trace_service.aggregate_by_model_for_project` | ✅ | PCA-925 |

**Принцип:** обработчик не знает про SQL/репозитории. Только сервисы (`cod_doc.services.*`) и существующие helper-ы (`get_config`, `get_project`, `get_project_db`). См. §7.

> **Status (2026-05-02, post WEB-040):** правило восстановлено. `cod_doc/api/web/`
> импортирует только `cod_doc.services.*`, `cod_doc.api.deps`, `cod_doc.config`,
> `cod_doc.core`, `cod_doc.domain.entities`, `cod_doc.logging_config`. AST-тест
> [tests/api/test_web_layer_imports.py](../../../tests/api/test_web_layer_imports.py)
> блокирует регрессию.

## 4. HTML-структура

> **Целевая структура.** Реальное состояние и матрица «есть/нет» — в
> [roadmap/web-frontend-task-plan.md](../roadmap/web-frontend-task-plan.md)
> Progress Overview. Добавлять файлы под TBD-эндпоинты заранее **не нужно** —
> создавайте только то, что закрывает живая задача.

```text
cod_doc/api/web/
├── __init__.py            # router = APIRouter()         ← ✅
├── pages.py               # GET-страницы                  ← ✅
├── fragments.py           # HTMX-фрагменты                ← ✅
├── templates_env.py       # Jinja2Templates + STATIC_DIR  ← ✅
└── db_resolver.py         # bridge slug → DB session      ← ⚠ удалить в WEB-040
                           #   (заменить на get_project_db в cod_doc.api.deps)

cod_doc/templates/web/
├── base.html              # <html>, htmx, app.css; #alerts ← ✅
├── _layout/               # макросы — общие фрагменты         ❌ (WEB-041)
│   ├── project_tabs.html  # tabs nav (active=…)               ❌ (WEB-041)
│   ├── header.html                                           ❌
│   └── nav.html                                              ❌
├── index.html             # список проектов               ← ✅
├── settings.html                                            ❌ (WEB-020)
├── project/
│   ├── show.html          # дашборд                       ← ✅
│   ├── docs_list.html     # список документов             ← ✅
│   ├── doc_show.html      # просмотр документа            ← ✅ (raw markdown — WEB-006)
│   ├── tasks_list.html    # таблица задач + фильтр        ← ✅
│   ├── plan_show.html     # Plan + Mermaid                ❌ (WEB-004)
│   ├── revisions.html     # лог ревизий                   ❌ (WEB-021)
│   └── run.html           # SSE-консоль                   ❌ (WEB-030)
└── _frag/
    ├── task_row.html      # строка таблицы задач          ← ✅
    ├── section_view.html  # секция документа              ❌ (WEB-012)
    ├── section_edit.html  # textarea + concurrency token  ❌ (WEB-012)
    └── alert.html         # ошибка/уведомление в #alerts  ❌ (WEB-022)

cod_doc/static/
├── app.css                # ~210 LOC, raw CSS             ← ✅
├── htmx.min.js            # v2.0.4 vendored                ← ✅
└── mermaid.min.js                                          ❌ (WEB-004)
```

## 5. UX-инварианты

- Каждая страница рендерится за один SQL-запрос к данным + один к stats. Никаких N+1.
- Inline-редактирование (статус задачи, патч секции) идёт через HTMX `hx-post` → сервер возвращает HTML-фрагмент строки/секции, который заменяет старый. Никаких JSON-API в этих эндпоинтах — только HTML. JSON-вариант остаётся в `/api/*`.
- Любая ошибка сервиса (NotFound, Conflict, Validation) выводится в виде alert-баннера сверху страницы (HTMX target `#alerts`) либо красным текстом рядом с полем. Нет молчаливых редиректов.
- Все формы — обычные `<form method="post">`, работают и без JS. HTMX — прогрессивный enhancement.
- В URL-ах используется `slug` проекта и `doc_key` / `task_id` — те же ключи, что в БД и MCP. Это даёт совпадение URL ↔ ссылка в markdown.

## 6. Live-операции (агент, импорт)

Длинные операции (`Orchestrator.run_autonomous`, импорт Restate) отдаются через **Server-Sent Events**, не через WebSocket. Причина:

- SSE — простой `text/event-stream`, нативно поддерживается HTMX (`hx-ext="sse"`), не требует JS-библиотек.
- WebSocket в [webhooks.py](../../../cod_doc/api/webhooks.py#L120) остаётся для машинных клиентов; web-консоль использует SSE.
- Подключение one-way (сервер → клиент); отмена — через `DELETE /p/{slug}/run/{run_id}`.

## 7. Соответствие сервисам и DI-конвенция

Web-страница не имеет права обходить сервис. Правило проверяется в `cod-doc audit`
(`web_calls_services_only`) и в банлисте импортов (ruff
`flake8-tidy-imports.banned-module-level-imports` после WEB-040).

| Страница | Разрешённые модули | Запрещено |
|----------|-------------------|-----------|
| `/` | `cod_doc.config`, `cod_doc.api.deps` | `cod_doc.infra.*` |
| `/p/{slug}/*` | `cod_doc.services.*`, `cod_doc.api.deps`, `cod_doc.domain.entities` (только enums) | `cod_doc.infra.*`, ORM-модели |
| `/settings` | `cod_doc.config`, `cod_doc.api.deps` | `cod_doc.infra.*` |

### DI: как страница получает DB session

Резолв «slug → SQLAlchemy Session + project_db_id» — единственный способ
работы с per-project БД из web-слоя. После закрытия WEB-040 он живёт в
[cod_doc/api/deps.py](../../../cod_doc/api/deps.py) как FastAPI dependency:

```python
# целевой контракт (после WEB-040):
def get_project_db(slug: str) -> tuple[Session, int]: ...
# возвращает (session, project_db_id) или поднимает HTTPException(404)
# session — из кэшированного Engine-а (см. WEB-005)
```

Запрещено:
- `from cod_doc.infra.db import make_engine`,
- `from cod_doc.infra.repositories import ProjectRepository`,
- импорт ORM-моделей `from cod_doc.infra.models import …`.

Разрешено:
- `from cod_doc.domain.entities import TaskStatus, DocumentStatus, …` — только
  enums и dataclass-ы домена; они не привязаны к БД.

Прямой доступ из Web к `cod_doc.infra.db` или ORM-моделям — запрещён.

> **Закрыто (2026-05-02):** WEB-040 + WEB-005 закрыты; web-слой больше не
> импортирует `cod_doc.infra.*`. Регрессии ловятся AST-тестом
> [tests/api/test_web_layer_imports.py](../../../tests/api/test_web_layer_imports.py).

### Как добавить новую web-страницу (DI-pattern)

Recipe для нового handler'а в `cod_doc/api/web/pages.py` или `fragments.py`:

```python
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import doc_service as docs

router = APIRouter()

# ── Strict (404 если БД не готова) ───────────────────────────────────
@router.get("/p/{slug}/something")
def page_strict(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
):
    proj = get_project(slug)
    session, project_db_id = db
    # use session through services only — never `from cod_doc.infra...`
    items = docs.list_for_project(session, project_db_id)
    return templates.TemplateResponse(request, "..", {...})

# ── Graceful (рендер с warning если БД не готова) ────────────────────
@router.get("/p/{slug}/something-graceful")
def page_graceful(request: Request, slug: str):
    proj = get_project(slug)
    items = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            items = docs.list_for_project(session, project_db_id)
    return templates.TemplateResponse(request, "..", {..., "db_available": db_available})
```

Choosing between strict and graceful: **detail pages** (entity-by-id) →
strict (404 если БД нет). **List pages** (overview, table) → graceful
(показать warning, не падать). Cross-project guard для entity-id
endpoints — service-helper типа `plan_service.get_for_project(...)`.

## 8. Тестирование

- **Smoke**: `fastapi.testclient.TestClient`, каждая страница 200 на seed-проекте.
  Текущий suite — `tests/api/test_web_*.py`, **137 тестов, все зелёные**.
- **Error-branch coverage** (часть DoD каждой write-path задачи):
  - валидация формы (400 на garbage),
  - конфликт ревизий (`RevisionConflictError`),
  - нарушение FK (`IntegrityError`),
  - доменный отказ (`ValueError` от сервиса).
  Без всех четырёх веток — не закрывать write-path.
- **Snapshot-тесты HTML-фрагментов** — нет, и не планируем. Фрагменты тестируются
  через service-тесты + smoke-структурные ассерты (`'badge-pending' in r.text`).
  HTML-snapshot шумит на каждой косметической правке.
- **E2E (Playwright)** — отложено до закрытия §3. Триггер: появление ≥3 многошаговых
  сценариев (например, «создать → редактировать секцию → откатить ревизию»).

## 9. Roadmap

Реализация — [roadmap/web-frontend-task-plan.md](../roadmap/web-frontend-task-plan.md). Зависимости от ядра:

- Section A (scaffold) — независимо.
- Doc/Task/Plan-страницы — после соответствующих сервисов (COD-010 ✅ / COD-011 ✅ / COD-012 ✅).
- Revisions — после COD-015 ✅.
- Live-консоль — после стабилизации `Orchestrator` (вне этого плана).

## 10. Альтернатива, отвергнутая на старте

**SPA (Vite + React + TanStack Query).** Преимущества: богатые виджеты (drag-n-drop в плане, интерактивный граф), переиспользование на mobile. Недостатки на текущем этапе:

- Дублирование схем (TS-типы vs Pydantic) или генератор OpenAPI → лишний слой.
- Отдельный билд-пайплайн, отдельный deploy.
- CORS, токены, devtools.
- Регрессии по принципу «UI отстаёт от CLI/MCP» — то самое, чего избегаем по [ARCHITECTURE.md §1](../ARCHITECTURE.md).

Возврат к SPA возможен, когда понадобится один из non-goals выше (мобайл, интерактивный граф). До этого — server-rendered.

---

## 11. Текущее состояние (2026-05-02)

Срез по факту реализации. Поддерживается в синхроне с
[roadmap/web-frontend-task-plan.md](../roadmap/web-frontend-task-plan.md) и
[audit/2026-05-02-section-web-frontend.md](../audit/2026-05-02-section-web-frontend.md).

### 11.1 Метрики

| Метрика | Значение |
|---|---:|
| Endpoints shipped | **13 / 14** (~93 %) |
| LOC python (`api/web`) | ~900 |
| LOC templates | ~600 |
| LOC `app.css` | ~370 |
| Web-tests | 137 (`pytest tests/api/ -q` ⇒ зелёные) |
| Vendored JS | `htmx.min.js` v2.0.4 |
| Зависимостей в `pyproject.toml` сверх baseline | **0** (как обещано §2) |

### 11.2 Что работает «сегодня»

- **Список проектов** (`/`) — все registered проекты, их stats, warning при unconfigured.
- **Дашборд проекта** (`/p/{slug}`) — 7 KPI-карточек, MASTER preview (первые 80 строк),
  ссылка «Открыть целиком», табы.
- **Документы** (`/p/{slug}/docs` + `/p/{slug}/docs/{doc_key:path}`) — список, split-layout
  body+sections, graceful warning «БД не инициализирована».
- **Задачи** (`/p/{slug}/tasks`) — таблица с фильтром по статусу, color-coded badges + priority,
  HTMX inline status update (POST `/{task_id}/status`), `<noscript>` fallback,
  POST/Redirect/GET для не-HTMX клиентов.

### 11.3 Архитектурный долг

| Долг | Где | Закрывается в |
|---|---|---|
| ~~Web → infra direct import~~ | ~~`db_resolver.py`~~ | ✅ **WEB-040** done 2026-05-02 |
| ~~Engine на каждый запрос~~ | ~~`db_resolver.py`~~ | ✅ **WEB-005** done 2026-05-02 |
| ~~Index N+1 (`Project.stats()` per project)~~ | ~~`pages.py:27-35`~~ | ✅ **WEB-013** done 2026-05-02 |
| ~~Tabs дублируются в 3 шаблонах, в `doc_show` отсутствуют~~ | ~~`templates/web/project/*`~~ | ✅ **WEB-041** done 2026-05-02 |
| ~~Tabs ведут на 404 для нереализованных страниц~~ | ~~shared~~ | ✅ **WEB-041** done 2026-05-02 |
| ~~`<div id="alerts">` без модели — ошибки молча теряются~~ | ~~`base.html` + `fragments.py`~~ | ✅ **WEB-022** done 2026-05-02 |
| ~~`doc_show` body — raw markdown без anchor'ов~~ | ~~`doc_show.html`, `pages.py:162`~~ | ✅ **WEB-006** done 2026-05-02 |
| ~~`status_options` дубль~~ | ~~`pages.py:147`, `fragments.py:52`~~ | ✅ **WEB-041** done 2026-05-02 |

Полный разбор — в audit-отчёте от 2026-05-02 (см. ссылку выше).

## 12. Changelog

| Дата | Событие |
|------|---------|
| 2026-04-28 | Создан capability-документ (status: draft). |
| 2026-05-02 | Section A (Scaffold) + WEB-010/011 закрыты. Capability переведён в `active`. §3 расширен колонками Status/Task; §4 — пометкой «целевая структура», явное реальное состояние; §7 — DI-конвенция и ссылка на WEB-040; §8 — error-branch coverage в DoD; добавлен §11 «Текущее состояние». См. audit-отчёт `2026-05-02-section-web-frontend.md`. |
| 2026-08-28 | ADO-011: §3 синхронизирован со всеми живыми web-роутами (87 endpoints). Устранён WR-1 `/static/{path}`. |