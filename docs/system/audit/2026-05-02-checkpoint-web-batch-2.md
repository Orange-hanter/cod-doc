---
type: checkpoint-report
scope: cod_doc/api/web/* + cod_doc/services/* (Section B reads + polish + overview agg)
status: resolved
source_of_truth: false
canonical_source: docs/system/audit/2026-05-02-section-web-frontend.md
owner: cod-doc core
created: 2026-05-02
last_updated: 2026-05-02
covers_commits: [72209db, f712a59, 3776acb, 85754af]
related_docs:
  - 2026-05-02-section-web-frontend.md
  - 2026-05-02-checkpoint-web-batch-1.md
  - ../roadmap/web-frontend-task-plan.md
  - ../capabilities/web-frontend.md
---

# Web Frontend — Mid-Section Checkpoint #2 (2026-05-02)

> Cadence checkpoint after another 4 commits since checkpoint #1. Focus
> shifted from hardening (Section F) to read-views (Section B): markdown
> rendering, overview agg, revisions log; plus 3 small polish tickets
> from the previous checkpoint.

## 1. Batch under review

| # | Commit  | Task     | Description |
|---|---------|----------|-------------|
| 1 | 72209db | WEB-006  | Server-rendered markdown for `doc_show` body via custom mini-renderer (~110 LOC, no new deps); `<section id>` blocks make sidebar anchor nav work; `?raw=1` keeps the old `<pre>` view. |
| 2 | f712a59 | WEB-013b + WEB-022b + WEB-054 | Polish bundle: clamp empty-page summary on `/`; log WebError events at INFO; cap `flash_message` cookie length. |
| 3 | 3776acb | WEB-014  | Overview agg blocks (ready / plan progress / recent revisions) on the dashboard, plus `POST .../complete` HTMX button. New service helpers: `plan_service.list_for_project`, `revision_service.list_recent_for_project`. |
| 4 | 85754af | WEB-021  | Project-wide revisions log page with filter by `entity_kind` / `entity_id`. Revisions tab flipped to `ready=True`. |

Net code: +1100 prod LOC, +1500 test LOC since checkpoint #1.
Suite: 441 → 483 (+42); web-tests: 66 → 108 (+42).
Endpoints shipped: 5/14 → 8/14 (~57 %).

## 2. Sweep — original audit findings

Cumulative against the 16 findings in `2026-05-02-section-web-frontend.md`:

| Code | Title | Status |
|---|---|---|
| SW-HI-1 | Web → infra direct import | ✅ closed batch-1 (WEB-040) |
| SW-HI-2 | Engine per request | ✅ closed batch-1 (WEB-005) |
| SW-HI-3 | Index N+1 | ✅ closed batch-1 (WEB-013) |
| SW-HI-4 | No alert/error model | ✅ closed batch-1 (WEB-022) |
| SW-ME-1 | Tabs duplicated in 4 templates | ✅ closed batch-1 (WEB-041) |
| SW-ME-2 | Tabs to 404 | ✅ closed batch-1 (WEB-041) |
| **SW-ME-3** | **doc_show body raw markdown, broken anchors** | **✅ closed batch-2 (WEB-006)** |
| SW-ME-4 | Capability §3 status drift | ✅ closed audit-prep |
| SW-ME-5 | §4 skeletal templates absent | ✅ closed audit-prep |
| SW-ME-6 | `status_options` duplicated | ✅ closed batch-1 (WEB-041) |
| **SW-ME-7** | **No agg dashboard on overview** | **✅ closed batch-2 (WEB-014)** |
| SW-LO-1 | htmx.min.js without version-fingerprint | open → WEB-051 |
| SW-LO-2 | Error-branch coverage gaps | partial — WEB-022 added 7 + WEB-014 +3; WEB-052 still queued |
| SW-LO-3 | No test for missing master_path | open → WEB-052 |
| SW-LO-4 | audit `2026-04-28-section-c-capabilities` still active | ✅ closed batch-1 (WEB-040) |
| SW-LO-5 | `_alembic_upgrade` duplicated | open — WEB-053 part 2 |

**Cumulative tally: 13 of 16 baseline findings closed.** Remaining 3 are
non-blocking low-severity items in the WEB-051 / WEB-052 / WEB-053 queue.

## 3. New surfaces / risks introduced by batch-2

### 3.1 Mini-renderer scope creep risk

`cod_doc/api/web/markdown.py` covers paragraphs, fenced code, bullet lists,
inline `code`/**bold**/*italic*/[link](url). Anything outside (tables,
nested lists, footnotes, blockquotes) falls through to the paragraph fallback.

