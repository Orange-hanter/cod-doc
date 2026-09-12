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

> A purely functional web interface to COD-DOC: project list, documents, tasks, plans, revisions, agent run log. Without visual polish and without a separate frontend stack: server-rendered Jinja + targeted HTMX fragments served by the same FastAPI.

## 1. Goals and non-goals

### Goal

Give a human (documentation author, agent operator) the same set of operations available through CLI/MCP, in the browser, **without a separate build pipeline and without duplicating the domain model in TS**. The surface is equal to the others: everything available in the services must be available in Web within one PR-cycle after CLI/MCP (the rule [ARCHITECTURE.md §1](../ARCHITECTURE.md)).

### Non-goals

- Not an SPA. No React/Vue/Svelte, no build-pipeline, no client-side router.
- Not a visual product. No design system, no dark theme, no animations. Minimal CSS (one file, ~200 lines, a `pico.css`-like baseline or a custom one).
- Not public. Authentication — deferred; the interface is designed to run locally or behind a reverse-proxy with basic-auth.
- Not a replacement for the TUI. The TUI remains for offline/fast scenarios. Web — for "easier to point the cursor" situations.

## 2. Stack

| Layer | Solution | Why |
|------|---------|--------|
| Server | FastAPI (the same `cod_doc.api.server:app`) | Already present, shared lifespan and DI |
| Templates | Jinja2 (`jinja2` already in deps) | Server-rendered HTML; one model, no schema generation |
| Interactivity | HTMX (via `<script src="/static/htmx.min.js">`) | `hx-get`/`hx-post`/`hx-swap` for inline editing and fragments; SSE for live logs |
| Styles | One `static/app.css` (~150-300 lines, raw CSS) | No bundler, no PostCSS, no Tailwind |
| Dependency graph | Mermaid via `<script type="module">` (CDN or local) | Already used in task-plan markdown — reused |

No new dependencies in `pyproject.toml` beyond the existing ones (`fastapi`, `jinja2`).

## 3. Routes

Web routes live in `cod_doc.api.web.*` and are wired as a second router in `server.py`. There is no prefix — the root is given to Web; the API stays on `/api/*`. Static files (`/static/*`) are served by a separate `StaticFiles` mount and are not included in the table below.

The **Status** column shows the real state of implementation (see also §11):

- ✅ shipped, tests green;
- 🔄 in-progress / partially implemented;
- ❌ pending — the endpoint is described as a target, but is not yet in code.

