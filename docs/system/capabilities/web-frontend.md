---
type: capability
scope: web-frontend
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-28
last_updated: 2026-04-28
related_docs:
  - ../ARCHITECTURE.md
  - ../VISION.md
  - plan-management.md
  - doc-evolution.md
  - context-retrieval.md
related_code:
  - cod_doc/api/server.py
  - cod_doc/api/routes.py
  - cod_doc/services/
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

| Метод + путь | Назначение | Сервис |
|--------------|-----------|--------|
| `GET /` | Список проектов + ссылка на settings | `Config.list_projects()` (legacy) или `ProjectRepository.list_all()` (DB) |
| `GET /p/{slug}` | Дашборд проекта: stats, MASTER preview, табы | `Project.stats()` + `DocService.get` |
| `GET /p/{slug}/docs` | Список документов | `DocService.list` (TBD) |
| `GET /p/{slug}/docs/{doc_key:path}` | Просмотр документа: секции + body | `DocService.get` + `DocService.get_sections` + `DocService.render_body` |
| `POST /p/{slug}/docs/{doc_key:path}/sections/{anchor}` | HTMX-патч секции (form-encoded body) | `DocService.patch_section` |
| `GET /p/{slug}/tasks` | Таблица задач (фильтр `?status=`, `?plan=`) | `TaskService.list_for_plan` |
| `POST /p/{slug}/tasks/{task_id}/status` | HTMX-смена статуса (radio/select) | `TaskService.update_status` |
| `POST /p/{slug}/tasks/{task_id}/complete` | HTMX-завершение задачи | `TaskService.complete` |
| `GET /p/{slug}/plans/{plan_id}` | Plan view: Progress Overview + Next Batch + Mermaid | `PlanService.recalc/ready/export` |
| `GET /p/{slug}/revisions` | Лог ревизий (фильтр по entity) | `RevisionService.list_for_entity` |
| `GET /p/{slug}/run` | SSE-стрим запуска агента | переиспользует `Orchestrator.run_autonomous` (см. [routes.py](../../../cod_doc/api/routes.py)) |
| `GET /settings` | Просмотр конфига | `Config.load()` |
| `POST /settings` | Сохранение конфига (form) | `Config.save()` |
| `GET /static/{path:path}` | Статика | StaticFiles mount |

**Принцип:** обработчик не знает про SQL/репозитории. Только сервисы (`cod_doc.services.*`) и существующие helper-ы (`get_config`, `get_project`).

## 4. HTML-структура

```text
cod_doc/api/web/
├── __init__.py          # router = APIRouter()
├── pages.py             # GET-страницы: возвращают HTMLResponse через templates
├── fragments.py         # HTMX-фрагменты: возвращают HTML-куски (hx-swap targets)
└── templates_env.py     # настройка Jinja2Templates с фильтрами/глобалами

cod_doc/templates/web/
├── base.html            # <html>, <head>, htmx, app.css; блок {% block content %}
├── _layout/             # шапка, навигация, alert-бар
│   ├── header.html
│   └── nav.html
├── index.html           # список проектов
├── settings.html
├── project/
│   ├── show.html        # дашборд
│   ├── docs_list.html
│   ├── doc_show.html
│   ├── tasks_list.html
│   ├── plan_show.html
│   ├── revisions.html
│   └── run.html         # SSE-консоль
└── _frag/               # HTMX-фрагменты
    ├── task_row.html
    ├── section_view.html
    └── section_edit.html

cod_doc/static/
├── app.css              # минимальный baseline
├── htmx.min.js          # вендорный (или CDN — на выбор)
└── mermaid.min.js       # для Plan view
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

## 7. Соответствие сервисам

Web-страница не имеет права обходить сервис. Ниже — соответствие, проверяемое в `cod-doc audit` (правило `web_calls_services_only`):

| Страница | Разрешённые модули |
|----------|-------------------|
| `/` | `cod_doc.config`, `cod_doc.infra.repositories.project_repo` |
| `/p/{slug}/*` | `cod_doc.services.*`, `cod_doc.api.deps` |
| `/settings` | `cod_doc.config` |

Прямой доступ из Web к `cod_doc.infra.db` или ORM-моделям — запрещён.

## 8. Тестирование

- Smoke-тесты через `fastapi.testclient.TestClient`: каждая страница возвращает 200 на seed-проекте.
- Snapshot-тесты HTML-фрагментов — нет (фрагменты тестируются через service-тесты + smoke-проверку структуры).
- E2E (Playwright) — отложено; сначала закрыть scope §3.

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
