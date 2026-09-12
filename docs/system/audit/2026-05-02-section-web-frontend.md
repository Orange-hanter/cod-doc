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

> A focused audit of the web section after closing Section A (Scaffold) and the targeted implementation in Section B/C
> (`WEB-010` tasks list, `WEB-011` HTMX status). We check: capability conformance,
> architectural violations, performance, UX coherence, test coverage.
> The goal is to record debt BEFORE starting `WEB-004/020/021/030` and Section E (`WEB-040`),
> so the same problems are not baked into new pages.

## 0. Baseline statistics

| Metric | Value |
|---|---|
| LOC python (`cod_doc/api/web/*.py`) | 363 |
| LOC templates (`cod_doc/templates/web/**`) | 280 |
| LOC `app.css` | 211 |
| Vendored `htmx.min.js` | ~50 KB |
| Realised endpoints | 5 (`GET /`, `GET /p/{slug}`, `GET /p/{slug}/docs`, `GET /p/{slug}/docs/{key:path}`, `GET /p/{slug}/tasks`, `POST /p/{slug}/tasks/{id}/status`) |
| Capability §3 endpoints total | 14 |
| Coverage capability §3 | **5/14 (36 %)** |
| Web-tests | 27 (`27 passed in 10.96s`) |

The core is healthy, but 9 routes from the spec do not exist yet, and under the closed ones architectural debt and UX-broken links have accumulated covertly.

## Summary

| Severity | Count | Inline fix | New task | Deferred |
|---|---:|---:|---:|---:|
| critical | 0 | — | — | — |
| high | 4 | — | 4 (WEB-040 ↑, WEB-005, WEB-013, WEB-022 ↑) | 0 |
| medium | 7 | — | 6 (WEB-006, WEB-014, WEB-041, WEB-042, WEB-050, WEB-060) | 1 (markdown rendering→COD-070) |
| low | 5 | — | 3 (WEB-051, WEB-052, WEB-053) | 2 |
| **total** | **16** | **0** | **13** | **3** |

There are no inline fixes on purpose: each finding below either affects ≥2 future tasks,
or requires an architectural decision (see §1.SW-HI-1). Closing them "on the way" to
WEB-004/020/021 = repeating the same mistake as SC-HI-3 (web bypasses services)
— the problem was missed in WEB-001..003 because it was not recorded as a separate task.

---

## 1. High

### SW-HI-1. Web → infra bypass still not closed (WEB-040)

**Where:** historical `cod_doc/api/web/db_resolver.py:22-23` (deleted in WEB-040)

```python
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.repositories import ProjectRepository
```

**Symptom:** `cod_doc.api.web` directly imports `cod_doc.infra.*`. This violates
[capabilities/web-frontend.md §7](../capabilities/web-frontend.md):
"A web page is not allowed to bypass the service. Allowed modules — only `cod_doc.services.*` and `cod_doc.api.deps`".

Declared as WEB-040 (Section E, priority `medium`) back on 2026-04-28. Since then —
no changes; meanwhile `WEB-011` (closed) added a **second** call-site
(`fragments.py` via the same `db_resolver`). The longer we wait, the more code
will hook onto this resolver.

**What is needed:**
1. Move the "slug → DB session + project_db_id" resolution into `cod_doc.api.deps` as
   a FastAPI `Depends` function (`get_project_db`). Returns `(Session, project_db_id)`
   or raises `HTTPException(404)`.
2. Cache engine/factory (see SW-HI-2) — do not spawn per-request.
3. Delete `cod_doc/api/web/db_resolver.py`.
4. `pages.py` / `fragments.py` depend only on `cod_doc.services.*` + `cod_doc.api.deps`.
5. Raise the priority of WEB-040 to **high** and make it a blocker for **any** new
   write-path feature (`WEB-012`, `WEB-014`).

### SW-HI-2. Engine is created on every HTTP request (perf cliff)

**Where:** historical `cod_doc/api/web/db_resolver.py:44-59` (deleted in WEB-040)

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

**Symptom:** on every `/p/{slug}/...` request a new SQLAlchemy `Engine` is created
(including connection pool, pragma init, registration), used for one SELECT,
then `dispose()`. The cost on a cold SSD for embedded SQLite — 5–15 ms;
on a network FS (NFS, syncthing) — 50–200 ms. For the index page with 10 projects —
**100×** this overhead (see SW-HI-3).

**What is needed:** a new task **WEB-005 — DB engine cache** (priority `high`):
- a `dict[Path, Engine]` cache with TTL invalidation by the state.db file mtime,
- initialization in `lifespan`, cleanup on shutdown,
- integration with FastAPI DI (`get_engine_for_slug`).

### SW-HI-3. N+1 on the index page (`Project.stats()` per project)

