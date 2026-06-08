---
type: audit-report
scope: cod_doc/api/web/* + cod_doc/templates/web/* + cod_doc/static/* (Web frontend)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-05-02
last_updated: 2026-05-02
audit_target_revision: HEAD = ff7f92c (post Section G hardening, WEB-001..003, WEB-010, WEB-011)
related_docs:
  - ../MASTER.md
  - ../capabilities/web-frontend.md
  - ../roadmap/web-frontend-task-plan.md
  - 2026-04-28-section-c-capabilities.md
  - 2026-05-02-checkpoint-web-batch-1.md
  - 2026-05-02-checkpoint-web-batch-2.md
  - 2026-05-02-checkpoint-web-batch-3.md
  - 2026-05-02-checkpoint-web-batch-4.md
---

# Web Frontend — System Audit (2026-05-02)

> Точечный аудит web-секции после закрытия Section A (Scaffold) и точечной реализации в Section B/C
> (`WEB-010` tasks list, `WEB-011` HTMX status). Проверяем: соответствие capability,
> архитектурные нарушения, производительность, UX-связность, тестовое покрытие.
> Цель — зафиксировать долг ДО старта `WEB-004/020/021/030` и Section E (`WEB-040`),
> чтобы не закладывать те же проблемы в новые страницы.

## 0. Базовая статистика

| Метрика | Значение |
|---|---|
| LOC python (`cod_doc/api/web/*.py`) | 363 |
| LOC templates (`cod_doc/templates/web/**`) | 280 |
| LOC `app.css` | 211 |
| Vendored `htmx.min.js` | ~50 KB |
| Realised endpoints | 5 (`GET /`, `GET /p/{slug}`, `GET /p/{slug}/docs`, `GET /p/{slug}/docs/{key:path}`, `GET /p/{slug}/tasks`, `POST /p/{slug}/tasks/{id}/status`) |
| Capability §3 endpoints total | 14 |
| Coverage capability §3 | **5/14 (36 %)** |
| Web-tests | 27 (`27 passed in 10.96s`) |

Ядро здоровое, но 9 маршрутов из спеки ещё не существуют, а под закрытыми скрытно
накопились архитектурный долг и UX-битые ссылки.

## Сводка

| Severity | Count | Inline fix | New task | Deferred |
|---|---:|---:|---:|---:|
| critical | 0 | — | — | — |
| high | 4 | — | 4 (WEB-040 ↑, WEB-005, WEB-013, WEB-022 ↑) | 0 |
| medium | 7 | — | 6 (WEB-006, WEB-014, WEB-041, WEB-042, WEB-050, WEB-060) | 1 (markdown rendering→COD-070) |
| low | 5 | — | 3 (WEB-051, WEB-052, WEB-053) | 2 |
| **итого** | **16** | **0** | **13** | **3** |

Inline-fix'ов нет намеренно: каждое замечание ниже либо аффектит ≥2 будущие задачи,
либо требует архитектурного решения (см. §1.SW-HI-1). Закрывать их «по дороге»
к WEB-004/020/021 = повторить ту же ошибку, что и SC-HI-3 (web обходит сервисы)
— проблему пропустили в WEB-001..003, потому что её не зафиксировали отдельной задачей.

---

## 1. High

### SW-HI-1. Web → infra bypass всё ещё не закрыт (WEB-040)

**Где:** historical `cod_doc/api/web/db_resolver.py:22-23` (удалён в WEB-040)

```python
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.repositories import ProjectRepository
```

**Симптом:** `cod_doc.api.web` напрямую импортирует `cod_doc.infra.*`. Нарушает
[capabilities/web-frontend.md §7](../capabilities/web-frontend.md):
«Web-страница не имеет права обходить сервис. Разрешённые модули — только `cod_doc.services.*` и `cod_doc.api.deps`».

Заявлено как WEB-040 (Section E, priority `medium`) ещё 2026-04-28. С тех пор —
никаких изменений; при этом `WEB-011` (закрыт) добавил **второй** call-site
(`fragments.py` через тот же `db_resolver`). Чем дольше тянем, тем больше код
зацепится за этот резолвер.

**Что нужно:**
1. Перенести резолв «slug → DB session + project_db_id» в `cod_doc.api.deps` как
   FastAPI `Depends`-функцию (`get_project_db`). Возвращает `(Session, project_db_id)`
   или поднимает `HTTPException(404)`.
2. Engine/factory кэшировать (см. SW-HI-2) — а не плодить per-request.
3. `cod_doc/api/web/db_resolver.py` удалить.
4. `pages.py` / `fragments.py` зависят только от `cod_doc.services.*` + `cod_doc.api.deps`.
5. Поднять приоритет WEB-040 до **high** и поставить блокером для **любой** новой
   write-path фичи (`WEB-012`, `WEB-014`).

### SW-HI-2. Engine создаётся на каждый HTTP-запрос (perf cliff)

**Где:** historical `cod_doc/api/web/db_resolver.py:44-59` (удалён в WEB-040)

```python
engine = make_engine(f"sqlite:///{db_path}")
factory = make_session_factory(engine)
session = factory()
try:
    ...
finally:
    session.close()
    engine.dispose()
```

**Симптом:** на каждый `/p/{slug}/...`-запрос создаётся новый SQLAlchemy `Engine`
(включая connection pool, pragma init, registration), используется один SELECT,
затем `dispose()`. Стоимость на холодный SSD-диск для embedded SQLite — 5–15 ms;
на сетевую FS (NFS, syncthing) — 50–200 ms. Для index page при 10 проектах —
**100×** этот overhead (см. SW-HI-3).

**Что нужно:** новая задача **WEB-005 — DB engine cache** (priority `high`):
- кэш `dict[Path, Engine]` с TTL-инвалидацией по mtime файла state.db,
- инициализация в `lifespan`, чистка в shutdown,
- интеграция с FastAPI DI (`get_engine_for_slug`).

### SW-HI-3. N+1 на index page (`Project.stats()` per project)

**Где:** [cod_doc/api/web/pages.py:27-35](../../../cod_doc/api/web/pages.py)

```python
for entry in cfg.list_projects():
    projects.append({..., "stats": Project(entry).stats()})
```

**Симптом:** `Project.stats()` для каждого проекта читает либо `state.db`, либо
файлы. Для N проектов — N последовательных I/O, и кэш нет. UI замирает на
`GET /` пропорционально числу зарегистрированных проектов.

**Что нужно:** новая задача **WEB-013 — projects listing batch stats**
(priority `high`, depends on WEB-005):
- batch-метод `ProjectRepository.list_with_stats(slugs)` (или мульти-DB агрегация),
- лимит на отображаемое количество (top-20 по `last_run`) с пагинацией,
- placeholder skeleton на стороне HTMX, если N большое.

### SW-HI-4. WEB-022 (alert/error model) висит → ошибки молча теряются

**Где:** [cod_doc/templates/web/base.html:18](../../../cod_doc/templates/web/base.html), [cod_doc/api/web/fragments.py:80-97](../../../cod_doc/api/web/fragments.py)

**Симптом:** `<div id="alerts">` в `base.html` существует, но никто туда не пишет.
HTMX-фрагмент при ошибке (`RevisionConflictError`, `IntegrityError`, `ValueError`)
возвращает inline `<span class="row-error">{{ error }}</span>` — текст без структуры,
без severity, без dismissable-кнопки. Конфликты ревизий сейчас выглядят как
«красная подпись пропала после следующего HTMX-обновления» — пользователь
теряет инцидент.

WEB-022 заявлен как `medium` и зависит от **WEB-011, WEB-012**. WEB-012 ещё `pending`
→ WEB-022 формально разблокирован уже сейчас (т. к. WEB-011 закрыт), но в плане
он стоит после WEB-012, что некорректно. Поднимаем до `high` и развязываем зависимость:
WEB-022 должна закрываться сразу после `WEB-040 + WEB-005` — это шина для всех
будущих write-path.

---

## 2. Medium

### SW-ME-1. Дублирование таб-навигации (4 копии)

**Где:** [show.html:9-16](../../../cod_doc/templates/web/project/show.html), [docs_list.html:8-15](../../../cod_doc/templates/web/project/docs_list.html), [tasks_list.html:8-15](../../../cod_doc/templates/web/project/tasks_list.html), [doc_show.html](../../../cod_doc/templates/web/project/doc_show.html) (тут табов нет — расхождение с другими страницами)

**Симптом:** один и тот же `<nav class="tabs">` повторён в 3 шаблонах из 4
(на `doc_show.html` его НЕТ — отдельный баг UX). Любое изменение порядка/добавление
таба = N правок и риск рассинхрона. `doc_show.html` уже рассинхронизирован.

**Что нужно:** новая задача **WEB-041 — extract `_layout/project_tabs.html`**
(priority `medium`):
- вынести в include с параметром `active`,
- добавить недостающую таб-полосу в `doc_show.html`,
- единый список вкладок описать как Jinja-глобал (`PROJECT_TABS`) в `templates_env`.

### SW-ME-2. Все табы (кроме Overview/Docs/Tasks) ведут в 404

**Где:** `<a href="/p/{{slug}}/plans|revisions|run">` во всех табовых шаблонах.

**Симптом:** в табовой полосе видны 6 табов, но 3 из них (`Plans`, `Revisions`, `Run`)
+ верхняя `Settings` ссылка ведут на 404. Пользователь не отличает «не реализовано»
от «битый сайт». Ухудшает доверие к UI на этапе разработки.

**Что нужно:** добавить в `_layout/project_tabs.html` явный признак `disabled`
для нереализованных табов (рендерить как `<span>` с tooltip «coming soon»).
Включить в **WEB-041**. До закрытия WEB-004/020/021/030 убрать живые ссылки.

### SW-ME-3. doc_show body — raw markdown в `<pre>`, anchor'ы битые

**Где:** [doc_show.html:36-40](../../../cod_doc/templates/web/project/doc_show.html), [pages.py:162](../../../cod_doc/api/web/pages.py)

**Симптом:** в боковой нав-панели — ссылки `<a href="#data-model">`, но в `<pre>`
тегах нет HTML id'ов — клик ничего не делает. Вторая проблема: markdown показывается
как raw text — заголовки `## API` остаются `## API` вместо `<h2>API</h2>`. Документ
читать неудобно даже глазом, не говоря о scroll-to-anchor.

**Что нужно:** новая задача **WEB-006 — markdown rendering в doc_show**
(priority `medium`, depends on `WEB-001`):
- решение либо `markdown-it-py` (новая dep) либо собственный server-side рендерер
  поверх существующего `DocService.render_body` (он уже знает структуру секций),
- секции рендерить как `<section id="{anchor}"><h{level}>...</h{level}>...</section>`,
- raw-mode оставить как `?raw=1` query param.

### SW-ME-4. Capability §3 рассинхронизирован с реальностью

**Где:** [capabilities/web-frontend.md §3](../capabilities/web-frontend.md)

**Симптом:** таблица маршрутов содержит endpoint'ы, реализация которых распределена
между задачами. Capability — source of truth, но в нём нет колонки «implementation
status» и нет привязки к task-id. Читатель не отличает «уже работает» от «целевое
состояние». Нарушает принцип source-of-truth: документ описывает целевую
функциональность, но это не явно.

**Что нужно:** усилить документацию (см. §6 этого аудита):
- добавить колонку `Status` в таблицу §3 (`✅ shipped / 🔄 in-progress / ❌ pending`),
- добавить колонку `Task` (`WEB-001`/`WEB-010`/...).

### SW-ME-5. Скелетные шаблоны из §4 capability отсутствуют

**Где:** [capabilities/web-frontend.md §4](../capabilities/web-frontend.md)

**Симптом:** §4 описывает структуру:
```
templates/web/
├── _layout/{header,nav}.html      ← НЕТ
├── settings.html                   ← НЕТ
├── project/{plan_show,revisions,run}.html  ← НЕТ
└── _frag/{section_view,section_edit,alert}.html  ← НЕТ
```

Десять шаблонов фигурируют только на бумаге. Documentation drift.

**Что нужно:** не создавать пустые файлы (drift в обратную сторону), а отметить
в §4 явно: «целевая структура; реальное состояние трекается в
`roadmap/web-frontend-task-plan.md` Progress Overview». Включить в усиление
документации (см. §6).

### SW-ME-6. `status_options` дублируется в pages и fragments

**Где:** [pages.py:147](../../../cod_doc/api/web/pages.py), [fragments.py:52](../../../cod_doc/api/web/fragments.py)

```python
"status_options": [s.value for s in TaskStatus],
```

**Симптом:** один и тот же expression в двух модулях. Если добавим `TaskStatus.BLOCKED`
— нужно поправить в двух местах. Тесты не поймают расхождение.

**Что нужно:** включить в **WEB-041** или вынести в helper `cod_doc.api.web.choices`.

### SW-ME-7. Нет страницы агрегата задач + plan view → продуктивность Web фронта низкая

**Где:** capability §3 + roadmap.

**Симптом:** дашборд `/p/{slug}` показывает 7 KPI-карточек, но **нет** ни
«ready-to-start» tasks (через `PlanService.ready`), ни «plan progress» панели
(через `PlanService.recalc`). Продуктивность Web-фронта пока ниже CLI:
`cod-doc plan ready` даёт больше информации в одну команду, чем дашборд.

**Что нужно:** новая задача **WEB-014 — Overview dashboard upgrade**
(priority `medium`, depends on `WEB-005`):
- блок «Ready to start» (top-5 из `PlanService.ready`),
- блок «Plan progress» (mini-bar по каждому plan),
- блок «Recent revisions» (top-5 из `RevisionRepository`).
- Это не дублирует WEB-004 (полный plan view) — это сжатый агрегат на overview.

---

## 3. Low

### SW-LO-1. Vendored `htmx.min.js` без version-fingerprint

**Где:** [base.html:8](../../../cod_doc/templates/web/base.html)

```html
<script defer src="/static/htmx.min.js"></script>
```

**Симптом:** при апгрейде htmx (например v2.0.4 → v2.1.0) browser получит
закэшированный старый файл. У dev'а и у пользователя — рассинхрон поведения.

**Что нужно:** новая задача **WEB-051 — static asset versioning** (priority `low`):
- `?v={hash}` или path `/static/htmx-2.0.4.min.js`,
- вычисление через `templates_env` (file-mtime → query string).

### SW-LO-2. Тесты не покрывают error-ветки fragment-handler'а

**Где:** [test_web_tasks.py:222-332](../../../tests/api/test_web_tasks.py), [fragments.py:80-97](../../../cod_doc/api/web/fragments.py)

**Симптом:** тестируется success path + 400/404, но НЕ:
- `RevisionConflictError` ветка (rollback + `error: str | None`),
- `IntegrityError` ветка (нарушение FK),
- `ValueError` ветка (включая state-machine отказ от TaskService).

Регрессии в обработке ошибок проскользнут.

**Что нужно:** новая задача **WEB-052 — error-branch coverage** (priority `low`):
- симулировать concurrent update → conflict,
- сломать FK через прямой DB-инжект,
- запросить переход в state, который запрещён доменом.

### SW-LO-3. Нет тестов на index без projects + master_path missing

**Где:** [pages.py:46-47](../../../cod_doc/api/web/pages.py)

**Симптом:** `proj.read_master()` может вернуть None, ветка `master_preview is none`
в шаблоне есть, но теста на «MASTER.md удалён вручную после init» — нет.

**Что нужно:** включить в **WEB-052** один тест.

### SW-LO-4. Audit `2026-04-28-section-c-capabilities.md` остаётся `active`

**Где:** [audit/2026-04-28-section-c-capabilities.md](2026-04-28-section-c-capabilities.md)

**Симптом:** статус `active`, потому что WEB-040 (SC-HI-3) не закрыт. После
закрытия WEB-040 надо переводить в `resolved`. Сейчас правильное состояние;
но как только WEB-040 уйдёт в done — не забыть.

**Что нужно:** добавить в Definition of Done WEB-040 пункт «обновить статус
аудит-отчёта 2026-04-28-section-c в `resolved`». Зафиксировать в самом тикете.

### SW-LO-5. `_alembic_upgrade()` дублируется в двух тест-файлах

**Где:** [test_web_docs.py:29-38](../../../tests/api/test_web_docs.py), [test_web_tasks.py:35-44](../../../tests/api/test_web_tasks.py)

**Симптом:** идентичная функция запуска миграций в `tasks_client` и `docs_client`
fixtures.

**Что нужно:** вынести в `tests/api/conftest.py`. Включить в **WEB-053** —
test fixtures hygiene (priority `low`).

---

## 4. Заведено в roadmap (в рамках этого аудита)

| Task | Section | Priority | Зависит от | Notes |
|---|---|---|---|---|
| **WEB-005** | F-Hardening (new) | high | WEB-001 | Engine cache + DI helper |
| **WEB-013** | F-Hardening | high | WEB-005 | Index batch stats |
| **WEB-014** | B-Read-Views | medium | WEB-005, WEB-010 | Overview agg |
| **WEB-041** | E-Architecture-Hygiene | medium | WEB-002, WEB-040 | tabs include + tooltip disabled |
| **WEB-042** | E-Architecture-Hygiene | medium | — | Документация-drift в §3/§4 |
| **WEB-006** | B-Read-Views | medium | WEB-003 | Markdown rendering |
| **WEB-050** | F-Hardening | medium | — | DB session DI helper, чтобы не было WEB-040 v2 |
| **WEB-051** | F-Hardening | low | — | Static asset versioning |
| **WEB-052** | F-Hardening | low | WEB-011 | Error-branch tests |
| **WEB-053** | F-Hardening | low | — | conftest cleanup |
| **WEB-060** | B-Read-Views | medium | WEB-005 | Settings page (form save) |

`WEB-022` поднимается с `medium` до `high`, зависимость `[WEB-011, WEB-012]`
заменяется на `[WEB-040, WEB-005]`.

`WEB-040` поднимается с `medium` до `high` и помечается блокером для всех новых
write-path задач (WEB-012, WEB-014).

## 5. Что осталось вне scope

- **Markdown rendering** через `markdown-it-py` или собственный — решается в WEB-006,
  но добавление новой dep требует ADR. Если откажемся от dep — пишем mini-renderer
  поверх `DocService.render_body` (документ уже структурирован посекционно).
- **CSP / Security headers** — local-only режим оставляет это вне scope. Production
  reverse-proxy (nginx) добавит CSP. Не наша забота.
- **WCAG / accessibility** — табы, формы, контраст — отложено до закрытия §3.

---

## 6. Усиление документации (рекомендации)

Применяется в этой же ревизии:

1. `capabilities/web-frontend.md`:
   - **§3 → таблица с колонками** `Status` (✅/🔄/❌) и `Task` (`WEB-XXX`).
   - **§4 → пометка** «целевая структура; реальное — в roadmap Progress Overview».
   - **§7 → расширение**: явное описание DI pattern для DB session
     («`get_project_db` в `cod_doc.api.deps` — единственный способ; прямой
     `infra.*` запрещён, проверяется в WEB-040»).
   - **§8 → расширение**: error-branch coverage — обязательная часть DoD.
   - Новый **§11 — «Текущее состояние»** с актуальными метриками
     (LOC, endpoints shipped/total, tests).

2. `roadmap/web-frontend-task-plan.md`:
   - `last_updated: 2026-05-02`,
   - **новая Section F: Hardening** (WEB-005, WEB-013, WEB-022 ↑, WEB-050..053),
   - расширение Section E: WEB-041, WEB-042,
   - Progress Overview пересчёт.

3. `MASTER.md`:
   - §2 — добавить ссылку на этот audit-отчёт,
   - §5 — пометить статус `active`,
   - §6 changelog — запись 2026-05-02.

## 7. Changelog

| Дата | Событие |
|---|---|
| 2026-05-02 | Аудит проведён; 13 задач заведено (WEB-005, 006, 013, 014, 022 ↑, 040 ↑, 041, 042, 050..053, 060). Inline-фиксов нет — все замечания пробрасываются в отдельные таски, чтобы не создать долг. Документация capability/roadmap усилена параллельно. |
| 2026-05-02 | **Resolved.** Все 16 / 16 находок закрыты в 4 batch'ах (см. checkpoint-аудиты #1..#4). Последние 2 LOW (SW-LO-2, SW-LO-3) закрыты в WEB-052; SW-LO-5 в WEB-053. 13 / 14 endpoints shipped; Section A/B/C закрыты целиком; Section D (Live ops) — единственный оставшийся endpoint scope, отслеживается отдельно. |
