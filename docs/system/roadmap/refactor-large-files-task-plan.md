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

> A preparatory plan to split files > 400 lines into compact modules aligned by logic. **Decomposition only** — the public API of services/commands/routes stays the same; changes are visible as new internal modules and `from … import …` at assembly points.
>
> The goal is to reduce cognitive load and simplify parallel work: after the refactor each module is ≤ ~350 lines, with a clear topic of responsibility.
>
> **Out of scope:** tests stay byte-for-byte green, no behavior change, no new features, no "drive-by" refactoring. Any edits outside the split go as separate tasks.

## Navigation

- [Task-plan standard](../standards/task-plan.md)
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

> **Status reconciliation 2026-06-05** (see [ROADMAP](ROADMAP.md)): the table above was a stale draft ("0 done") — reconciliation with the code showed the decomposition **is already done**: packages `cod_doc/services/{plan_service,link_service,story_service,validation,projection_service}/`, `cod_doc/infra/models/` (20 files), `cod_doc/cli/{doc,plan,story}/`, `cod_doc/api/web/{pages,fragments}/`, `cod_doc/tui/screens/wizard/`, CSS-split (`static/app.css` → `static/css/_*.css`). A residual low-value item (a standalone frontmatter parser COD-050 — the logic is embedded in `projection_service/_frontmatter.py`) does not block. The plan is closed.

## Decomposition principles

Applied to all tasks below without repetition.

1. **The public API does not change.** Service-function names, CLI commands, FastAPI routes, MCP tools, and ORM models stay on the same import paths. A re-export from `__init__.py` or from the original "facade" module is allowed.
2. **Files by topic, not by size.** First find clusters (parse / resolve / verify; CRUD vs graph queries; pages vs htmx fragments) — then move them into a separate module. The target is ≤ 350 lines, but 200 is fine, 400 is acceptable for a module with one dense topic.
3. **No cyclic imports.** If the extraction creates a cycle — rethink the boundary or introduce an `_internals.py` with shared helpers.
4. **Tests are not moved.** In Section H ONLY already large test modules are split, and only if they have a natural boundary (by feature / by scenario). The fixture structure is preserved.
5. **One task — one PR.** The PR contains exactly the code move + minimal import fixes + (if needed) a re-export. No type fixes, formatting, or forced behavior changes. If a bug is found along the way — a separate task.
6. **Verify-loop:** `pytest -q` + `ruff check` + `mypy` green BEFORE and AFTER the task. The commit diff reads in five minutes.

## Naming convention

`PREFIX = RFL` (refactor large files). Numbering by section: A → 001-009, B → 010-019, C → 020-029, …

---

## Section A — Web layer (FastAPI)

### RFL-001 — Refactor: split `cod_doc/api/web/pages.py` (828 LOC)

**Type:** refactor   **Priority:** high   **Section:** A-Web-Layer

**Current state.** One file with routes for all pages: index, project overview, docs (list/show/import), tasks (list/show), plans (list/show), revisions log, settings (GET/POST). Inside sit two standalone helpers (`_masked_api_key`, `_preview`) and limit constants.

**Target structure.** Create a package `cod_doc/api/web/pages/` with an init and submodules:

```
cod_doc/api/web/pages/
├── __init__.py        # router = APIRouter(); include sub-routers (or re-export)
├── index.py           # GET /  + INDEX_*_LIMIT
├── project.py         # GET /p/{slug}, POST /p/{slug}/init  + OVERVIEW_*_LIMIT, _preview
├── docs.py            # GET /p/{slug}/docs, GET /p/{slug}/docs/{key}, POST /docs/import
├── tasks.py           # GET /p/{slug}/tasks, GET /p/{slug}/tasks/{task_id}
├── plans.py           # GET /p/{slug}/plans, GET /p/{slug}/plans/{plan_id} + PLAN_READY_LIMIT
├── revisions.py       # GET /p/{slug}/revisions  + REVISIONS_PAGE_LIMIT
└── settings.py        # GET/POST /settings  + _masked_api_key
```