| Method + path | Purpose | Service | Status | Task |
|--------------|-----------|--------|:------:|------|
| **Global pages**  | | | |
| `GET /` | Project list + pagination + stats | `Config.list_projects()` + `Project.batch_stats()` + `task_service.summarize_for_project` | ✅ | WEB-001 |
| `GET /settings` | View config (API key masked) | `Config.load` | ✅ | WEB-060 |
| `POST /settings` | Save config | `Config.save` | ✅ | WEB-060 |
| `GET /standards` | Catalog of built-in skills/standards | `skill_service.list_skills` | ✅ | OBI-standards |
| `GET /standards/{name}` | Skill / standard detail | `skill_service.get_skill` | ✅ | OBI-standards |
| **Project — overview and bootstrap**  | | | |
| `GET /p/{slug}` | Project dashboard: KPIs, MASTER preview, ready tasks, plans, revisions, drift-health | `Project.stats/read_master` + `plan_service` + `revision_service` + `project_health_service` | ✅ | WEB-002 |
| `POST /p/{slug}/init` | Bootstrap DB (alembic upgrade + ProjectModel) | `project_service.init_project` | ✅ | WEB-080 |
| `POST /p/{slug}/import_master/scan` | AI scan of the repo: preview MASTER.md + coverage tasks | `ai_generate.generate_master_from_folder` | ✅ | COD-060 |
| `POST /p/{slug}/import_master/save` | Write MASTER.md and create coverage tasks | `ai_generate` + `task_service.create` | ✅ | COD-060 |
| **Documents — list and CRUD**  | | | |
| `GET /p/{slug}/docs` | Document list (tree/flat, filter by type/status/q) | `doc_service.list_for_project` | ✅ | WEB-003 |
| `GET /p/{slug}/docs/new` | Form to create an empty document | `doc_service` (form only) | ✅ | COD-078 |
| `POST /p/{slug}/docs/new` | Create an empty document | `doc_service.create` | ✅ | COD-078 |
| `POST /p/{slug}/docs/suggest` | AI suggestion of title/doc_key/type/preamble from a description | `ai_text.suggest_doc_meta` | ✅ | COD-078 |
| `POST /p/{slug}/docs-accept` | Promote a document to ACTIVE (or other) status | `doc_service.update_status` | ✅ | COD-052 |
| `GET /p/{slug}/docs/{doc_key:path}` | View document: preamble + sections + links + comments + suggestions | `doc_service.get/render_body/get_sections` + `link_service` + `comment_service` | ✅ | WEB-003 |
| `POST /p/{slug}/docs/{doc_key:path}/expand` | AI generation of sections for an empty/sparse document | `ai_text.expand_doc_sections` + `doc_service.add_section` | ✅ | COD-078 |
| **Documents — import**  | | | |
| `GET /p/{slug}/docs/import` | Bulk import UI: form + manifest | `import_service` (form) | ✅ | PCA-401 |
| `POST /p/{slug}/docs/import` | Upload a single markdown file → Document + Sections | `import_service.import_markdown` | ✅ | WEB-081 |
| `GET /p/{slug}/docs/import/scan` | JSON manifest of project files vs DB | `import_service.scan_folder` | ✅ | PCA-400 |
| `POST /p/{slug}/docs/import/apply` | Apply selected files from the bulk manifest | `import_service.import_or_update_markdown` | ✅ | PCA-401 |
| **Documents — AI generation from sources**  | | | |
| `GET /p/{slug}/docs/generate` | Form to pick source-documents and intent | `doc_service.list_for_project` | ✅ | COD-078 |
| `POST /p/{slug}/docs/generate` | Preview of the generated document | `ai_generate.generate_doc_from_sources` | ✅ | COD-078 |
| `POST /p/{slug}/docs/generate/save` | Save the generated document + auto-links | `doc_service.create/add_section` | ✅ | COD-078 |
| **Documents — semantic link suggestions**  | | | |
| `POST /p/{slug}/suggestions/run` | Run semantic link suggestion generation for a document | `link_service.semantic.suggest_for_section` | ✅ | PCA-422 |
| `POST /p/{slug}/suggestions/{row_id}/accept` | Accept a suggestion: add a link to See also | `doc_service.patch_section` + `link_service.semantic` | ✅ | PCA-422 |
| `POST /p/{slug}/suggestions/{row_id}/reject` | Reject a suggestion | `link_service.semantic.update_suggestion_state` | ✅ | PCA-422 |
| **Documents — comments**  | | | |
| `POST /p/{slug}/docs/{doc_key:path}/comments` | Create a section-anchored or doc-level comment | `comment_service.create` | ✅ | OBI-comments |
| `GET /p/{slug}/docs/{doc_key:path}/comments.json` | JSON dump of comments (popovers) | `comment_service.list_for_document` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/{comment_id}/resolve` | Mark a comment resolved | `comment_service.update_status` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/{comment_id}/reopen` | Reopen a comment | `comment_service.update_status` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/{comment_id}/delete` | Delete a comment | `comment_service.delete` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/apply` | AI-rework preview over open comments | `comment_service.apply_open_with_ai` | ✅ | OBI-comments |
| `POST /p/{slug}/docs/{doc_key:path}/comments/apply/commit` | Apply selected AI edits | `doc_service.patch_section` + `comment_service.update_status` | ✅ | OBI-comments |
| **Documents — navigator and analysis**  | | | |
| `GET /p/{slug}/docs/navigator` | Documentation path map + cached gap-analysis | `nav_service.compute_journey` + `nav_service.peek_cached_analysis` | ✅ | OBI-navigator |
| `POST /p/{slug}/docs/navigator/analyze` | HTMX: run/load AI gap-analysis | `nav_service.analyze_gaps` | ✅ | OBI-navigator |
| **Sections — HTMX fragments**  | | | |
| `GET /p/{slug}/docs/{doc_key:path}/sections/{anchor}/edit` | Edit form fragment | `doc_service.get_sections` + `revision_service.head_for_entity` | ✅ | WEB-012 |
| `GET /p/{slug}/docs/{doc_key:path}/sections/{anchor}/view` | View fragment (cancel) | `doc_service.get_sections` | ✅ | WEB-012 |
| `POST /p/{slug}/docs/{doc_key:path}/sections/{anchor}` | HTMX section patch (form-encoded body) | `doc_service.patch_section` | ✅ | WEB-012 |
| **Plans**  | | | |
| `GET /p/{slug}/plans` | Project plan list with progress | `plan_service.list_for_project` + `recalc` | ✅ | WEB-004 |
| `GET /p/{slug}/plans/{plan_id}` | Plan view: Progress Overview + Next Batch + Mermaid | `plan_service.recalc/ready/export` | ✅ | WEB-004 |
| `POST /p/{slug}/plans/{plan_id}/freeze` | Snapshot the current plan into an EXECUTION_LOG document | `plan_service.freeze_projection` | ✅ | COD-052 |
| **Tasks — list and detail**  | | | |
| `GET /p/{slug}/tasks` | Task list (kanban board / chains, filter by plan/status) | `task_service.list_for_project` + `plan_service` | ✅ | WEB-010 |
| `GET /p/{slug}/tasks/{task_id}` | Task detail (header + chains + history + trace) | `task_service.get` + `plan_service.forward/reverse_chain` + `revision_service` + `trace_service` | ✅ | WEB-070 |
| `POST /p/{slug}/tasks/audit` | AI consistency audit of all project tasks | `ai_text._call_lite_raw` + `task_service.list_for_project` | ✅ | OBI-tasks |
| `GET /p/{slug}/tasks/legacy` | List of legacy YAML tasks (paginated) | `Project.get_tasks` | ✅ | PCA-410 |
| `POST /p/{slug}/tasks/legacy/import` | Migrate legacy YAML tasks → DB (with WebSocket progress) | `restate_importer.import_legacy_tasks` | ✅ | PCA-410 |
| `POST /p/{slug}/tasks/legacy/archive` | Archive tasks.yaml → tasks.archived.yaml | filesystem rename | ✅ | PCA-410 |
| **Tasks — HTMX fragments for fields and status**  | | | |
| `GET /p/{slug}/tasks/{task_id}/fields/{field}/edit` | HTMX edit-form for description / acceptance | `task_service.get` | ✅ | WEB-071 |
| `GET /p/{slug}/tasks/{task_id}/fields/{field}/view` | HTMX view-fragment (Cancel target) | `task_service.get` | ✅ | WEB-071 |
| `POST /p/{slug}/tasks/{task_id}/fields/{field}` | Inline patch of a field (description / acceptance) | `task_service.update_description / update_acceptance` | ✅ | WEB-071 |
| `POST /p/{slug}/tasks/{task_id}/fields/{field}/improve` | LLM "improve" draft of a field (without a DB write) | `ai_text.improve_text_traced` | ✅ | WEB-071 |
| `POST /p/{slug}/tasks/{task_id}/status` | HTMX status change | `task_service.update_status` | ✅ | WEB-011 |
| `POST /p/{slug}/tasks/{task_id}/complete` | HTMX task completion | `task_service.complete` | ✅ | WEB-014 |
| **User Stories**  | | | |
| `GET /p/{slug}/stories` | User stories list (group by section/persona/none) | `story_service.list_for_project` + `doc_service.list_for_project` | ✅ | COD-068 |
| `GET /p/{slug}/stories/{story_id}` | Story detail + acceptance + linked tasks | `story_service.get` + `list_acceptance` + `list_tasks` | ✅ | COD-069 |
| `POST /p/{slug}/stories/generate` | AI generation of draft stories from MASTER.md | `ai_generate.generate_stories` | ✅ | COD-068 |
| `POST /p/{slug}/stories/save` | Save selected story drafts | `story_service.create` | ✅ | COD-068 |
| `POST /p/{slug}/stories/{story_id}/status` | Promote a story between statuses | `story_service.update_status` | ✅ | COD-069 |
| `POST /p/{slug}/stories/{story_id}/tasks/generate` | AI generation of tasks for a story | `ai_generate.generate_tasks_for_story` | ✅ | COD-069 |
| `POST /p/{slug}/stories/{story_id}/tasks/save` | Save generated tasks + link to the story | `task_service.create` + `story_service.link` | ✅ | COD-069 |
| `POST /p/{slug}/stories/coverage/analyze` | AI analysis of document coverage for story-generation | `ai_text._call_lite_raw` + `doc_service.list_for_project` | ✅ | COD-068 |
| `POST /p/{slug}/stories/section/{section_key}/analyze` | AI summary for one stories section | `section_summary_service.generate` | ✅ | COD-069 |
| **ADR — Architecture Decision Records**  | | | |
| `GET /p/{slug}/adr` | ADR list with status filter | `adr_service.list_for_project` | ✅ | ADR-004 |
| `GET /p/{slug}/adr/new` | ADR creation form | `adr_service` (form) | ✅ | ADR-005 |
| `POST /p/{slug}/adr/new` | Create an ADR | `adr_service.create` | ✅ | ADR-005 |
| `GET /p/{slug}/adr/graph` | Supersede DAG as Mermaid | `adr_service.graph` | ✅ | ADR-006 |
| `GET /p/{slug}/adr/{adr_id}` | ADR detail + diagrams + edit form | `adr_service.get` + `adr_to_dict` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/edit` | Edit ADR fields | `adr_service.update` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/diagram` | Add a Mermaid diagram to an ADR | `adr_service.add_diagram` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/supersede` | Record a supersede edge | `adr_service.supersede` | ✅ | ADR-005 |
| `POST /p/{slug}/adr/{adr_id}/deprecate` | Move an ADR to DEPRECATED | `adr_service.deprecate` | ✅ | ADR-005 |
| **Revisions, routines, agent runs**  | | | |
| `GET /p/{slug}/revisions` | Revision log (filter by entity) | `revision_service.list_for_project` | ✅ | WEB-021 |
| `GET /p/{slug}/routines` | Routine list + run history | `routine_service.list_routines` + `history` | ✅ | PCA-919 |
| `POST /p/{slug}/routines/create` | Create a routine | `routine_service.create` | ✅ | PCA-919 |
| `POST /p/{slug}/routines/{name}/toggle` | Enable/disable a routine | `routine_service.update_status` | ✅ | PCA-920 |
| `POST /p/{slug}/routines/{name}/run` | Manual routine run | `routine_service.run_now` | ✅ | PCA-920 |
| `POST /p/{slug}/routines/{name}/delete` | Delete a routine | `routine_service.delete` | ✅ | PCA-920 |
| `GET /p/{slug}/run` | Live agent console + run history | `run_service.list_recent` + `activity_service` | ✅ | WEB-030 |
| `GET /p/{slug}/run/{run_id}` | Single agent run detail | `run_service.get_one` + `activity_service.events_for_run` | ✅ | WEB-030 |
| **Search, commits, code-refs, metrics, costs**  | | | |
| `GET /p/{slug}/search` | FTS5 search over tasks/docs/stories/ADRs | `search_service.search` | ✅ | OBI-040 |
| `POST /p/{slug}/search/reindex` | Rebuild the project FTS index | `search_service.reindex_all` | ✅ | OBI-040 |
| `GET /p/{slug}/commits` | Table of commit↔task links | `commit_link_service.list_for_project` | ✅ | OBI-011 |
| `POST /p/{slug}/commits/import` | Rescan git log and import task-tagged commits | `commit_link_service.import_from_git_log` | ✅ | OBI-011 |
| `GET /p/{slug}/code-refs` | Project code-refs list | `link_service.list_code_refs` | ✅ | OBI-021 |
| `GET /p/{slug}/code-refs/preview` | Preview of the first 20 lines of a file (JSON) | `link_service.list_code_refs` + filesystem | ✅ | OBI-021 |
| `GET /p/{slug}/metrics` | Completion stats: sparkline + percentiles by type | `metrics_service.summary` + `sparkline_buckets` | ✅ | OBI-002 |
| `GET /p/{slug}/costs` | Cost dashboard by model (trace calls + pricing) | `trace_service.aggregate_by_model_for_project` | ✅ | PCA-925 |

**Principle:** the handler does not know about SQL/repositories. Only services (`cod_doc.services.*`) and existing helpers (`get_config`, `get_project`, `get_project_db`). See §7.

> **Status (2026-05-02, post WEB-040):** the rule is restored. `cod_doc/api/web/`
> imports only `cod_doc.services.*`, `cod_doc.api.deps`, `cod_doc.config`,
> `cod_doc.core`, `cod_doc.domain.entities`, `cod_doc.logging_config`. The AST test
> [tests/api/test_web_layer_imports.py](../../../tests/api/test_web_layer_imports.py)
> blocks regressions.

## 4. HTML structure

> **Target structure.** The real state and the "have/missing" matrix are in
> [roadmap/web-frontend-task-plan.md](../roadmap/web-frontend-task-plan.md)
> Progress Overview. Adding files for TBD-endpoints ahead of time is **not needed** —
> create only what a live task closes.

```text
cod_doc/api/web/
├── __init__.py            # router = APIRouter()         ← ✅
├── pages.py               # GET-pages                    ← ✅
├── fragments.py           # HTMX fragments               ← ✅
├── templates_env.py       # Jinja2Templates + STATIC_DIR  ← ✅
└── db_resolver.py         # bridge slug → DB session      ← ⚠ remove in WEB-040
                           #   (replace with get_project_db in cod_doc.api.deps)