**Today this is fine** — service-level docs and module specs only use the
inline four. But if a user pastes a table, it'll render as a wall of `|` chars
inside `<p>`. Bug-shaped, not a crash.

Action: monitor incoming complaints. When the first table-in-doc complaint
lands, cut a `WEB-006b` (extend renderer) or revisit the `markdown-it-py`
ADR.

### 3.2 WebError logging is unbounded

`server.web_error_handler` logs every WebError at INFO — including the full
`exc.message`. A noisy probe (e.g. an LLM scanner generating 404s) inflates
log volume.

Action: defer. Operational issue, not user-facing; address with a rate-limit
filter if/when it surfaces. Track as a low-pri internal note.

### 3.3 `Plan.row_id is None` defensive assert in pages.py

`plans.list_for_project` always returns persisted rows, but the domain
dataclass declares `row_id: int | None`. We `assert` for mypy; if domain
ever leaks an in-flight Plan through this path, we'd AssertError instead
of producing a 500 with context.

Action: not worth changing now. Future-proof: replace with a typed
`def list_persisted_for_project(...) -> list[PersistedPlan]` if the dataclass
ever splits. Internal only.

### 3.4 task complete redirect destination

`POST /tasks/{id}/complete` → 303 to `/p/{slug}` (overview), regardless of
where the form came from. Dashboard ✓ button is the primary path; tasks-list
form (no JS) lands on overview, not back on tasks. Minor UX wrinkle.

Action: pass a `next_url` form field or use `Referer` like the alert handler.
Low-pri; bundle into next polish pass if nothing bigger calls for attention.

### 3.5 Tab strip churn

Revisions tab flipped to `ready=True` in `_layout/project_tabs.html`. Every
tab-strip test had to be updated. The pattern works — flip one bool, update
2 tests — but it's a regression vector for tab tests when more tabs go live.

Action: consolidate the tab-state expectations into a single fixture/parametrize
so flipping a tab updates one place, not multiple. Cut **WEB-053b** (low-pri).

## 4. Health metrics

| Metric | After batch-1 | After batch-2 | Δ |
|---|---:|---:|---:|
| Web endpoints shipped | 5 / 14 | 8 / 14 | +3 (revisions, task-complete, doc?raw=1) |
| Web LOC (api/web + templates + static) | ~1100 | ~1700 | +55 % |
| Web tests | 66 | 108 | +64 % |
| Suite total | 441 | 483 | +9.5 % |
| Suite runtime (full) | 108 s | 106 s | −2 s (warm-cache effect) |
| Suite runtime (web only) | 18 s | 22 s | +22 % |
| Ruff on touched files | clean | clean | — |
| Mypy on touched files | clean | clean | — |
| `cod_doc.infra.*` imports under `cod_doc/api/web/` | 0 | 0 | — |
| Closed baseline findings | 11 / 16 | 13 / 16 | +2 |

## 5. Items to address before continuing

| Priority | Item | Why |
|---|---|---|
| **low** | `WEB-053b` — consolidate tab-state test fixtures | Each new tab going live forces N test updates; central fixture removes the cost. Bundle when 1-2 more tabs are flipped. |
| **low** | `WEB-014b` — task-complete `next_url` for non-HTMX | Form-post from tasks list redirects to overview, not back to tasks. Mostly cosmetic. |
| **low** | track for future: mini-renderer table support | Defer until first user complaint. |

None of these block progress. Recommended next: **WEB-004 (plan view +
Mermaid)** — biggest remaining endpoint, last big read-view, would round out
Section B (4/6 → 5/6) and flip the Plans tab live in one PR.

## 6. Decisions deferred

- `markdown-it-py` ADR — keep deferred until mini-renderer hits its scope wall.
- WEB-022 logging rate-limit — defer until log volume becomes a real problem.

## 7. Changelog

| Дата | Событие |
|---|---|
| 2026-05-02 | Checkpoint после 4 коммитов batch-2 (commits 72209db → 85754af). 13/16 находок baseline-аудита закрыты; 3 новых внутренних item (WEB-053b, WEB-014b, mini-renderer table watch); 0 регрессий, suite 441 → 483. Endpoints shipped 5→8/14. |