`__init__.py`: creates `router = APIRouter()` and includes sub-routers via `router.include_router(...)`. The old import `from cod_doc.api.web.pages import router` keeps working. Templates (`templates/...`) and URL paths — unchanged.

**Acceptance.**
- [ ] All original routes are available at the same URLs and return the same HTML.
- [ ] The import `from cod_doc.api.web.pages import router` (as in [cod_doc/api/server.py](../../../cod_doc/api/server.py)) works without edits.
- [ ] Each file ≤ 250 LOC.
- [ ] `pytest tests/api/` green, a manual smoke `curl /` / `/p/<slug>` / `/settings` matches the baseline.

---

### RFL-002 — Refactor: split `cod_doc/api/web/fragments.py` (511 LOC)

**Type:** refactor   **Priority:** high   **Section:** A-Web-Layer

**Current state.** One file mixing three independent HTMX scenarios: task status change, inline patching of document sections, inline editing of task fields (description / acceptance), plus "complete" from the ready block. Shared helpers (`_is_htmx`, `_render_*`) live nearby.

**Target structure.** Package `cod_doc/api/web/fragments/`:

```
cod_doc/api/web/fragments/
├── __init__.py        # router + include_router
├── _shared.py         # _is_htmx, _render_task_row (shared by status/complete)
├── tasks_status.py    # POST /tasks/{id}/status, POST /tasks/{id}/complete
├── tasks_fields.py    # GET/POST /tasks/{id}/fields/{field}/...  + _TASK_FIELDS map
└── sections.py        # GET/POST /docs/{key}/sections/{anchor}/...  + _resolve_section
```

`_TASK_FIELDS` — a private registry, moves to `tasks_fields.py`. `_resolve_section` — to `sections.py`. Re-export `router` from `__init__.py`.

**Acceptance.**
- [ ] HTMX swap targets return the same HTML (visually + a byte diff).
- [ ] Cookie-flash and OOB-alert behavior is preserved (especially in `task_complete` with the Referer fallback).
- [ ] Each file ≤ 220 LOC.

---

## Section B — Services

### RFL-010 — Refactor: split `cod_doc/services/plan_service.py` (742 LOC)

**Type:** refactor   **Priority:** high   **Section:** B-Services

**Current state.** One file holds five heterogeneous subsystems: dataclass-DTO, `recalc` aggregations (views), `ready` selection, `audit` + cycle detector (DFS), `export` markdown renders, `forward/reverse/critical_path` via CTE. The file is already marked with `── Internals/recalc/ready/audit/export/Graph queries ──` — the boundaries are there, just materialize them.

**Target structure.** Turn into a package `cod_doc/services/plan/`:

```
cod_doc/services/plan/
├── __init__.py            # re-export the whole public API (see __all__ below)
├── _types.py              # DerivedStatus, SectionProgress, PlanProgress,
│                          # PlanAuditReport, ChainEntry, CriticalPathResult,
│                          # PlanNotFoundError, TaskNotFoundInPlanError
├── _internals.py          # _require_plan, _derive_status, _PRIORITY_ORDER
├── reads.py               # get_for_project, list_for_project, recalc, ready
├── audit.py               # audit + _find_cycles
├── export.py              # export + _render_progress_overview / _render_next_batch /
│                          # _render_dependency_graph / _mermaid_node_id
└── graph.py               # forward_chain, reverse_chain, critical_path + SQL CTE constants
```

Backward compatibility: the module `cod_doc/services/plan_service.py` stays as a thin facade with `from cod_doc.services.plan import *  # noqa: F401,F403` — all calls `from cod_doc.services import plan_service as plans` keep working.