cod_doc/templates/web/
├── base.html              # <html>, htmx, app.css; #alerts ← ✅
├── _layout/               # macros — shared fragments         ❌ (WEB-041)
│   ├── project_tabs.html  # tabs nav (active=…)               ❌ (WEB-041)
│   ├── header.html                                           ❌
│   └── nav.html                                              ❌
├── index.html             # project list                 ← ✅
├── settings.html                                            ❌ (WEB-020)
├── project/
│   ├── show.html          # dashboard                     ← ✅
│   ├── docs_list.html     # document list                 ← ✅
│   ├── doc_show.html      # document view                 ← ✅ (raw markdown — WEB-006)
│   ├── tasks_list.html    # task table + filter           ← ✅
│   ├── plan_show.html     # Plan + Mermaid                ❌ (WEB-004)
│   ├── revisions.html     # revision log                  ❌ (WEB-021)
│   └── run.html           # SSE-console                    ❌ (WEB-030)
└── _frag/
    ├── task_row.html      # task table row                ← ✅
    ├── section_view.html  # document section              ❌ (WEB-012)
    ├── section_edit.html  # textarea + concurrency token  ❌ (WEB-012)
    └── alert.html         # error/notification in #alerts  ❌ (WEB-022)

cod_doc/static/
├── app.css                # ~210 LOC, raw CSS             ← ✅
├── htmx.min.js            # v2.0.4 vendored                ← ✅
└── mermaid.min.js                                          ❌ (WEB-004)
```

## 5. UX invariants

- Each page renders with a single SQL query to data + one to stats. No N+1.
- Inline editing (task status, section patch) goes via HTMX `hx-post` → the server returns an HTML fragment of the row/section that replaces the old one. No JSON-API in these endpoints — only HTML. The JSON variant stays in `/api/*`.
- Any service error (NotFound, Conflict, Validation) is shown as an alert banner at the top of the page (HTMX target `#alerts`) or as red text next to the field. No silent redirects.
- All forms are plain `<form method="post">`, they work without JS. HTMX is a progressive enhancement.
- URLs use the project `slug` and `doc_key` / `task_id` — the same keys as in the DB and MCP. This gives a URL ↔ markdown link match.

## 6. Live operations (agent, import)

Long operations (`Orchestrator.run_autonomous`, Restate import) are served via **Server-Sent Events**, not via WebSocket. Reason:

- SSE is a simple `text/event-stream`, natively supported by HTMX (`hx-ext="sse"`), requires no JS libraries.
- WebSocket in [webhooks.py](../../../cod_doc/api/webhooks.py#L120) stays for machine clients; the web-console uses SSE.
- Connection is one-way (server → client); cancellation — via `DELETE /p/{slug}/run/{run_id}`.

## 7. Compliance with services and DI conventions

A web page is not allowed to bypass a service. The rule is checked in `cod-doc audit`
(`web_calls_services_only`) and in the import banlist (ruff
`flake8-tidy-imports.banned-module-level-imports` after WEB-040).

| Page | Allowed modules | Forbidden |
|----------|-------------------|-----------|
| `/` | `cod_doc.config`, `cod_doc.api.deps` | `cod_doc.infra.*` |
| `/p/{slug}/*` | `cod_doc.services.*`, `cod_doc.api.deps`, `cod_doc.domain.entities` (enums only) | `cod_doc.infra.*`, ORM models |
| `/settings` | `cod_doc.config`, `cod_doc.api.deps` | `cod_doc.infra.*` |

### DI: how a page gets a DB session

The "slug → SQLAlchemy Session + project_db_id" resolve is the only way
to work with the per-project DB from the web layer. After WEB-040 it lives in
[cod_doc/api/deps.py](../../../cod_doc/api/deps.py) as a FastAPI dependency:

```python
# target contract (after WEB-040):
def get_project_db(slug: str) -> tuple[Session, int]: ...
# returns (session, project_db_id) or raises HTTPException:
#   404 — project missing or its DB not initialized;
#   503 — DB present, but its schema diverged from the migration head
#         (body: {"code": "schema_mismatch", "project", "detail", "hint"}).
# session — from a cached Engine (see WEB-005)
```

> **STO-026 (2026-09-07):** schema desync is distinguishable from "project missing".
> `get_engine_for_slug` still returns `None` in both cases — whoever needs
> the difference calls `deps.resolve_engine(slug)` and reads `schema_error`. That
> is how `get_project_db` and the legacy task endpoints are made; graceful pages
> (`try_open_project_db`) are intentionally left with a generic "DB not initialized":
> a listing is not the place for migration diagnostics.

Forbidden:
- `from cod_doc.infra.db import make_engine`,
- `from cod_doc.infra.repositories import ProjectRepository`,
- importing ORM models `from cod_doc.infra.models import …`.

Allowed:
- `from cod_doc.domain.entities import TaskStatus, DocumentStatus, …` — only
  domain enums and dataclasses; they are not tied to the DB.

Direct access from Web to `cod_doc.infra.db` or ORM models — forbidden.

> **Closed (2026-05-02):** WEB-040 + WEB-005 closed; the web layer no longer
> imports `cod_doc.infra.*`. Regressions are caught by the AST test
> [tests/api/test_web_layer_imports.py](../../../tests/api/test_web_layer_imports.py).

### How to add a new web page (DI-pattern)

Recipe for a new handler in `cod_doc/api/web/pages.py` or `fragments.py`:

```python
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import doc_service as docs

router = APIRouter()

# ── Strict (404 if DB not ready) ───────────────────────────────────
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

# ── Graceful (render with warning if DB not ready) ────────────────────
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
strict (404 if the DB is missing). **List pages** (overview, table) → graceful
(show a warning, do not crash). Cross-project guard for entity-id
endpoints — a service-helper like `plan_service.get_for_project(...)`.

## 8. Testing

- **Smoke**: `fastapi.testclient.TestClient`, each page returns 200 on the seed-project.
  Current suite — `tests/api/test_web_*.py`, **137 tests, all green**.
- **Error-branch coverage** (part of the DoD of every write-path task):
  - form validation (400 on garbage),
  - revision conflict (`RevisionConflictError`),
  - FK violation (`IntegrityError`),
  - domain refusal (`ValueError` from a service).
  Without all four branches — do not close the write-path.
- **HTML-fragment snapshot tests** — no, and not planned. Fragments are tested
  via service-tests + smoke-structural asserts (`'badge-pending' in r.text`).
  HTML-snapshot is noisy on every cosmetic edit.
- **E2E (Playwright)** — deferred until §3 is closed. Trigger: the appearance of ≥3 multi-step
  scenarios (e.g. "create → edit a section → revert a revision").

## 9. Roadmap

Implementation — [roadmap/web-frontend-task-plan.md](../roadmap/web-frontend-task-plan.md). Dependencies on the core:

- Section A (scaffold) — independent.
- Doc/Task/Plan pages — after the corresponding services (COD-010 ✅ / COD-011 ✅ / COD-012 ✅).
- Revisions — after COD-015 ✅.
- Live-console — after `Orchestrator` stabilizes (outside this plan).

## 10. Alternative rejected at the start

**SPA (Vite + React + TanStack Query).** Advantages: rich widgets (drag-n-drop in the plan, interactive graph), reuse on mobile. Disadvantages at the current stage:

- Schema duplication (TS-types vs Pydantic) or an OpenAPI generator → an extra layer.
- A separate build-pipeline, a separate deploy.
- CORS, tokens, devtools.
- Regressions on the "UI lags behind CLI/MCP" principle — exactly what we avoid per [ARCHITECTURE.md §1](../ARCHITECTURE.md).

A return to SPA is possible when one of the non-goals above is needed (mobile, interactive graph). Until then — server-rendered.

---

## 11. Current state (2026-05-02)

A snapshot of the actual implementation. Kept in sync with
[roadmap/web-frontend-task-plan.md](../roadmap/web-frontend-task-plan.md) and
[audit/2026-05-02-section-web-frontend.md](../audit/2026-05-02-section-web-frontend.md).

### 11.1 Metrics

| Metric | Value |
|---|---:|
| Endpoints shipped | **13 / 14** (~93 %) |
| LOC python (`api/web`) | ~900 |
| LOC templates | ~600 |
| LOC `app.css` | ~370 |
| Web-tests | 137 (`pytest tests/api/ -q` ⇒ green) |
| Vendored JS | `htmx.min.js` v2.0.4 |
| Dependencies in `pyproject.toml` beyond baseline | **0** (as promised in §2) |

### 11.2 What works "today"

- **Project list** (`/`) — all registered projects, their stats, warning when unconfigured.
- **Project dashboard** (`/p/{slug}`) — 7 KPI cards, MASTER preview (first 80 lines),
  an "Open full" link, tabs.
- **Documents** (`/p/{slug}/docs` + `/p/{slug}/docs/{doc_key:path}`) — list, split-layout
  body+sections, graceful warning "DB not initialized".
- **Tasks** (`/p/{slug}/tasks`) — table with status filter, color-coded badges + priority,
  HTMX inline status update (POST `/{task_id}/status`), `<noscript>` fallback,
  POST/Redirect/GET for non-HTMX clients.

### 11.3 Architectural debt

| Debt | Where | Closed in |
|---|---|---|
| ~~Web → infra direct import~~ | ~~`db_resolver.py`~~ | ✅ **WEB-040** done 2026-05-02 |
| ~~Engine per request~~ | ~~`db_resolver.py`~~ | ✅ **WEB-005** done 2026-05-02 |
| ~~Index N+1 (`Project.stats()` per project)~~ | ~~`pages.py:27-35`~~ | ✅ **WEB-013** done 2026-05-02 |
| ~~Tabs duplicated in 3 templates, missing in `doc_show`~~ | ~~`templates/web/project/*`~~ | ✅ **WEB-041** done 2026-05-02 |
| ~~Tabs lead to 404 for unimplemented pages~~ | ~~shared~~ | ✅ **WEB-041** done 2026-05-02 |
| ~~`<div id="alerts">` without a model — errors silently lost~~ | ~~`base.html` + `fragments.py`~~ | ✅ **WEB-022** done 2026-05-02 |
| ~~`doc_show` body — raw markdown without anchors~~ | ~~`doc_show.html`, `pages.py:162`~~ | ✅ **WEB-006** done 2026-05-02 |
| ~~`status_options` duplicate~~ | ~~`pages.py:147`, `fragments.py:52`~~ | ✅ **WEB-041** done 2026-05-02 |

A full breakdown — in the audit report from 2026-05-02 (see the link above).

## 12. Changelog

| Date | Event |
|------|---------|
| 2026-04-28 | Capability document created (status: draft). |
| 2026-05-02 | Section A (Scaffold) + WEB-010/011 closed. Capability moved to `active`. §3 expanded with Status/Task columns; §4 — with a "target structure" note, explicit real state; §7 — DI convention and a link to WEB-040; §8 — error-branch coverage in the DoD; added §11 "Current state". See the audit report `2026-05-02-section-web-frontend.md`. |
| 2026-08-28 | ADO-011: §3 synchronized with all live web-routes (87 endpoints). Fixed WR-1 `/static/{path}`. |