**Where:** [cod_doc/api/web/pages.py:27-35](../../../cod_doc/api/web/pages.py)

```python
for entry in cfg.list_projects():
    projects.append({..., "stats": Project(entry).stats()})
```

**Symptom:** `Project.stats()` for each project reads either `state.db` or
files. For N projects — N sequential I/Os, and no cache. The UI freezes on
`GET /` proportionally to the number of registered projects.

**What is needed:** a new task **WEB-013 — projects listing batch stats**
(priority `high`, depends on WEB-005):
- a batch method `ProjectRepository.list_with_stats(slugs)` (or multi-DB aggregation),
- a limit on the displayed count (top-20 by `last_run`) with pagination,
- a placeholder skeleton on the HTMX side if N is large.

### SW-HI-4. WEB-022 (alert/error model) is hanging → errors are silently lost

**Where:** [cod_doc/templates/web/base.html:18](../../../cod_doc/templates/web/base.html), [cod_doc/api/web/fragments.py:80-97](../../../cod_doc/api/web/fragments.py)

**Symptom:** `<div id="alerts">` exists in `base.html`, but no one writes to it.
An HTMX fragment on error (`RevisionConflictError`, `IntegrityError`, `ValueError`)
returns an inline `<span class="row-error">{{ error }}</span>` — text without structure,
without severity, without a dismissable button. Revision conflicts now look like
"the red label disappeared after the next HTMX update" — the user loses the incident.

WEB-022 is declared as `medium` and depends on **WEB-011, WEB-012**. WEB-012 is still `pending`
→ WEB-022 is formally unblocked already now (since WEB-011 is closed), but in the plan
it stands after WEB-012, which is incorrect. We raise it to `high` and untie the dependency:
WEB-022 must be closed right after `WEB-040 + WEB-005` — this is the bus for all
future write-paths.

---

## 2. Medium

### SW-ME-1. Tab navigation duplication (4 copies)

**Where:** [show.html:9-16](../../../cod_doc/templates/web/project/show.html), [docs_list.html:8-15](../../../cod_doc/templates/web/project/docs_list.html), [tasks_list.html:8-15](../../../cod_doc/templates/web/project/tasks_list.html), [doc_show.html](../../../cod_doc/templates/web/project/doc_show.html) (here there are no tabs — a divergence from the other pages)

**Symptom:** the same `<nav class="tabs">` is repeated in 3 of 4 templates
(on `doc_show.html` it is NOT present — a separate UX bug). Any change to the order/addition of
a tab = N edits and a risk of desync. `doc_show.html` is already desynchronized.

**What is needed:** a new task **WEB-041 — extract `_layout/project_tabs.html`**
(priority `medium`):
- move it to an include with an `active` parameter,
- add the missing tab strip to `doc_show.html`,
- describe the single list of tabs as a Jinja global (`PROJECT_TABS`) in `templates_env`.

### SW-ME-2. All tabs (except Overview/Docs/Tasks) lead to 404

**Where:** `<a href="/p/{{slug}}/plans|revisions|run">` in all tab templates.

**Symptom:** 6 tabs are visible in the tab strip, but 3 of them (`Plans`, `Revisions`, `Run`)
+ the top `Settings` link lead to 404. The user cannot distinguish "not implemented"
from "broken site". This degrades trust in the UI at the development stage.

**What is needed:** add an explicit `disabled` flag to `_layout/project_tabs.html`
for unimplemented tabs (render as `<span>` with a tooltip "coming soon").
Include in **WEB-041**. Until WEB-004/020/021/030 are closed, remove the live links.

### SW-ME-3. doc_show body — raw markdown in `<pre>`, anchors are broken

**Where:** [doc_show.html:36-40](../../../cod_doc/templates/web/project/doc_show.html), [pages.py:162](../../../cod_doc/api/web/pages.py)

**Symptom:** in the side nav panel there are links `<a href="#data-model">`, but in the `<pre>`
tags there are no HTML ids — clicking does nothing. The second problem: markdown is shown
as raw text — headings `## API` remain `## API` instead of `<h2>API</h2>`. The document is
hard to read even by eye, let alone scroll-to-anchor.

**What is needed:** a new task **WEB-006 — markdown rendering in doc_show**
(priority `medium`, depends on `WEB-001`):
- the decision is either `markdown-it-py` (a new dep) or a custom server-side renderer
  on top of the existing `DocService.render_body` (it already knows the section structure),
- render sections as `<section id="{anchor}"><h{level}>...</h{level}>...</section>`,
- keep raw-mode as a `?raw=1` query param.

### SW-ME-4. Capability §3 is desynchronized with reality

**Where:** [capabilities/web-frontend.md §3](../capabilities/web-frontend.md)