**Acceptance.**
- [ ] `tests/services/test_plan_service.py` — unchanged, green.
- [ ] Each module ≤ 250 LOC; `graph.py` may be up to 320 LOC due to two CTEs.
- [ ] `from cod_doc.services import plan_service` and `from cod_doc.services.plan_service import recalc` work.

---

### RFL-011 — Refactor: split `cod_doc/services/link_service.py` (723 LOC)

**Type:** refactor   **Priority:** high   **Section:** B-Services

**Current state.** The file combines three independent responsibilities: a pure regex link parser (parse + wiki-inner classification), DB-bound resolve/verify (with six `_resolve_*` helpers), rename-cascade (two rewrite systems: canonical `[[doc:OLD]]` and markdown-relative with path_map).

**Target structure.** Package `cod_doc/services/link/`:

```
cod_doc/services/link/
├── __init__.py            # re-export public API
├── _types.py              # ParsedLink, VerifyReport, RenameCascadeReport,
│                          # LinkNotFoundError
├── parser.py              # PURE: parse, _strip_fenced_code, _href_to_doc_key,
│                          # _classify_wiki_inner + regex constants
├── resolver.py            # _resolve_canonical/_resolve_section_anchor/
│                          # _resolve_task/_resolve_story/_resolve_wiki +
│                          # _apply_resolution + sync_section/resolve/resolve_section/
│                          # verify_section/list_for_section
├── _section_helpers.py    # _section_or_raise, _project_id_for_section,
│                          # _link_or_raise (internal, shared)
└── rename_cascade.py      # rename_cascade, _rewrite_canonical_refs,
                           # _rewrite_markdown_relative_refs, _resolve_md_href,
                           # _make_relative_href
```

Facade: `cod_doc/services/link_service.py` → `from cod_doc.services.link import *  # noqa`.

**Acceptance.**
- [ ] `tests/services/test_link_service.py` (932 LOC, 35+ tests) — green without edits.
- [ ] `parser.py` does NOT import SQLAlchemy (a pure function — this is the invariant we want to lock in).
- [ ] Each module ≤ 280 LOC.

---

### RFL-012 — Refactor: split `cod_doc/services/story_service.py` (479 LOC)

**Type:** refactor   **Priority:** medium   **Section:** B-Services

**Current state.** One file, but the topics are already clearly separated by dotted lines: create/get/list, update_status, acceptance-criteria, link, coverage. Plus the `_validate_link_target` utility (important — prevents broken links).

**Target structure.** Package `cod_doc/services/story/`:

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

The file `cod_doc/services/story_service.py` stays as a facade with re-export.

**Acceptance.**
- [ ] `tests/services/test_story_service.py` (605 LOC) — green.
- [ ] Each module ≤ 200 LOC.

---

### RFL-013 — Refactor: split `cod_doc/services/projection_service.py` (404 LOC)

**Type:** refactor   **Priority:** low   **Section:** B-Services

**A file on the edge (404 LOC).** The topics are already separated: render_markdown (pure, with redaction logic and audience-rank), export_document, detect_drift, import_document, plus `_safe_target` (a security-critical helper) and `_parse_frontmatter`/`_apply_frontmatter_to_model`.

**Decision:** split — because:
1. `_safe_target` — a security guard, must be in one module with path-escape tests (see `test_export_refuses_*` in [test_projection_service.py](../../../tests/services/test_projection_service.py)).
2. Audience redaction (`_audience_blocks_sensitivity`, `_REDACTION_MARKER`) — a separate concept (COD-025/SD-002), with its own lifecycle.

