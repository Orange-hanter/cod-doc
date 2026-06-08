---
type: kickoff-brief
scope: web-frontend / Section F (Hardening) → Section B remainder
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/web-frontend-task-plan.md
owner: cod-doc core
created: 2026-05-02
last_updated: 2026-05-02
audience: [next-session-agent, contributors]
---

# Web Frontend — Kickoff Brief (2026-05-02)

> **Назначение.** Точка входа для следующего сеанса работы по web-секции.
> Содержит: контекст, состояние, первый tick, критерии готовности, команды.
>
> **Не source of truth.** Канонические документы — capability и roadmap
> (см. `canonical_source` в frontmatter). Этот файл живёт до закрытия Section F,
> после чего архивируется.

## 1. TL;DR

- Web-секция: **5/14 endpoints** реализованы (WEB-001..003, WEB-010, WEB-011).
  Section A (Scaffold) закрыта.
- 2026-05-02 проведён аудит → 13 новых задач, 2 повышены до high.
- **Корень проблем — отсутствие фундамента**: engine на каждый запрос (perf),
  web → infra прямые импорты (architecture), `<div id="alerts">` без модели (UX).
- **Batch-1 закрыт 2026-05-02:** WEB-005, WEB-040, WEB-022, WEB-041, WEB-013 ✅.
  11 / 16 находок baseline закрыты; checkpoint #1 → [batch-1](../audit/2026-05-02-checkpoint-web-batch-1.md).
- **Batch-2 закрыт 2026-05-02:** WEB-006 (markdown), polish (013b/022b/054),
  WEB-014 (overview agg + complete), WEB-021 (revisions log) ✅.
  13 / 16 находок baseline закрыты; suite 441 → 483; endpoints 5→8/14;
  checkpoint #2 → [batch-2](../audit/2026-05-02-checkpoint-web-batch-2.md).
- **Batch-3 закрыт 2026-05-02:** WEB-004 (plan view), WEB-060 (settings),
  WEB-051 (asset versioning) ✅. **Section B closed (6/6).**
  14 / 16 находок baseline закрыты; suite 483 → 500; endpoints 8→10/14;
  checkpoint #3 → [batch-3](../audit/2026-05-02-checkpoint-web-batch-3.md).
- **Batch-4 закрыт 2026-05-02:** WEB-012 (section patch), polish bundle
  (WEB-052/053/053b/014b) ✅. **Section C closed (3/3).**
  **16 / 16 baseline findings закрыты — audit `2026-05-02-section-web-frontend`
  переведён в `resolved`.** Suite 500 → 512; endpoints 10→13/14;
  checkpoint #4 → [batch-4](../audit/2026-05-02-checkpoint-web-batch-4.md).
- **Следующий шаг:** WEB-030 (SSE run console) — последний endpoint и
  последний disabled-таб. Возможно, отдельной сессией — нужна
  интеграция с `Orchestrator` и `hx-ext="sse"`.

## 2. Где что лежит

