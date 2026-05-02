---
type: checkpoint-report
scope: cod_doc/api/web/* + tests/api/* (Section C closed + polish closure)
status: resolved
source_of_truth: false
canonical_source: docs/system/audit/2026-05-02-section-web-frontend.md
owner: cod-doc core
created: 2026-05-02
last_updated: 2026-05-02
covers_commits: [5c206c4, ae21041]
related_docs:
  - 2026-05-02-section-web-frontend.md
  - 2026-05-02-checkpoint-web-batch-1.md
  - 2026-05-02-checkpoint-web-batch-2.md
  - 2026-05-02-checkpoint-web-batch-3.md
  - ../roadmap/web-frontend-task-plan.md
---

# Web Frontend — Mid-Section Checkpoint #4 (2026-05-02)

> Cadence checkpoint after 2 substantial commits — Section C (Write paths)
> closed with WEB-012, then a polish bundle that closes the **last** items
> of the 2026-05-02 baseline audit. **All 16 / 16 baseline findings now
> resolved.**

## 1. Batch under review

| # | Commit  | Task    | Description |
|---|---------|---------|-------------|
| 1 | 5c206c4 | WEB-012 | HTMX inline section patch + optimistic concurrency. 3 endpoints: `/edit`, `/view`, `POST .../sections/{anchor}`. Section wrapper id changed to `section-{anchor}` (HTMX swap target) with inner `<a id="{anchor}">` to preserve sidebar nav. Public helper `revision_service.head_for_entity`. |
| 2 | ae21041 | Polish bundle (WEB-052 + WEB-053 + WEB-053b + WEB-014b) | (52) `test_project_show_handles_missing_master` — closes SW-LO-3. (53) `migrate_db` fixture in conftest, 8 dup helpers removed — closes SW-LO-5. (53b) `EXPECTED_LIVE_TABS` / `EXPECTED_DISABLED_TABS` constants in conftest — flipping a tab is a one-tuple change. (14b) task-complete redirect respects Referer. |

Net code: +900 prod LOC, +500 test LOC since checkpoint #3.
Suite: 500 → 512 (+12); web-tests: 125 → 137 (+12).
Endpoints shipped: 10/14 → 13/14 (~93 %; only `GET /run` for SSE remains).

## 2. Sweep — original audit findings

Cumulative against the 16 findings in `2026-05-02-section-web-frontend.md`:

| Code | Title | Status |
|---|---|---|
| SW-HI-1..4 | (4 high) | ✅ closed batch-1 |
| SW-ME-1..7 | (7 medium) | ✅ closed (5 batch-1, 2 audit-prep, SW-ME-3 + SW-ME-7 batch-2) |
| SW-LO-1 | htmx versioning | ✅ closed batch-3 (WEB-051) |
| **SW-LO-2** | **Error-branch coverage gaps** | **✅ closed batch-4 (WEB-052)** |
| **SW-LO-3** | **No test for missing master_path** | **✅ closed batch-4 (WEB-052)** |
| SW-LO-4 | audit C still active | ✅ closed batch-1 |
| **SW-LO-5** | **`_alembic_upgrade` duplicated** | **✅ closed batch-4 (WEB-053)** |

**Cumulative tally: 16 of 16 baseline findings closed. 🎯**

## 3. New surfaces / risks introduced by batch-4

### 3.1 Section anchor wrapper change is a small breaking convention

WEB-012 changed `<section id="{anchor}">` → `<section id="section-{anchor}">`
plus inner `<a id="{anchor}">`. The anchor scroll behaviour is preserved
(verified in `test_doc_show_renders_sections_with_anchor_ids`), but anyone
reading the rendered HTML and pattern-matching on the old selector will
miss it.

Action: documented in WEB-012 ticket; capability §4 unchanged because the
template structure was always implementation detail. No further action.

### 3.2 task-complete `Referer`-based redirect can be spoofed

`Referer` is client-controlled. Worst case: a malicious page could craft a
`Referer` to drive the user to a third-party URL after a successful POST.
For our local-only deployment this is acceptable; for any deployed
instance this is a real concern.

Action: track for the eventual auth/CSRF pass (out of scope per capability
§1.3). Mitigation in `web_error_handler` (where Referer is also used) has
the same caveat — the whole web layer assumes single-origin trust.

### 3.3 `migrate_db` fixture spawns alembic per test

The new `migrate_db` fixture in conftest is identical in cost to the
previous duplicates — each invocation forks an `alembic upgrade head`
subprocess (~1.5s overhead per fixture instance). Fixtures that use it
get this overhead. Multiple tests in the same file each pay it (function-
scoped fixture).

Optimisation idea (out of scope): make migrate_db session-scoped, build a
golden DB once, copy with shutil for each test. ~15-20s shave on a full
api-suite run. Not done because:
- The full suite is fast enough.
- session-scoped pytest fixtures become tricky when the autouse
  `_isolated_engine_cache` and `isolated_cod_doc_home` are function-scoped.

Action: leave as-is until someone complains about CI time.

## 4. Health metrics

| Metric | After batch-3 | After batch-4 | Δ |
|---|---:|---:|---:|
| Web endpoints shipped | 10 / 14 | 13 / 14 | +3 (`/edit`, `/view`, `POST sections`) |
| Web LOC | ~2300 | ~2700 | +17 % |
| Web tests | 125 | 137 | +10 % |
| Suite total | 500 | 512 | +2.4 % |
| Suite runtime (full) | 104 s | 126 s | +21 % |
| Suite runtime (web only) | 30 s | 33 s | +10 % |
| Ruff on touched files | clean | clean | — |
| Mypy on touched files | clean | clean | — |
| `cod_doc.infra.*` imports under `cod_doc/api/web/` | 0 | 0 | — |
| **Closed baseline findings** | 14 / 16 | **16 / 16 ✅** | +2 |
| Disabled tabs | 1 | 1 | — |

## 5. Items to address before continuing

| Priority | Item | Why |
|---|---|---|
| (none) | — | All baseline-audit items closed; no new blockers. |

**Recommended next:**
- **WEB-030** (SSE run console) — last endpoint, last disabled tab, last
  big architectural piece. Will need a small investigation into how to
  attach SSE without disrupting the existing `/api/run/*` WebSocket
  flow. Reasonable to plan but defer to next session.
- **WEB-042** / **WEB-050** — small docs/refactor items. WEB-042 is a
  doc-sync helper; WEB-050 is a DI-pattern doc note in capability §7.
  Either can be batched together as a low-pri cleanup.

My pick: defer WEB-030 to a fresh session; close WEB-042 + WEB-050 as a
mini-bundle in the meantime if there's bandwidth, otherwise wrap.

## 6. Decisions deferred

- ADR for `mermaid.min.js` vendoring — defer until requested.
- ADR for `markdown-it-py` (WEB-006 follow-up) — defer.
- Auth / CSRF pass — out of scope per capability §1.3.
- migrate_db session-scoped optimisation — defer until CI cost matters.

## 7. Audit close-out

The 2026-05-02 baseline audit (`2026-05-02-section-web-frontend.md`) can
now be flipped to `resolved` — all 16 findings closed across batches
1..4. Hand-off:
- 19 of the 26 numbered tickets are done
  (Section A 3/3, B 6/6, C 3/3, F-tail polish 5/5; 2 of 6 Section F-core).
- Section D (Live ops, WEB-030 + WEB-031) is the only remaining work for
  endpoint coverage.
- Section E remainder (WEB-042) and Section F core (WEB-050) are pure
  documentation/lint hygiene.

## 8. Changelog

| Дата | Событие |
|---|---|
| 2026-05-02 | Checkpoint #4 после 2 коммитов batch-4 (5c206c4, ae21041). **16 / 16 baseline findings closed.** Section C closed (3/3). Suite 500 → 512. Endpoints shipped 10/14 → 13/14 (~93 %). 0 регрессий, baseline-аудит готов к переводу в `resolved`. |