**Target structure.** Package `cod_doc/services/projection/`:

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
└── import_doc.py          # import_document  (the name `import.py` is forbidden — keyword)
```

**Acceptance.**
- [ ] `tests/services/test_projection_service.py` (411 LOC) — green.
- [ ] Each module ≤ 130 LOC.
- [ ] `_safety.py` is imported only from `export.py` / `drift.py`; the frontmatter parser does not know about the disk path.

---

### RFL-014 — Refactor: split `cod_doc/services/validation.py` (402 LOC)

**Type:** refactor   **Priority:** low   **Section:** B-Services

**A file on the edge (402 LOC).** Inside there are two explicit categories: structural validators (raise `ValidationError`) and advisory validators (return `list[ValidationIssue]`). Per the memory `validation_pattern.md`, splitting along this boundary is the project's main pattern; materializing it in code reinforces it.

**Target structure.** Package `cod_doc/services/validation/`:

```
cod_doc/services/validation/
├── __init__.py            # re-export everything so from cod_doc.services import validation keeps working
├── _errors.py             # ValidationError, ValidationIssue
├── _patterns.py           # _TASK_ID_RE, _STORY_ID_RE, _SECTION_SLUG_RE,
│                          # _ID_PREFIX_RE, _VERB_PATTERNS, _FORBIDDEN_TYPE_ALIASES,
│                          # _FM007_REQUIRED_TYPES
├── structural.py          # validate_task_id / _id_prefix / _story_id /
│                          # _section_slug / _task_type / _doc_path
└── advisory.py            # audit_task_title, audit_frontmatter, audit_sensitivity
```

All imports of the form `from cod_doc.services import validation` and `from cod_doc.services.validation import ValidationError` keep working.

**Acceptance.**
- [ ] All calls `validation.validate_*` / `validation.audit_*` in [doc_service](../../../cod_doc/services/doc_service.py), [story_service](../../../cod_doc/services/story_service.py), [task_service](../../../cod_doc/services/task_service.py) work without import edits.
- [ ] Tests on validation (if any separate ones) and the integration tests of the services — green.

---

## Section C — Infra (ORM models)

### RFL-020 — Refactor: split `cod_doc/infra/models.py` (521 LOC)

**Type:** refactor   **Priority:** medium   **Section:** C-Infra

**Current state.** One file with 17 SQLAlchemy models. They already group by domain, but visually it is a "wall of code" — finding `LinkModel` or `RevisionModel` by eye is hard.

**Caveat:** SQLAlchemy is sensitive to model load order (relationships reference classes by string name, but `Base.metadata` must know about all tables BEFORE the first `create_all`). So the package `__init__.py` **must import all submodules** for the side-effect of registration in `Base.metadata`.

**Target structure.** Package `cod_doc/infra/models/`:

```
cod_doc/infra/models/
├── __init__.py            # from .base import Base
│                          # from .project import ProjectModel
│                          # from .documents import DocumentModel, SectionModel, LinkModel
│                          # ...  (import for registration + re-export)
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

`__init__.py` re-exports ALL models and `Base` itself. The import `from cod_doc.infra.models import DocumentModel` (as in [doc_service.py](../../../cod_doc/services/doc_service.py)) is preserved.

**Special attention.**
- Alembic migrations compare against `Base.metadata.tables` — after the refactor `alembic check` (or `alembic revision --autogenerate --dry-run`) must NOT show a diff.
- Cascade behavior and string-based `relationship(...foreign_keys="DependencyModel.from_task_id")` do not change (the class names are the same).

**Acceptance.**
- [ ] `pytest` is green (including the alembic-fixture tests in `tests/services/`).
- [ ] `alembic check` ↔ `Base.metadata` — no diff.
- [ ] Each module ≤ 130 LOC.

---

## Section D — CLI (Click groups)

### RFL-030 — Refactor: split `cod_doc/cli/doc.py` (519 LOC)

**Type:** refactor   **Priority:** medium   **Section:** D-CLI

**Current state.** A `@click.group()` with eight commands: list, show, create, rename, body, export, drift, import. Each command is self-contained, the group has a common `--project` prefix and three helper functions (`_make_session`, `_require_project_id`, `_get_root_path`).

**Target structure.** Package `cod_doc/cli/doc/`:

```
cod_doc/cli/doc/
├── __init__.py            # the `doc` group is defined here;
│                          # imports sub-commands for registration
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

The registration pattern — as in `cod_doc/mcp/tools/` (an already working precedent in the codebase).

`__init__.py`:
```python
import click
@click.group()
def doc() -> None: ...
from . import cmd_list, cmd_show, cmd_create, cmd_rename, cmd_body, cmd_export, cmd_drift, cmd_import  # noqa: E402, F401
```

Each `cmd_*.py` starts with `from . import doc` (or `from cod_doc.cli.doc import doc`) and is registered via `@doc.command(...)`.

**Acceptance.**
- [ ] `cod-doc doc --help` shows the same subcommands.
- [ ] `cod-doc doc list -p X`, `cod-doc doc create ...`, `cod-doc doc drift ...` work as before.
- [ ] The import `from cod_doc.cli.doc import doc` (wherever it is — via `entry_points` or via `cli/__main__.py`) is preserved.
- [ ] Each command file ≤ 100 LOC.

---

### RFL-031 — Refactor: split `cod_doc/cli/story.py` (489 LOC)

**Type:** refactor   **Priority:** medium   **Section:** D-CLI

**Analogous to RFL-030.** Seven commands: list, show, create, status, add-criterion, link, coverage. `_STATUS_ICON`, `_COVERAGE_ICON`, `_make_session`, `_require_project_id` → `_common.py`.

**Target structure.** `cod_doc/cli/story/__init__.py` + `_common.py` + `cmd_<name>.py` × 7.

**Acceptance.** Analogous to RFL-030: commands and imports work, each `cmd_*.py` ≤ 100 LOC.

---

### RFL-032 — Refactor: split `cod_doc/cli/plan.py` (428 LOC)

**Type:** refactor   **Priority:** medium   **Section:** D-CLI

**Analogous to RFL-030.** Seven commands: show, ready, audit, export, critical-path, forward, reverse. Additionally — the shared helper `_render_chain` (used by forward + reverse) → into `_common.py`.

**Target structure.** `cod_doc/cli/plan/__init__.py` + `_common.py` (with `_render_chain`) + `cmd_<name>.py` × 7.

**Acceptance.** Analogous to RFL-030.

---

## Section E — MCP server

### RFL-040 — Refactor: split `cod_doc/mcp/server.py` (583 LOC)

**Type:** refactor   **Priority:** medium   **Section:** E-MCP

**Current state.** The file registers ~18 inline tools and three resources/three prompts on a FastMCP instance. Meanwhile the DB-tools are already moved to [cod_doc/mcp/tools/](../../../cod_doc/mcp/tools/) — but the "legacy" tools (project/task management, MASTER.md hashes, context delivery, agent orchestration, config, semantic search) stayed in `server.py`.

**Target structure.** Move the rest into the existing package `cod_doc/mcp/tools/`:

```
cod_doc/mcp/
├── server.py              # ONLY create the FastMCP instance, register all tools/
│                          # packages, click main()  — ~80 LOC
└── tools/
    ├── _legacy_helpers.py # _config, _project, _project_summary  (internal utils)
    ├── project_tools.py   # NEW: list_projects, get_project_status, add_project, remove_project
    ├── task_tools_legacy.py # NEW: list_tasks, add_task, update_task, next_pending_task
    │                        #     (do NOT confuse with the existing task_tools.py — DB-side)
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

Each new module exports a `register(mcp)` function (as the existing [doc_tools.py](../../../cod_doc/mcp/tools/doc_tools.py) does). `server.py` shrinks to:

```python
mcp = FastMCP("COD-DOC", json_response=True)
for mod in (project_tools, task_tools_legacy, master_tools, context_tools,
            hash_tools, search_tools, agent_tools, config_tools,
            doc_tools, task_tools, plan_tools, story_tools, link_tools,
            revision_tools, resources, prompts):
    mod.register(mcp)
```