| Документ | Назначение |
|---|---|
| [docs/system/audit/2026-05-02-section-web-frontend.md](../audit/2026-05-02-section-web-frontend.md) | Аудит-отчёт. 16 находок, severity, links на код. |
| [docs/system/capabilities/web-frontend.md](../capabilities/web-frontend.md) | Capability (целевое поведение). §3 — маршруты со status. §7 — DI-конвенция. §11 — текущее состояние. |
| [docs/system/roadmap/web-frontend-task-plan.md](web-frontend-task-plan.md) | Execution plan. Section A..F. Backlog продуктивности. Mermaid-граф. |
| [cod_doc/api/web/](../../../cod_doc/api/web/) | Код: pages, fragments, db_resolver (на удаление), templates_env. |
| [cod_doc/templates/web/](../../../cod_doc/templates/web/) | Шаблоны: base, index, project/*, _frag/. |
| [tests/api/test_web_*.py](../../../tests/api/) | 27 тестов, все зелёные. |

## 3. Состояние реализации (матрица)

| ID | Title | Section | Status | Priority |
|---|---|---|:---:|:---:|
| WEB-001 | Scaffold | A | ✅ done | critical |
| WEB-002 | Project page | A | ✅ done | high |
| WEB-003 | Docs view | A | ✅ done | high |
| WEB-010 | Tasks list | B | ✅ done | high |
| WEB-011 | Task status HTMX | C | ✅ done | high |
| WEB-005 | Engine cache + DI | F | ✅ done 2026-05-02 | high |
| WEB-040 | Remove infra bypass | E | ✅ done 2026-05-02 | high |
| WEB-022 | Alerts/error model | C | ✅ done 2026-05-02 | high |
| WEB-041 | Tabs include + disabled | E | ✅ done 2026-05-02 | medium |
| WEB-013 | Index batch stats | F | ✅ done 2026-05-02 | high |
| WEB-006 | Markdown render для doc_show | B | ✅ done 2026-05-02 | medium |
| WEB-013b | empty-page summary clamp | F | ✅ done 2026-05-02 | low |
| WEB-022b | log WebError events | C | ✅ done 2026-05-02 | low |
| WEB-054 | flash_message length cap | F | ✅ done 2026-05-02 | low |
| WEB-014 | Overview agg + complete | B | ✅ done 2026-05-02 | medium |
| WEB-021 | Revisions log | B | ✅ done 2026-05-02 | medium |
| WEB-004 | Plan view + Mermaid | B | ✅ done 2026-05-02 | high |
| WEB-060 | Settings page | B | ✅ done 2026-05-02 | medium |
| WEB-051 | Asset versioning | F | ✅ done 2026-05-02 | low |
| WEB-012 | Section patch HTMX | C | ✅ done 2026-05-02 | high |
| WEB-052 | Error-branch tests | F | ✅ done 2026-05-02 | low |
| WEB-053 | Test hygiene (cache+alembic) | F | ✅ done 2026-05-02 | medium |
| WEB-053b | Tab fixture consolidation | F | ✅ done 2026-05-02 | low |
| WEB-014b | task complete next_url | B | ✅ done 2026-05-02 | low |
| **WEB-030** | **SSE run console** | **D** | **❌ next** | **medium** |
| WEB-031 | Import progress | D | ❌ pending | low |
| WEB-042 | Doc/code §3 sync | E | ❌ pending | medium |
| WEB-050 | Session DI pattern | F | ❌ pending | medium |

28 total · **24 done / 4 pending** · expected order:
WEB-030 → WEB-031 → WEB-042 + WEB-050 (cleanup bundle).

## 4. Первый tick — WEB-005 (Engine cache + DI helper)

**Цель.** Заменить per-request `make_engine + dispose` на кэшированный engine
с TTL по mtime; ввести `get_project_db` FastAPI-dependency, чтобы убрать
бойлерплейт из обработчиков и подготовить почву для WEB-040.

**Файлы:**
- [cod_doc/api/deps.py](../../../cod_doc/api/deps.py) — добавить
  `get_engine_for_slug`, `get_project_db`, `dispose_all_engines`.
- [cod_doc/api/server.py](../../../cod_doc/api/server.py) — в lifespan
  shutdown вызвать `dispose_all_engines()`.
- historical `cod_doc/api/web/db_resolver.py` —
  оставить пока что; **в WEB-040 удалится**. Внутри переписать на использование
  кэша из deps (минимальное изменение, чтобы тесты остались зелёными).
- `tests/api/test_deps_engine_cache.py` — **NEW**.

**Acceptance (повторяю из плана):**
1. `get_engine_for_slug(slug)` возвращает закэшированный engine; кэш —
   `dict[Path, tuple[Engine, float]]` с TTL по mtime файла state.db.
2. `get_project_db(slug) -> Iterator[tuple[Session, int]]` — yield-style
   FastAPI dependency, закрывает session после response.
3. `dispose_all_engines()` вызывается в `app.lifespan` shutdown.
4. Перфтест/микротест: 100 sequential `GET /p/{slug}/tasks` ускоряются за счёт
   кэша. Записать измеренное число (X→Y ms).
5. 3 теста: cache hit, cache invalidation по mtime, dispose-on-shutdown.
6. Все 27 существующих тестов остаются зелёными.

**Архитектурные вопросы для размышления:**
- **mtime vs explicit invalidation.** mtime прост, но fs-кэш на macOS/Linux
  имеет 1-секундное разрешение. Для embedded sqlite это норма (write-флоу
  меняет файл явно). Альтернатива — events. Старт: mtime, переходим если
  всплывут гонки.
- **TTL vs вечный кэш.** Вечный кэш + mtime-check на каждый lookup безопасен,
  но lookup стоит fs-stat. TTL 5 секунд — компромисс, после чего stat-проверка.
  Старт: TTL=5s + stat-on-stale.
- **Поведение при `OperationalError`** (схема не накатана). Вернуть `None` →
  обработчик отдаёт graceful warning, как сейчас в `db_resolver.py`. Не падать.

## 5. Команды для разработки

```bash
# Зелёный suite до начала
.venv/bin/pytest tests/ -q

# Запустить только web-тесты
.venv/bin/pytest tests/api/ -q

# Запустить dev-сервер (опционально для ручной проверки)
.venv/bin/uvicorn cod_doc.api.server:app --reload --port 8765
# затем GET http://localhost:8765/

# Линт + типы
.venv/bin/ruff check cod_doc tests
.venv/bin/mypy cod_doc

# Перед коммитом
.venv/bin/pytest tests/ -q && .venv/bin/ruff check cod_doc tests && .venv/bin/mypy cod_doc
```

## 6. Definition of Done для Section F

Section F закрывается, когда:

- [x] WEB-005 done — engine кэшируется; `get_project_db` доступен. (2026-05-02)
- [x] WEB-040 done — `db_resolver.py` удалён; ruff banned-imports правило работает;
      audit `2026-04-28-section-c-capabilities.md` переведён в `resolved`. (2026-05-02)
- [x] WEB-022 done — `WebError` + middleware + `_frag/alert.html`;
      `<div id="alerts">` живой. (2026-05-02)
- [x] WEB-013 done — index загружается за один проход для N=20. (2026-05-02)
- [ ] WEB-050 done — DI-pattern зафиксирован в capability §7 как
      «единственно верный».
- [ ] WEB-051, WEB-052, WEB-053 done — versioning, error-branch coverage,
      conftest extract.
- [ ] WEB-013b/022b/054 (sub-tickets из checkpoint-аудита) — закрыты.
- [ ] Suite зелёный (>66 тестов, на каждой задаче пометить добавленные).
- [ ] Audit-отчёт `2026-05-02-section-web-frontend.md` переведён в `resolved`.
- [ ] Capability §11 «Текущее состояние» обновлено: endpoints shipped,
      LOC, tests; «Архитектурный долг» очищен от закрытых строк.

## 7. Известные ADR-вопросы (отложено до своего времени)

| Когда | Вопрос | Варианты |
|---|---|---|
| WEB-006 | Markdown rendering | (a) `markdown-it-py` (новая dep) (b) собственный mini-renderer поверх `DocService` (без deps) |
| WEB-013 | Async stats или single-pass | (a) `asyncio.gather` + кэш (b) глобальная DB-агрегация (требует cross-DB) |
| WEB-022 | Alerts: cookie-flash или session | (a) Signed cookie (b) Server-side session (новая dep `itsdangerous` уже у FastAPI) |
| WEB-030 | SSE: in-memory pub/sub или EventBridge | (a) inmem (b) общий с CLI/MCP через event bus (новая абстракция) |
| P-3 (palette) | Search backend | (a) SQL LIKE (b) FTS5 (c) chromadb (уже есть) |

Каждый ADR — отдельная record в [docs/system/capabilities/decisions-and-questions.md](../capabilities/decisions-and-questions.md)
после старта соответствующей задачи.

## 8. Чек-лист «прежде чем закрывать любую web-задачу»

Каждый PR в web-секции проверяется на:

- [ ] Только `cod_doc.services.*`, `cod_doc.api.deps`, `cod_doc.domain.entities` (enums)
      импортируются в `cod_doc/api/web/`. Никакого `infra.*`.
- [ ] HTMX-fragment имеет fallback на `<form>` без JS (POST/Redirect/GET).
- [ ] Error-branch coverage: validation/conflict/integrity/domain — все 4 ветки
      имеют тесты.
- [ ] Capability §3 (таблица маршрутов) обновлена: status `✅` + ссылка на task id.
- [ ] Capability §11 (метрики, текущее состояние) обновлено если изменились
      числа.
- [ ] Audit-отчёт от 2026-05-02: если фикс закрывает находку — поставить ✅
      рядом с её SW-кодом.
- [ ] Roadmap §Progress Overview пересчитан.
- [ ] Suite зелёный, новые тесты названы по `WEB-XXX`-префиксу.

## 9. Risk register (что может сломаться)

| Риск | Митигейшн |
|---|---|
| Engine cache не инвалидируется при внешнем `cod-doc db migrate` | mtime-check на lookup ловит это; +тест с touch state.db |
| Удаление `db_resolver.py` ломает orphan-импорты где-то | grep по репо в WEB-040; banned-imports rule в ruff после rm |
| `WebError` middleware конфликтует с `routes.py` exception-handler-ами | Зарегистрировать через `app.exception_handler(WebError)` (не middleware) — изолированный scope; тестировать оба роутера |
| Markdown-it-py добавляет ~150 KB к зависимостям | См. ADR в WEB-006; выбор будет зафиксирован до начала задачи |
| SSE и Background Daemon (run_daemon) конкурируют за asyncio loop | WEB-030 деplore-ить с явным task-pool; интеграционный тест |

## 10. Что точно НЕ делаем в Section F

- Не добавляем новые маршруты (Section B/C ждут).
- Не трогаем CSS-дизайн (capability §1 это запрещает).
- Не вводим dark mode, иконки-эмодзи, анимации.
- Не пишем `markdown-it-py` интеграцию (это WEB-006).
- Не пытаемся «по-быстрому» закрыть P-1..P-15 backlog'а — они в очереди.

---

## 11. Changelog

| Дата | Событие |
|---|---|
| 2026-05-02 | Создан kickoff-brief после аудита веб-секции. Точка входа в Section F. |
