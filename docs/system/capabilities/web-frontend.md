---
type: capability
scope: web-frontend
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-04-28
last_updated: 2026-05-02
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

Web-маршруты живут в `cod_doc.api.web.*` и подключаются вторым роутером в `server.py`. Префикса нет — корень отдан под Web; API остаётся на `/api/*`.

Колонка **Status** показывает реальное состояние реализации (см. также §11):

- ✅ shipped, тесты зелёные;
- 🔄 in-progress / частично реализовано;
- ❌ pending — endpoint описан как целевой, но в коде его ещё нет.

| Метод + путь | Назначение | Сервис | Status | Task |
|--------------|-----------|--------|:------:|------|
| `GET /` | Список проектов + ссылка на settings | `Config.list_projects()` + `Project.stats()` | ✅ | WEB-001 |
| `GET /p/{slug}` | Дашборд проекта: stats, MASTER preview, табы | `Project.stats()` + `Project.read_master()` | ✅ | WEB-002 |
| `GET /p/{slug}/docs` | Список документов | `doc_service.list_for_project` | ✅ | WEB-003 |
| `GET /p/{slug}/docs/{doc_key:path}` | Просмотр документа: секции + body | `doc_service.get` + `get_sections` + `render_body` | ✅ | WEB-003 |
| `POST /p/{slug}/docs/{doc_key:path}/sections/{anchor}` | HTMX-патч секции (form-encoded body) | `doc_service.patch_section` | ✅ | WEB-012 |
| `GET /p/{slug}/docs/{doc_key:path}/sections/{anchor}/edit` | Edit form fragment | `doc_service.get_sections + revisions.head_for_entity` | ✅ | WEB-012 |
| `GET /p/{slug}/docs/{doc_key:path}/sections/{anchor}/view` | View fragment (cancel) | `doc_service.get_sections` | ✅ | WEB-012 |
| `GET /p/{slug}/tasks` | Таблица задач (фильтр `?status=`, `?plan=`) | `task_service.list_for_project` | ✅ | WEB-010 |
| `POST /p/{slug}/tasks/{task_id}/status` | HTMX-смена статуса (radio/select) | `task_service.update_status` | ✅ | WEB-011 |
| `POST /p/{slug}/tasks/{task_id}/complete` | HTMX-завершение задачи | `task_service.complete` | ✅ | WEB-014 |
| `GET /p/{slug}/plans/{plan_id}` | Plan view: Progress Overview + Next Batch + Mermaid | `plan_service.recalc/ready/export` | ✅ | WEB-004 |
| `GET /p/{slug}/plans` | Список планов проекта | `plan_service.list_for_project + recalc` | ✅ | WEB-004 |
| `GET /p/{slug}/revisions` | Лог ревизий (фильтр по entity) | `revision_service.list_for_project` | ✅ | WEB-021 |
| `GET /p/{slug}/run` | SSE-стрим запуска агента | переиспользует `Orchestrator.run_autonomous` (см. [routes.py](../../../cod_doc/api/routes.py)) | ❌ | WEB-030 |
| `GET /settings`, `POST /settings` | Просмотр + сохранение конфига (API-ключ маскирован) | `Config.load/save` | ✅ | WEB-060 |
| `GET /static/{path:path}` | Статика | StaticFiles mount | ✅ | WEB-001 |

**Принцип:** обработчик не знает про SQL/репозитории. Только сервисы (`cod_doc.services.*`) и существующие helper-ы (`get_config`, `get_project`, новый `get_project_db` после WEB-040). См. §7.

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

## 8. Тестирование

- **Smoke**: `fastapi.testclient.TestClient`, каждая страница 200 на seed-проекте.
  Текущий suite — `tests/api/test_web_*.py`, **27 тестов, все зелёные**.
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
| Endpoints shipped | **5 / 14** (~36 %) |
| LOC python (`api/web`) | 363 |
| LOC templates | 280 |
| LOC `app.css` | 211 |
| Web-tests | 27 (`pytest tests/api/ -q` ⇒ зелёные) |
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
| ~~`status_options` дубль~~ | ~~`pages.py:147`, `fragments.py:52`~~ | ✅ **WEB-041** done 2026-05-02 (Jinja global) |

Полный разбор — в audit-отчёте от 2026-05-02 (см. ссылку выше).

## 12. Changelog

| Дата | Событие |
|------|---------|
| 2026-04-28 | Создан capability-документ (status: draft). |
| 2026-05-02 | Section A (Scaffold) + WEB-010/011 закрыты. Capability переведён в `active`. §3 расширен колонками Status/Task; §4 — пометкой «целевая структура», явное реальное состояние; §7 — DI-конвенция и ссылка на WEB-040; §8 — error-branch coverage в DoD; добавлен §11 «Текущее состояние». См. audit-отчёт `2026-05-02-section-web-frontend.md`. |