**Attention:** `task_tools.py` (DB-side) and `task_tools_legacy.py` (YAML-side, via `Project`) are DIFFERENT tools, do not merge them. Pick a better name — e.g., `project_tasks_tools.py` or `legacy/task_tools.py` (a nested `tools/legacy/` folder if there are many in the end).

**Acceptance.**
- [ ] `cod-doc-mcp` (or an equivalent entry-point) starts, the list of tools/resources/prompts via MCP `list_tools` matches the baseline (capture before and after).
- [ ] `server.py` ≤ 100 LOC.
- [ ] Each new `*_tools.py` ≤ 200 LOC.

---

## Section F — TUI screens

### RFL-050 — Refactor: split `cod_doc/tui/screens/wizard.py` (450 LOC)

**Type:** refactor   **Priority:** low   **Section:** F-TUI

**Current state.** One Textual screen with an embedded `_StepBar` widget, four steps (welcome / API / project / done) and validation. ~165 LOC of 450 is `DEFAULT_CSS` (styles).

**Target structure.**

```
cod_doc/tui/screens/wizard/
├── __init__.py            # re-export WizardScreen
├── screen.py              # class WizardScreen + compose() + on_mount()
├── _stepbar.py            # class _StepBar
├── _styles.py             # WIZARD_CSS (string constant)
├── _steps.py              # stateless render functions for each step:
│                          # render_welcome(), render_api_step(), render_project_step(),
│                          # render_done_step()  — return list[Widget]
├── _validation.py         # validate_and_save_api(config, ...),
│                          # validate_and_save_project(config, ...)
└── _models.py             # MODELS-list, STEPS-list, _model_widget_id()
```

`screen.py` imports `WIZARD_CSS` and assigns `DEFAULT_CSS = WIZARD_CSS`. The `compose()` method calls render functions from `_steps.py`. The validators are isolated from the UI and testable in unit tests (if they appear).

**Acceptance.**
- [ ] `cod-doc tui` launches the wizard, the four steps navigate as before.
- [ ] Saving the configuration (`config.save()`) runs at the same points.
- [ ] `screen.py` ≤ 180 LOC; `_steps.py` ≤ 130 LOC.

---

## Section G — Static assets (CSS)

### RFL-060 — Refactor: split `cod_doc/static/app.css` (768 LOC)

**Type:** refactor   **Priority:** low   **Section:** G-Static

**Current state.** One monolithic CSS with already marked comment-sections: layout/topbar, grid/tables, tabs, cards, master-preview, doc viewer (split + sections-nav + section bodies), overview agg blocks, settings form, section inline edit (WEB-012), overview tightening, **task detail page** (≈ 350 LOC — the largest zone), alerts, pagination. The key problem is navigation: "find the styles of the task hero zone" = scroll to line 370.

**Decision.** Break into partials and assemble via CSS-import (or concatenation at build time).

**Target structure.**

```
cod_doc/static/
├── app.css                # entry point; @imports partials in the right order:
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
                            # (≈ 350 LOC, the largest part)
```

`app.css` after the refactor:
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

**Alternative (if CSS @import creates extra HTTP round-trips):** keep a single `app.css`, but generate it by concatenation in a Makefile / build-step. The decision is informed by the fact that FastAPI serves static assets without HTTP/2 push — evaluate the load separately.

**Attention.**
- Selectors and the cascade order MUST match (the @import order = the order of the original file, plus "overview tightening" AFTER the original `.cards`/`.overview-grid`).
- Include the folder `css/**` in `package_data` (see [pyproject.toml](../../../pyproject.toml)).
- Visually check all five pages via playwright scripts (`.cod-doc-pw-*.py` already exist in the repo) before and after.

**Acceptance.**
- [ ] All pages render identically (screenshots match, baseline in `/tmp/cod-doc-playwright-shots/`).
- [ ] Each partial ≤ 200 LOC; `pages/task-detail.css` may be up to 380 LOC.
- [ ] Serving via `/static/css/*` works (the StaticFiles mount is already there).