**Symptom:** the routes table contains endpoints whose implementation is distributed
across tasks. The capability is the source of truth, but it has no "implementation
status" column and no binding to a task-id. The reader cannot distinguish "already works"
from "target state". This violates the source-of-truth principle: the document describes
the target functionality, but this is not explicit.

**What is needed:** strengthen the documentation (see §6 of this audit):
- add a `Status` column to the §3 table (`✅ shipped / 🔄 in-progress / ❌ pending`),
- add a `Task` column (`WEB-001`/`WEB-010`/...).

### SW-ME-5. The skeletal templates from §4 of the capability are missing

**Where:** [capabilities/web-frontend.md §4](../capabilities/web-frontend.md)

**Symptom:** §4 describes the structure:
```
templates/web/
├── _layout/{header,nav}.html      ← MISSING
├── settings.html                   ← MISSING
├── project/{plan_show,revisions,run}.html  ← MISSING
└── _frag/{section_view,section_edit,alert}.html  ← MISSING
```

Ten templates appear only on paper. Documentation drift.

**What is needed:** do not create empty files (drift in the other direction), but note
in §4 explicitly: "target structure; the real state is tracked in
`roadmap/web-frontend-task-plan.md` Progress Overview". Include in the documentation
strengthening (see §6).

### SW-ME-6. `status_options` is duplicated in pages and fragments

**Where:** [pages.py:147](../../../cod_doc/api/web/pages.py), [fragments.py:52](../../../cod_doc/api/web/fragments.py)

```python
"status_options": [s.value for s in TaskStatus],
```

**Symptom:** the same expression in two modules. If we add `TaskStatus.BLOCKED`
— we need to fix it in two places. Tests will not catch the divergence.

**What is needed:** include in **WEB-041** or extract into a helper `cod_doc.api.web.choices`.

### SW-ME-7. No task aggregate page + plan view → the productivity of the Web front is low

**Where:** capability §3 + roadmap.

**Symptom:** the dashboard `/p/{slug}` shows 7 KPI cards, but there is **no**
"ready-to-start" tasks (via `PlanService.ready`), nor a "plan progress" panel
(via `PlanService.recalc`). The productivity of the Web front is so far lower than the CLI:
`cod-doc plan ready` gives more information in one command than the dashboard.

**What is needed:** a new task **WEB-014 — Overview dashboard upgrade**
(priority `medium`, depends on `WEB-005`):
- a "Ready to start" block (top-5 from `PlanService.ready`),
- a "Plan progress" block (a mini-bar per plan),
- a "Recent revisions" block (top-5 from `RevisionRepository`).
- This does not duplicate WEB-004 (full plan view) — this is a compressed aggregate on the overview.

---

## 3. Low

### SW-LO-1. Vendored `htmx.min.js` without a version-fingerprint

**Where:** [base.html:8](../../../cod_doc/templates/web/base.html)

```html
<script defer src="/static/htmx.min.js"></script>
```

**Symptom:** on an htmx upgrade (e.g. v2.0.4 → v2.1.0) the browser will receive
a cached old file. The dev and the user get desynchronized behavior.

**What is needed:** a new task **WEB-051 — static asset versioning** (priority `low`):
- `?v={hash}` or path `/static/htmx-2.0.4.min.js`,
- computation via `templates_env` (file-mtime → query string).

### SW-LO-2. Tests do not cover the error branches of the fragment handler

**Where:** [test_web_tasks.py:222-332](../../../tests/api/test_web_tasks.py), [fragments.py:80-97](../../../cod_doc/api/web/fragments.py)

**Symptom:** the success path + 400/404 is tested, but NOT:
- the `RevisionConflictError` branch (rollback + `error: str | None`),
- the `IntegrityError` branch (FK violation),
- the `ValueError` branch (including the state-machine refusal from TaskService).

Regressions in error handling will slip through.

**What is needed:** a new task **WEB-052 — error-branch coverage** (priority `low`):
- simulate a concurrent update → conflict,
- break an FK via a direct DB inject,
- request a transition to a state that is forbidden by the domain.

### SW-LO-3. No tests for the index without projects + master_path missing

**Where:** [pages.py:46-47](../../../cod_doc/api/web/pages.py)

**Symptom:** `proj.read_master()` can return None, the template has a `master_preview is none`
branch, but there is no test for "MASTER.md deleted manually after init".

**What is needed:** include one test in **WEB-052**.

### SW-LO-4. Audit `2026-04-28-section-c-capabilities.md` remains `active`

**Where:** [audit/2026-04-28-section-c-capabilities.md](2026-04-28-section-c-capabilities.md)

