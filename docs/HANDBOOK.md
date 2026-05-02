# COD-DOC — Handbook

> Context Orchestrator for Documentation. Single document covering the whole
> product: что это, как поставить, как использовать через Web/CLI/MCP.
> Скриншоты — реальные, сделаны Playwright-прогоном по seed-проекту.

**Status:** Web UI — 13/14 endpoints shipped (~93 %), 137 e2e tests green;
CLI — все основные сурфейсы (`task`/`plan`/`doc`/`story`/`link`/`revision`/`project`/`agent`/`audit`/`hash`/`tui`);
MCP сервер для интеграции с Claude/Cursor; Docker-stack production-ready.

---

## Содержание

1. [Что такое COD-DOC](#1-что-такое-cod-doc)
2. [Архитектура одним взглядом](#2-архитектура-одним-взглядом)
3. [Установка](#3-установка)
4. [Quick Start (5 минут)](#4-quick-start)
5. [Web UI tour](#5-web-ui-tour) — 8 скриншотов
6. [CLI справочник](#6-cli-справочник)
7. [Конфигурация](#7-конфигурация)
8. [MCP интеграция](#8-mcp-интеграция)
9. [Типичные workflow](#9-типичные-workflow)
10. [Troubleshooting](#10-troubleshooting)
11. [Тестирование и разработка](#11-тестирование-и-разработка)

---

## 1. Что такое COD-DOC

**COD-DOC** = Context Orchestrator for Documentation. Это автономный агент,
который ведёт документацию проекта за тебя:

- Хранит весь граф знаний (документы, секции, задачи, планы, ревизии,
  пользовательские истории, ссылки) в SQLite и проектирует на markdown.
- Пишет revision на каждое изменение — git-style история без ручного
  ведения changelog'ов.
- Автолинкование `[[doc:KEY#anchor]]`-ссылок с каскадным переименованием.
- Frontmatter validation (FM-001..FM-007), task-plan structural validation
  (TP-001..TP-011), sensitivity scanning (SD-001..SD-002).
- Три surface'а с одинаковым capability set: **Web UI**, **CLI**, **MCP** (Claude/Cursor).
- Daemon-режим: фоновый агент следит за MASTER.md и автогенерирует задачи.

**Не цель:** не SPA, не дизайн-система, не replacement for Obsidian.
Web UI — server-rendered Jinja + точечные HTMX-фрагменты, CSS ~370 LOC.

---

## 2. Архитектура одним взглядом

```
                ┌──────────────────────────────────────────────────────┐
                │                  Presentation                         │
                │  ┌─────────┐    ┌────────┐    ┌─────┐    ┌────────┐  │
                │  │ Web UI  │    │  CLI   │    │ TUI │    │  MCP   │  │
                │  │ Jinja+  │    │  click │    │textl│    │ server │  │
                │  │  HTMX   │    │        │    │     │    │        │  │
                │  └────┬────┘    └────┬───┘    └──┬──┘    └───┬────┘  │
                └───────┼──────────────┼───────────┼───────────┼───────┘
                        │              │           │           │
                ┌───────┴──────────────┴───────────┴───────────┴───────┐
                │                    Services                           │
                │   doc · task · plan · story · link · revision         │
                │   projection · sensitivity · validation               │
                └──────────────────────┬───────────────────────────────┘
                                       │
                ┌──────────────────────┴───────────────────────────────┐
                │                    Domain                             │
                │   entities (Plan, Task, Document, Section, ...)       │
                │   pure dataclasses, no DB awareness                   │
                └──────────────────────┬───────────────────────────────┘
                                       │
                ┌──────────────────────┴───────────────────────────────┐
                │             Infra (DB + Repositories)                 │
                │   SQLAlchemy 2.0 · Alembic · embedded SQLite          │
                │   .cod-doc/state.db (per project)                     │
                └──────────────────────────────────────────────────────┘
```

**Слои:** Presentation никогда не импортирует Infra напрямую — только через
Services + DI helpers (`cod_doc.api.deps.get_project_db`). Архитектурное
правило закреплено AST-тестом `tests/api/test_web_layer_imports.py`.

**Главные сущности:**
- `Project` — проект (repo + `.cod-doc/state.db`).
- `Plan` → `PlanSection` → `Task` — иерархия плана. Task с зависимостями
  (`Dependency.kind = blocks`).
- `Document` → `Section` — структурированный markdown. Каждая секция —
  отдельная entity со своими revision'ами.
- `Revision` — каждое изменение entity (TASK/SECTION/DOCUMENT/PLAN).
- `UserStory` — пользовательская история с acceptance criteria.
- `Link` — `[[doc:KEY#anchor]]` ссылки, parsed + tracked.

---

## 3. Установка

### 3.1. Docker (рекомендуется для production)

`docker-compose.yml` уже в репо. Стек: один контейнер `cod-doc`, healthcheck,
порт 8765.

```bash
git clone https://github.com/<org>/cod-doc.git
cd cod-doc

# Положи свой проект под /projects/ через volume mount.
# Открой docker-compose.yml и раскомментируй / добавь:
#   volumes:
#     - cod_doc_data:/data/cod-doc
#     - /path/to/your/project:/projects/my-project:rw

docker compose up -d cod-doc

# Проверка:
curl http://localhost:8765/api/health
# {"status":"ok","configured":false,"projects":0}
```

API-ключ для LLM передаётся через env (см. §7):
```bash
COD_DOC_API_KEY=sk-or-v1-... docker compose up -d cod-doc
```

### 3.2. Локально (dev)

```bash
git clone https://github.com/<org>/cod-doc.git
cd cod-doc

python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# alembic — встроенный admin для миграций embedded SQLite:
alembic upgrade head  # против текущей рабочей директории

cod-doc --help
cod-doc serve  # → http://127.0.0.1:8765
```

Миграции применяются автоматически при первом обращении к проекту через CLI;
явный `alembic upgrade head` нужен только если ты создаёшь `state.db` руками.

---

## 4. Quick Start

### 4.1. Зарегистрировать проект

```bash
# 1. Создай или открой свой проект
cd ~/code/my-app

# 2. Зарегистрируй его в COD-DOC
cod-doc project add my-app .

# 3. Инициализируй .cod-doc/ (создаёт state.db + MASTER.md)
cod-doc project init my-app

# 4. Проверь
cod-doc project list
# my-app    /Users/.../my-app    (enabled)
```

### 4.2. Создать первый план и задачу

```bash
# План в этом проекте
cod-doc plan show <plan_id>          # пока нет — создадим
# (см. §6 — plan-create через MCP/Python; CLI plan create в текущем релизе через interactive)

# Задача (минимальный пример)
cod-doc task create my-app \
  --plan-id 1 --section-id 1 \
  --title "Implement user login" \
  --type feature --priority high
# → MYP-001 created

cod-doc task list my-app
cod-doc task status my-app MYP-001 in-progress
cod-doc task complete my-app MYP-001
```

### 4.3. Открыть Web UI

```bash
cod-doc serve  # или docker compose up -d cod-doc
```

Открой <http://localhost:8765> — увидишь свой проект, кликнешь — попадёшь
на дашборд, увидишь план, задачи, документы, ревизии.

---

## 5. Web UI tour

Все страницы — server-rendered, без JS-сборщика. HTMX подключён только
для inline-редактирования (статус задачи, тело секции). Без JS всё тоже
работает — `<form method="post">` + 303 redirect.

### 5.1. Список проектов — `GET /`

![Projects list](assets/cod-doc/01-index.png)

- Таблица всех зарегистрированных проектов.
- Stats — DB-aggregated (total / done / in_progress).
- Пагинация: `?limit=20&offset=0`, поддерживает «N проектов» масштаб.
- Static-asset versioning: `/static/app.css?v=<mtime-hex>` — кэш-буст на upgrade.

### 5.2. Дашборд проекта — `GET /p/{slug}`

![Project overview](assets/cod-doc/02-overview.png)

7 KPI-карточек + 3 агрегата:

- **Status / Tasks total / Done / In progress / Pending / Failed / Last run** —
  агрегаты из БД (синхронны с Plan-progress блоком ниже).
- **Ready to start** — top-5 задач, готовых к старту, через
  `plan_service.ready` (фильтр по unfulfilled blocks-deps). HTMX `✓`-кнопка
  завершает задачу одним кликом.
- **Plan progress** — мини-таблица планов с прогресс-бар.
- **Recent revisions** — top-5 последних ревизий (newest first).
- **MASTER.md preview** — первые 80 строк, ссылка на полный документ.

### 5.3. Список документов — `GET /p/{slug}/docs`

![Docs list](assets/cod-doc/03-docs-list.png)

Таблица с doc_key / title / type / status / owner / last_updated. Каждая
строка — clickable. Если `.cod-doc/state.db` отсутствует — graceful warning
вместо 500.

### 5.4. Документ — `GET /p/{slug}/docs/{doc_key:path}`

![Doc detail](assets/cod-doc/04-doc-show.png)

- Slug-компонент в URL — поддерживает вложенные пути (`modules/M1/spec`).
- Sidebar с anchor-нав. Клик → smooth scroll к секции.
- Mini-renderer markdown'а (~110 LOC, без новых deps): paragraphs,
  fenced code, bullet lists, inline `code`/**bold**/*italic*/[link](url).
  HTML-escapes all input.
- ✎-кнопка возле каждой секции → swap в `<textarea>`-форму с hidden
  `expected_parent_revision_id` (optimistic concurrency).
- `?raw=1` → fallback на `<pre>`-режим.

**Inline section editing (HTMX flow):**

```
[GET /docs/foo]
  ↓ click ✎
[GET /docs/foo/sections/data-model/edit]   → swap to textarea
  ↓ edit + Save
[POST /docs/foo/sections/data-model]       → patch_section + revision
  ↓ swap-back to view fragment
[GET /docs/foo]                             → updated section visible
```

Если другой автор обновил секцию между `edit` и `Save` — `RevisionConflictError`
→ `ConflictWebError(409)` → alert-warning поверх через `hx-swap-oob`.

### 5.5. Задачи — `GET /p/{slug}/tasks`

![Tasks](assets/cod-doc/05-tasks.png)

- Таблица: ID / Title / Type / Status / Priority / Plan / Section.
- Status badge color-coded; priority text-coded.
- Filter: `?status=pending` (dropdown autosubmit, без JS работает как form).
- HTMX inline status update: change `<select>` → `POST .../status` →
  swap row. Conflict / validation error → OOB alert.
- ✓-кнопка из «Ready to start» блока тоже сюда персистится.

### 5.6. Планы — `GET /p/{slug}/plans` + `GET /p/{slug}/plans/{plan_id}`

![Plan detail](assets/cod-doc/06-plans.png)

Список планов с прогрессом → детальная страница плана:
- **Header** — scope / principle / status / done/total/percent.
- **Section progress** — таблица секций с progress bar.
- **Next batch (ready to start)** — top-7 ready, тот же `✓`-flow что и на дашборде.
- **Dependency graph** — `<pre>` с mermaid-syntax (interactive рендер
  отложен до vendor'а `mermaid.min.js` — пока копипаст в mermaid.live).
- **Raw export** — `<details>` с markdown-проекциями (`progress_overview`,
  `next_batch`).

Cross-project guard: `/p/B/plans/<id-from-A>` → 404, не утечка.

### 5.7. Ревизии — `GET /p/{slug}/revisions`

![Revisions](assets/cod-doc/07-revisions.png)

- Newest-first project-wide log.
- Фильтр `?entity_kind=task|document|section|plan&entity_id=42`.
- 6 колонок: revision_id / entity#id / author / at / reason / diff first line.
- Diff показан превью (первые 200 символов первой строки).

### 5.8. Settings — `GET /settings` / `POST /settings`

![Settings](assets/cod-doc/08-settings.png)

- API key — masked (`…XXXX`), никогда не в plaintext.
- Базовые поля LLM (base_url, model, max_tokens, embedding_model).
- Agent-параметры (max_iterations, daemon interval, auto_commit checkbox).
- **Secret-field UX:** пустое значение api_key = «оставить как есть»;
  явный дефис `-` = удалить; непустая строка = заменить.
- POST → 303 на `/settings`, форма работает без JS.

---

## 6. CLI справочник

Все команды через `cod-doc <group> <action>`. Примеры — на gist-уровне,
полный help: `cod-doc --help`, `cod-doc <group> --help`.

### project

```bash
cod-doc project add my-app /path/to/repo    # зарегистрировать
cod-doc project init my-app                  # создать .cod-doc/state.db + MASTER.md
cod-doc project list                          # все проекты
cod-doc project status my-app                # подробности
cod-doc project remove my-app                # из реестра (репо не трогает)
```

### task

```bash
cod-doc task create my-app --plan-id 1 --section-id 1 \
    --title "Implement login" --type feature --priority high
cod-doc task list my-app                     # все
cod-doc task list my-app --status pending    # фильтр
cod-doc task show my-app MYP-001
cod-doc task status my-app MYP-001 in-progress
cod-doc task complete my-app MYP-001
```

### plan

```bash
cod-doc plan show my-app <plan_id>
cod-doc plan ready my-app <plan_id>          # топ-N готовых задач
cod-doc plan audit my-app <plan_id>          # циклы, done-with-unfinished-blocks
cod-doc plan critical-path my-app <plan_id>  # longest sequential chain
cod-doc plan forward my-app <task_id>        # what must complete BEFORE
cod-doc plan reverse my-app <task_id>        # what this task UNBLOCKS
cod-doc plan export my-app <plan_id>         # markdown projections
```

### doc

```bash
cod-doc doc create my-app --doc-key modules/auth/spec \
    --type module-spec --title "Auth Module Spec"
cod-doc doc list my-app
cod-doc doc show my-app modules/auth/spec
cod-doc doc body my-app modules/auth/spec    # full rendered markdown
cod-doc doc rename my-app modules/auth/spec modules/identity/spec
cod-doc doc export my-app modules/auth/spec  # → projection .md file
cod-doc doc drift my-app                      # find docs that diverged from DB
cod-doc doc import my-app path/to/file.md     # import existing markdown
```

### story (user-stories)

```bash
cod-doc story create my-app --as-a "registered user" \
    --i-want "to reset my password" --so-that "I regain access"
cod-doc story list my-app
cod-doc story show my-app US-001
cod-doc story add-criterion my-app US-001 "Email with reset link is sent within 60s"
cod-doc story coverage my-app US-001         # related tasks/docs
cod-doc story link my-app US-001 --task MYP-001
cod-doc story status my-app US-001 accepted
```

### link

```bash
cod-doc link list my-app                     # все ссылки
cod-doc link verify my-app                   # broken refs?
cod-doc link sync my-app                     # rescan + reparse
```

### revision

```bash
cod-doc revision list my-app --kind task --id 42
cod-doc revision show my-app <revision_id>
cod-doc revision revert my-app <revision_id>  # WHERE supported
```

### audit

```bash
cod-doc audit my-app                         # frontmatter + task-plan + sensitivity
cod-doc audit my-app --strict                # advisory issues тоже фейлят exit-code
```

### serve / mcp / agent / tui / hash

```bash
cod-doc serve --host 0.0.0.0 --port 8765 [--reload]
cod-doc mcp                                  # MCP server (stdio)
cod-doc agent run my-app                     # interactive agent loop
cod-doc tui                                  # textual-based TUI
cod-doc hash calc my-app modules/foo         # content hash
cod-doc hash update my-app modules/foo
```

---

## 7. Конфигурация

### 7.1. `~/.cod-doc/config.yaml`

Создаётся автоматически. Поля:

```yaml
api_key: sk-or-v1-XXXXXXXX                   # OpenRouter
base_url: https://openrouter.ai/api/v1
model: anthropic/claude-sonnet-4-6
max_tokens: 8192

auto_commit: false                            # auto-git after task done
max_iterations: 50
agent_interval: 60                            # daemon poll seconds

chroma_path: ~/.cod-doc/chroma
embedding_model: openai/text-embedding-ada-002

api_host: 0.0.0.0
api_port: 8765

projects:
  - name: my-app
    path: /Users/me/code/my-app
    enabled: true
    auto_commit: false
    master_md: MASTER.md
```

### 7.2. Environment variables

Все поля можно переопределить через `COD_DOC_*`:

```bash
COD_DOC_HOME=/data/cod-doc                   # alternative ~/.cod-doc
COD_DOC_API_KEY=sk-or-v1-...
COD_DOC_MODEL=anthropic/claude-sonnet-4-6
COD_DOC_BASE_URL=https://openrouter.ai/api/v1
COD_DOC_AUTO_COMMIT=true
COD_DOC_AGENT_INTERVAL=120
COD_DOC_API_HOST=127.0.0.1
COD_DOC_API_PORT=9000
COD_DOC_DB_URL=postgresql://user:pass@host/db   # server mode (otherwise embedded sqlite)
LOG_LEVEL=INFO
LOG_FORMAT=text                              # or json
```

Web UI `/settings` работает с тем же `Config.save()` — изменения попадают
в `~/.cod-doc/config.yaml` и подхватываются на следующем lifespan-старте.

---

## 8. MCP интеграция

См. отдельный гайд: [docs/mcp-integration.md](mcp-integration.md).

Кратко: `cod-doc mcp` запускает MCP-сервер по stdio; tools покрывают
тот же сервис-слой, что и CLI/Web. Подключение в Claude Desktop:

```json
{
  "mcpServers": {
    "cod-doc": {
      "command": "cod-doc",
      "args": ["mcp"]
    }
  }
}
```

После рестарта Claude увидит инструменты `task_create`, `doc_show`,
`plan_ready`, и т. д.

---

## 9. Типичные workflow

### 9.1. Документировать новый модуль

```bash
# 1. Создать документ-болванку
cod-doc doc create my-app \
    --doc-key modules/payments/spec \
    --type module-spec \
    --title "Payments Module Spec" \
    --owner "human:dakh"

# 2. Открыть в Web → /p/my-app/docs/modules/payments/spec
#    Кликнуть ✎ возле секции "Data Model" → редактировать → Save

# 3. Из CLI: добавить раздел программно
.venv/bin/python <<'PY'
from cod_doc.config import Config
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.services import doc_service as docs

cfg = Config.load()
entry = cfg.get_project("my-app")
engine = make_engine(f"sqlite:///{entry.cod_doc_dir}/state.db")
factory = make_session_factory(engine)
with transactional(factory) as s:
    # ... lookup proj_id and doc_id, then:
    docs.add_section(s, document_id=DOC_ID, anchor="api",
                     heading="API", level=2, position=1,
                     body="POST /payments/intent\nPOST /payments/attempt/{id}",
                     author="human:dakh")
PY
```

### 9.2. Управлять прогрессом задачи

```bash
# Через CLI
cod-doc task status my-app PAY-001 in-progress
# ... работа ...
cod-doc task complete my-app PAY-001

# Через Web — нажать ✓ в Ready блоке на дашборде или перейти в /tasks
# и сменить статус через select.

# Через MCP — Claude вызывает task_status через свой инструментарий.
```

### 9.3. Откатить изменение

```bash
cod-doc revision list my-app --kind section --id 42
# 01HXXX...   2026-05-02 16:55  human:web  : web inline section patch
# 01HYYY...   2026-05-02 16:30  human:dakh : add_section
# ...

cod-doc revision revert my-app 01HXXX...
# Восстанавливает body секции до состояния до этой ревизии
# (поддерживается для SECTION; для TASK — set status, для DOC — TBD).
```

### 9.4. Прогнать audit перед merge

```bash
cod-doc audit my-app --strict
# Frontmatter:  FM-002 (3) FM-003 (1) FM-004 (12) ...
# Task-plan:    TP-001 (0) TP-002 (1) TP-003 (0) ...
# Sensitivity:  SD-001 high-conf-leak: 0
# Exit code:    1   ← из-за advisory FM-004 в strict mode
```

---

## 10. Troubleshooting

### Контейнер 500'ит на странице проекта

`TemplateNotFound: '_layout/project_tabs.html'` или ему подобный —
значит инсталлированный пакет отстаёт от исходников. Перебилди:
```bash
docker compose build cod-doc && docker compose up -d cod-doc
```

`pyproject.toml` `package-data` глобы должны покрывать все папки в
`templates/web/` и `static/` (см. фикс `a0861cb`). Регрессионный AST-тест
держит web layer в чистоте, но новый template-subdir = новый glob.

### KPI карточки на дашборде показывают 0, а Plan progress — реальные числа

Уже не должно — закрыто фиксом `e8512e6`. Если воспроизводится:
- Вероятнее всего, `.cod-doc/tasks.yaml` пуст (legacy YAML) и контейнер
  работает на старой версии. Перебилди.

### «Ревизий пока нет.» при существующих задачах

Это **состояние данных**, не баг. 31 task в БД был создан напрямую
(SQL/fixture), минуя `task_service.create` (который пишет revision).
Все новые operations через сервис-слой автоматически наполняют лог.

Чтобы стартовать с чистого слайта:
```bash
rm -rf .cod-doc
cod-doc project init my-app  # пересоздать
```

### MASTER.md показывает мусор от тестов

```bash
rm /path/to/repo/MASTER.md
cod-doc project init my-app  # init не перезапишет существующий
```

`Project.init()` намеренно не перезаписывает MASTER.md — defensive default
(чтобы не затереть твой настоящий навигатор). Удаляй вручную.

### `Internal Server Error` без TemplateNotFound

`docker logs cod-doc --tail 100` или `journalctl -u cod-doc-serve -e` для
systemd. Стек-трейс покажет конкретную причину. Самые частые:
- Schema mismatch — старая `state.db`, накатить `alembic upgrade head`.
- Slug в config.yaml не совпадает с `project.slug` в БД — пересоздать
  через `cod-doc project init`.

### HTMX не работает (формы перезагружают страницу целиком)

- Проверь, что `/static/htmx.min.js` отдаётся 200, не 404.
  ```bash
  curl -I http://localhost:8765/static/htmx.min.js
  ```
  404 = `package-data` глоб не покрывает `static/*.js` → перебилди.
- `<noscript>` fallback тоже работает — `<form method="post">` шлёт обычный
  POST, handler возвращает 303-redirect. UI всегда graceful-degrades.

---

## 11. Тестирование и разработка

### 11.1. pytest

```bash
.venv/bin/pytest tests/ -q             # full suite (~512 tests, ~110s)
.venv/bin/pytest tests/api/ -q         # web only (~137, ~30s)
.venv/bin/pytest tests/services/ -q    # service layer
.venv/bin/pytest tests/infra/ -q       # repositories + migrations
```

CI-блок (locked since COD-024a):
- pytest matrix `3.11 / 3.12 / 3.13` — blocking
- ruff — blocking
- mypy strict — blocking

### 11.2. Lint / types

```bash
.venv/bin/ruff check cod_doc tests
.venv/bin/ruff format cod_doc tests
.venv/bin/mypy cod_doc
```

### 11.3. Playwright e2e (опционально)

Не часть CI; runner лежит в `.cod-doc-playwright-*.py` (untracked).
Прогон:

```bash
# 1. Seed sandbox (создаёт /tmp/cod-doc-playwright-sandbox/)
.venv/bin/python .cod-doc-playwright-seed.py

# 2. Запустить сервер на seed
COD_DOC_HOME=/tmp/cod-doc-playwright-sandbox/home \
    .venv/bin/uvicorn cod_doc.api.server:app --port 8765

# 3. Прогнать сценарий
.venv/bin/python .cod-doc-playwright-run.py
# → Playwright e2e summary: 23 passed, 0 failed

# 4. Скриншоты для документации
.venv/bin/python .cod-doc-playwright-screenshots.py
# → /tmp/cod-doc-playwright-shots/{01..08}.png
```

### 11.4. Migrations

```bash
# Новая миграция
alembic revision --autogenerate -m "0010_add_payment_tables"

# Применить
alembic upgrade head

# Откатить шаг назад
alembic downgrade -1

# Проверить состояние
alembic current
alembic history --verbose
```

`COD_DOC_DB_URL` управляет таргетом: `sqlite:///path/to/state.db` для
embedded, `postgresql://...` для server mode.

### 11.5. Architectural rules (auto-enforced)

- **Web layer не импортирует `cod_doc.infra.*`** —
  `tests/api/test_web_layer_imports.py` ловит регрессию AST-сканированием.
- **Сервисы пишут revision на каждый mutation** — пустые revision-таблицы =
  signal что что-то идёт мимо service-слоя.
- **Frontmatter validation** — `FM-002` (active без owner) и `FM-003`
  (sot=false без canonical_source) эскалируются в `ValidationError` на
  write-path; freshness-правила (`FM-004/005`) advisory.

---

## Дальше

- **Целевая архитектура:** [docs/system/MASTER.md](system/MASTER.md) — пакет
  capability-документов и стандартов (source of truth).
- **Roadmap:** [docs/system/roadmap/cod-doc-task-plan.md](system/roadmap/cod-doc-task-plan.md)
  и [web-frontend-task-plan.md](system/roadmap/web-frontend-task-plan.md).
- **Audit-trail:** [docs/system/audit/](system/audit/) — закрытые секции
  и checkpoint'ы.
- **Restate migration guide:** [docs/system/migration/from-restate.md](system/migration/from-restate.md).