---

## Section H — Tests

> Tests are split only if they have a **natural scenario boundary**. The goal is NOT to shrink the file at all costs, but to group tests by behavior so that on a failure it is clear which feature broke.
>
> Everywhere the pattern `engine_with_schema` / `_run_alembic_upgrade` / `db_url` fixture is preserved — they move to `tests/services/conftest.py` (if not there yet) BEFORE the split. This is a separate subtask inside RFL-070.

### RFL-070 — Refactor: extract shared fixtures into `tests/services/conftest.py`

**Type:** refactor   **Priority:** medium   **Section:** H-Tests

**Why.** All test modules in `tests/services/` repeat:
```python
def _run_alembic_upgrade(db_url: str) -> None: ...
@pytest.fixture
def db_url(tmp_path: Path) -> str: ...
@pytest.fixture
def engine_with_schema(db_url: str): ...
```

(historically identical blocks lived in `test_doc_service.py`, `test_task_service.py`,
`test_plan_service.py`, `test_story_service.py`, `test_link_service.py`; after
the split see the current focused tests: [test_doc_create.py](../../../tests/services/test_doc_create.py),
[test_task_create.py](../../../tests/services/test_task_create.py),
[test_plan_recalc.py](../../../tests/services/test_plan_recalc.py),
[test_story_crud.py](../../../tests/services/test_story_crud.py),
[test_link_parser.py](../../../tests/services/test_link_parser.py),
[test_projection_service.py](../../../tests/services/test_projection_service.py)).

**Action.** Create (or extend) `tests/services/conftest.py` with these three elements. Remove the copies from the six modules. ⚠️ PRECEDES tasks RFL-071..RFL-075 — without it every subsequent split inflates the duplication.

**Acceptance.**
- [ ] `pytest tests/services/ -q` is green.
- [ ] In each of the six `test_*.py` the fixtures are removed, and the tests stay in place.
- [ ] Line reduction per file ~25 LOC (= a total minus ~150 LOC before the main split).

---

### RFL-071 — Refactor: split `tests/services/test_link_service.py` (932 LOC)

**Type:** refactor   **Priority:** medium   **Section:** H-Tests

**Existing markup** (see the `# ====` markers): parser tests / sync_section / resolve / verify / rename_cascade / rename_cascade with path_map / DocService↔link cascade.

**Target structure.**

```
tests/services/link/
├── __init__.py
├── _helpers.py              # _seed_project, _add_doc, _add_doc_with_path
├── test_parser.py           # block 1: pure parser tests (no DB)
├── test_sync_section.py     # block 2
├── test_resolve.py          # block 3
├── test_verify.py           # block 4
├── test_rename_cascade.py   # blocks 5+6 (key rename + path_map)
└── test_rename_cascade_integration.py  # block 7 (DocService rename → cascade)
```

**Acceptance.**
- [ ] `pytest tests/services/link/ -q` — the same number of tests, the same result.
- [ ] Each file ≤ 250 LOC.

---

### RFL-072 — Refactor: split `tests/services/test_story_service.py` (605 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Existing markup:** create / update_status / acceptance / link / coverage.

**Target structure.**

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

**Acceptance.** Each file ≤ 200 LOC; the test count is preserved.

---

### RFL-073 — Refactor: split `tests/services/test_doc_service.py` (566 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Groups:** create + create-validation / sections (add/list) / render_body / patch_section + concurrency / rename / path-validation.

**Target structure.**

```
tests/services/doc/
├── __init__.py
├── _helpers.py              # _add_project, _new_doc
├── test_create.py
├── test_sections.py         # add_section, get_sections
├── test_render_body.py
├── test_patch_section.py    # including the concurrency conflict
├── test_rename.py
└── test_path_validation.py  # SD-100, absolute/traversal paths
```

**Acceptance.** Each file ≤ 200 LOC.