**Symptom:** status `active`, because WEB-040 (SC-HI-3) is not closed. After
closing WEB-040 it must be moved to `resolved`. The current state is correct;
but as soon as WEB-040 goes to done — do not forget.

**What is needed:** add to the Definition of Done of WEB-040 the item "update the status
of audit report 2026-04-28-section-c to `resolved`". Fix in the ticket itself.

### SW-LO-5. `_alembic_upgrade()` is duplicated in two test files

**Where:** [test_web_docs.py:29-38](../../../tests/api/test_web_docs.py), [test_web_tasks.py:35-44](../../../tests/api/test_web_tasks.py)

**Symptom:** an identical function that runs migrations in the `tasks_client` and `docs_client`
fixtures.

**What is needed:** extract it to `tests/api/conftest.py`. Include in **WEB-053** —
test fixtures hygiene (priority `low`).

---

## 4. Opened in the roadmap (within this audit)

| Task | Section | Priority | Depends on | Notes |
|---|---|---|---|---|
| **WEB-005** | F-Hardening (new) | high | WEB-001 | Engine cache + DI helper |
| **WEB-013** | F-Hardening | high | WEB-005 | Index batch stats |
| **WEB-014** | B-Read-Views | medium | WEB-005, WEB-010 | Overview agg |
| **WEB-041** | E-Architecture-Hygiene | medium | WEB-002, WEB-040 | tabs include + tooltip disabled |
| **WEB-042** | E-Architecture-Hygiene | medium | — | Documentation drift in §3/§4 |
| **WEB-006** | B-Read-Views | medium | WEB-003 | Markdown rendering |
| **WEB-050** | F-Hardening | medium | — | DB session DI helper, so there is no WEB-040 v2 |
| **WEB-051** | F-Hardening | low | — | Static asset versioning |
| **WEB-052** | F-Hardening | low | WEB-011 | Error-branch tests |
| **WEB-053** | F-Hardening | low | — | conftest cleanup |
| **WEB-060** | B-Read-Views | medium | WEB-005 | Settings page (form save) |

`WEB-022` is raised from `medium` to `high`, the dependency `[WEB-011, WEB-012]`
is replaced with `[WEB-040, WEB-005]`.

`WEB-040` is raised from `medium` to `high` and marked as a blocker for all new
write-path tasks (WEB-012, WEB-014).

## 5. What remained out of scope

- **Markdown rendering** via `markdown-it-py` or a custom one — is resolved in WEB-006,
  but adding a new dep requires an ADR. If we decline the dep — we write a mini-renderer
  on top of `DocService.render_body` (the document is already structured per-section).
- **CSP / Security headers** — the local-only mode leaves this out of scope. A production
  reverse-proxy (nginx) will add CSP. Not our concern.
- **WCAG / accessibility** — tabs, forms, contrast — deferred until §3 is closed.

---

## 6. Documentation strengthening (recommendations)

Applied in this same revision:

1. `capabilities/web-frontend.md`:
   - **§3 → a table with columns** `Status` (✅/🔄/❌) and `Task` (`WEB-XXX`).
   - **§4 → a note** "target structure; the real one — in roadmap Progress Overview".
   - **§7 → expansion**: an explicit description of the DI pattern for the DB session
     ("`get_project_db` in `cod_doc.api.deps` — the only way; a direct
     `infra.*` is forbidden, checked in WEB-040").
   - **§8 → expansion**: error-branch coverage — a mandatory part of the DoD.
   - A new **§11 — "Current state"** with up-to-date metrics
     (LOC, endpoints shipped/total, tests).

2. `roadmap/web-frontend-task-plan.md`:
   - `last_updated: 2026-05-02`,
   - **a new Section F: Hardening** (WEB-005, WEB-013, WEB-022 ↑, WEB-050..053),
   - expansion of Section E: WEB-041, WEB-042,
   - Progress Overview recalculation.

3. `MASTER.md`:
   - §2 — add a link to this audit report,
   - §5 — mark the status `active`,
   - §6 changelog — an entry for 2026-05-02.

## 7. Changelog

| Date | Event |
|---|---|
| 2026-05-02 | Audit conducted; 13 tasks opened (WEB-005, 006, 013, 014, 022 ↑, 040 ↑, 041, 042, 050..053, 060). No inline fixes — all findings are routed to separate tasks so as not to create debt. The capability/roadmap documentation is strengthened in parallel. |
| 2026-05-02 | **Resolved.** All 16 / 16 findings closed in 4 batches (see checkpoint audits #1..#4). The last 2 LOW (SW-LO-2, SW-LO-3) closed in WEB-052; SW-LO-5 in WEB-053. 13 / 14 endpoints shipped; Section A/B/C closed entirely; Section D (Live ops) — the only remaining endpoint scope, tracked separately. |
