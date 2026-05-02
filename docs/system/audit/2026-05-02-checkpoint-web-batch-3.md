---
type: checkpoint-report
scope: cod_doc/api/web/* + cod_doc/services/plan_service.py (Section B closed + WEB-051)
status: resolved
source_of_truth: false
canonical_source: docs/system/audit/2026-05-02-section-web-frontend.md
owner: cod-doc core
created: 2026-05-02
last_updated: 2026-05-02
covers_commits: [5cc1617, d566956, d733840]
related_docs:
  - 2026-05-02-section-web-frontend.md
  - 2026-05-02-checkpoint-web-batch-1.md
  - 2026-05-02-checkpoint-web-batch-2.md
  - ../roadmap/web-frontend-task-plan.md
---

# Web Frontend — Mid-Section Checkpoint #3 (2026-05-02)

> Cadence checkpoint after the third batch of 3 commits. Section B (Read
> views) is now **closed (6/6)** — 5/6 tabs live (only Run remains disabled).

## 1. Batch under review

| # | Commit  | Task    | Description |
|---|---------|---------|-------------|
| 1 | 5cc1617 | WEB-004 | Plans list (`/p/{slug}/plans`) + plan detail (`/p/{slug}/plans/{id}`) with Progress / Next batch / Mermaid `<pre>`. Adds `plan_service.get_for_project` cross-project guard. Plans tab → live. |
| 2 | d566956 | WEB-060 | `/settings` GET/POST: config form with masked api_key, persistence via `Config.save()`, secret-field UX (empty=keep, `-`=clear). |
| 3 | d733840 | WEB-051 | `static_url('app.css')` Jinja global with mtime fingerprint; baked into `base.html` for both vendored assets. Closes SW-LO-1. |

Net code: +600 prod LOC, +400 test LOC since checkpoint #2.
Suite: 483 → 500 (+17); web-tests: 108 → 125 (+17).
Endpoints shipped: 8/14 → 10/14 (~71 %; only `/run` and `POST .../sections/{anchor}` remain).

## 2. Sweep — original audit findings

Cumulative against the 16 findings in `2026-05-02-section-web-frontend.md`:

| Code | Title | Status |
|---|---|---|
| SW-HI-1..4 | (4 high) | ✅ closed batch-1 |
| SW-ME-1..7 | (7 medium) | ✅ closed (5 batch-1, 1 audit-prep, SW-ME-3 + SW-ME-7 batch-2) |
| **SW-LO-1** | **htmx.min.js without version-fingerprint** | **✅ closed batch-3 (WEB-051)** |
| SW-LO-2 | Error-branch coverage gaps | partial — 7+3 tests added across WEB-022/014; WEB-052 still queued |
| SW-LO-3 | No test for missing master_path | open → WEB-052 |
| SW-LO-4 | audit C still active | ✅ closed batch-1 |
| SW-LO-5 | `_alembic_upgrade` duplicated | open — WEB-053 part 2 |

**Cumulative tally: 14 of 16 baseline findings closed.** Only 2 LOW
items remain, both bundled into the WEB-052 / WEB-053 queue.

## 3. New surfaces / risks introduced by batch-3

### 3.1 Tab fixture churn shifts from "many disabled" to "one disabled"

After WEB-021 the tabs were 4 live + 2 disabled; after WEB-004 it's 5 live
+ 1 disabled. WEB-053b (consolidate tab-state expectations) was queued in
checkpoint #2 as low-pri; now it's **more relevant** because the next tab
flip (WEB-030 → Run) will be the LAST one and we'll want to celebrate by
not breaking N tab tests one more time.

Action: keep WEB-053b at `low` for now, but bump if anyone touches the
include before WEB-030. Include in WEB-053 polish PR if convenient.

### 3.2 Test-fixture vs. app-lifespan footgun

`WEB-060` revealed: app lifespan calls `Config.load()`, clobbering any
prior `deps.set_config(cfg)` in the test fixture. Tests must `cfg.save()`
before entering the TestClient. This footgun is documented in the
`settings_client` fixture's docstring but **only there**.

Action: add a one-line note to `tests/api/conftest.py` explaining the
pattern, since `isolated_cod_doc_home` already lives there.

### 3.3 Cross-project guard pattern

`plan_service.get_for_project(session, project_id, plan_id)` is a new
pattern: returns `domain | None` iff entity belongs to the project. We'll
want the same for tasks (currently uses ad-hoc `existing.project_id !=
project_db_id` check inline in `fragments.py`) and docs (when WEB-012
lands).

Action: defer until 2nd usage. Generalising too early when there's only
one usage is YAGNI — but track the pattern. Cut a future refactor ticket
if 3 usages emerge.

### 3.4 Mermaid in `<pre>` is honest, not pretty

The dependency graph block in `plan_show.html` shows ` ```mermaid \n graph
TD ...` as plain text. Fine for "I want to grab the syntax and paste into
mermaid.live", less fine for casual browsing. Vendoring `mermaid.min.js`
is a real ADR (size: ~2.5 MB minified, but tree-shakeable to ~600 KB).

Action: leave as-is. Open ADR in `capabilities/decisions-and-questions.md`
when first user actually asks for interactive rendering.

### 3.5 Static asset cache lives for process lifetime

`_STATIC_VERSION_CACHE` in `templates_env.py` doesn't refresh between
requests. If a static file changes during a long-running uvicorn process,
the browser still receives the same `?v=<old-mtime>`. uvicorn's
`--reload` triggers a full process restart, so this is fine in dev.

Action: document the trade-off in the helper's docstring (already done)
+ no action needed in production paths.

## 4. Health metrics

| Metric | After batch-2 | After batch-3 | Δ |
|---|---:|---:|---:|
| Web endpoints shipped | 8 / 14 | 10 / 14 | +2 |
| Web LOC | ~1700 | ~2300 | +35 % |
| Web tests | 108 | 125 | +16 % |
| Suite total | 483 | 500 | +3.5 % |
| Suite runtime (full) | 106 s | 104 s | −2 s |
| Suite runtime (web only) | 22 s | 30 s | +36 % |
| Ruff on touched files | clean | clean | — |
| Mypy on touched files | clean | clean | — |
| `cod_doc.infra.*` imports under `cod_doc/api/web/` | 0 | 0 | — |
| Closed baseline findings | 13 / 16 | **14 / 16** | +1 |
| Disabled tabs | 2 | 1 | −1 |

## 5. Items to address before continuing

| Priority | Item | Why |
|---|---|---|
| **low** | Doc the fixture-vs-lifespan footgun in `tests/api/conftest.py` | Saves the next contributor 30 minutes. |
| **low** | WEB-053b: tab fixture consolidation | Last tab flip (WEB-030 → Run live) will need test updates again. |
| **low** | Cross-project guard generalisation | Wait for 3rd usage; track only. |

None block progress.

**Recommended next:**
- **WEB-012** (HTMX section patch) — last write-path for read-views; closes
  Section C (3/3) and the `POST /docs/.../sections/{anchor}` endpoint. Uses
  the same alert pipeline + Depends pattern already in place.
- Or **WEB-052** (error-branch tests) — small, closes SW-LO-2 fully and
  bumps test coverage with cheap effort.

My pick: **WEB-012** (one big endpoint left in the write-path slate; better
to close it before live-ops Section D opens).

## 6. Decisions deferred

- ADR for `mermaid.min.js` vendoring — defer until requested.
- ADR for `markdown-it-py` (WEB-006 follow-up) — defer until requested.
- Cross-project guard generalisation — defer until 3rd usage.

## 7. Changelog

| Дата | Событие |
|---|---|
| 2026-05-02 | Checkpoint #3 после 3 коммитов batch-3 (5cc1617, d566956, d733840). 14 / 16 находок baseline закрыты; Section B (Read views) закрыта целиком (6/6). Endpoints shipped 8 → 10 / 14 (~71 %). Suite 483 → 500. 0 регрессий, 0 архитектурных нарушений. |