---

### RFL-074 — Refactor: split `tests/services/test_plan_service.py` (490 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Groups (by the `# ====` markup):** recalc / ready / audit / export.

**Target structure.**

```
tests/services/plan/
├── __init__.py
├── _helpers.py              # _seed_plan_with_sections, _seed_task
├── test_recalc.py
├── test_ready.py
├── test_audit.py
└── test_export.py
```

After RFL-074 it also makes sense to move the `forward_chain` / `reverse_chain` / `critical_path` tests into `test_graph.py` (if they exist — find and move them too).

**Acceptance.** Each file ≤ 200 LOC.

---

### RFL-075 — Refactor: split `tests/services/test_task_service.py` (444 LOC)

**Type:** refactor   **Priority:** low   **Section:** H-Tests

**Groups:** create (+ id-generation, validation) / update_status / complete (with deps + concurrency) / list_for_plan.

**Target structure.**

```
tests/services/task/
├── __init__.py
├── _helpers.py              # _seed_plan, _task
├── test_create.py
├── test_update_status.py
├── test_complete.py
└── test_list.py
```

**Acceptance.** Each file ≤ 200 LOC.

---

## Out of scope (do not touch now)

Files > 400 LOC that are NOT covered by this plan — with a rationale:

| File | LOC | Reason not to split |
|:-----|----:|:---------------------|
| `docs/system/roadmap/web-frontend-task-plan.md` | 1339 | A plan document — intentionally one file; a split would break the cohesion of Progress Overview / Next Batch. |
| `docs/system/roadmap/cod-doc-task-plan.md` | 815 | Likewise. |
| `docs/HANDBOOK.md` | 759 | A single-document product guide (see commit f897bc0). A split contradicts the intent. |
| `docs/system/roadmap/audit-followups-task-plan.md` | 511 | An active plan, see above. |
| `docs/system/DATA_MODEL.md` | 499 | An architectural document; split recommendations — a separate audit. |
| `docs/cod-doc-guide.md` | 412 | A user guide; a split is possible but needs a UX decision. |
| `tests/services/test_projection_service.py` | 411 | On the edge, the topics are well marked, but only 411 — a split would give 4 files of ~100 LOC, which is not justified. If RFL-070 removes ~25 LOC of fixtures — it becomes 386, on the boundary. **Decision:** do not split in this iteration. |

## Sequencing

A safe order:

1. **RFL-070** (move fixtures to conftest.py) — **first**, otherwise every test split inflates duplication.
2. **RFL-014** (validation), **RFL-013** (projection) — the smallest services, low risk, work out the "service → package with a facade" pattern.
3. **RFL-010** (plan_service), **RFL-011** (link_service), **RFL-012** (story_service) — large services; use the already worked-out pattern.
4. **RFL-020** (models) — a separate PR, with special attention to the alembic diff.
5. **RFL-001** (pages), **RFL-002** (fragments) — the web layer; smoke-test via playwright.
6. **RFL-040** (mcp/server) — many small registrations, low risk.
7. **RFL-030**, **RFL-031**, **RFL-032** (CLI) — run in parallel.
8. **RFL-050** (TUI wizard) — an isolated zone, can be done anytime.
9. **RFL-060** (CSS) — after a visual snapshot via playwright.
10. **RFL-071..RFL-075** (tests) — last, when the public API of the services is already stable.

Each task — a separate PR, ≤ ~600 LOC diff, easy to review.

## Verify-loop (common to all tasks)

```bash
# before the split — capture the baseline
pytest -q  > /tmp/baseline.txt
ruff check
mypy cod_doc

# after the split
pytest -q  > /tmp/after.txt
diff /tmp/baseline.txt /tmp/after.txt   # should be empty (or only time)
ruff check
mypy cod_doc

# for web/CSS — playwright screenshots before/after
python .cod-doc-pw-task-detail.py   # already in the repo
```
