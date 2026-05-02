---
type: checkpoint-report
scope: cod_doc/api/web/* + cod_doc/core/project.py (Section F first batch)
status: resolved
source_of_truth: false
canonical_source: docs/system/audit/2026-05-02-section-web-frontend.md
owner: cod-doc core
created: 2026-05-02
last_updated: 2026-05-02
covers_commits: [10689ac, c1008f6, 9c72a13, f8b007a, 517fc02]
related_docs:
  - 2026-05-02-section-web-frontend.md
  - ../roadmap/web-frontend-task-plan.md
  - ../capabilities/web-frontend.md
---

# Web Frontend — Mid-Section Checkpoint #1 (2026-05-02)

> Cadence checkpoint after the first 5 commits of Section F (Hardening) +
> follow-on into Section E. Triggered by the user's rule «каждые 3-6 коммита
> — аудит». Goal: confirm the batch is healthy, log new surfaces, decide
> what (if anything) must be addressed before continuing with the remaining
> WEB-022 → WEB-006 → WEB-014 chain.
>
> This is a **checkpoint**, not a full section-close audit. The canonical
> baseline is `2026-05-02-section-web-frontend.md`; this report cross-links
> back to its severity codes (`SW-HI-N`, `SW-ME-N`, `SW-LO-N`).

## 1. Batch under review

| # | Commit  | Task    | Description |
|---|---------|---------|-------------|
| 1 | 10689ac | WEB-005 | Per-project DB engine cache + `get_project_db` / `try_open_project_db` DI helpers in `cod_doc.api.deps`. |
| 2 | c1008f6 | WEB-040 | Delete `cod_doc/api/web/db_resolver.py`; lock web→services boundary via AST-tested import banlist. |
| 3 | 9c72a13 | WEB-022 | `WebError` exception family + handler; HTMX `hx-swap-oob` alerts to `#alerts`; cookie-flash for form-post; inline OOB alert on conflict. |
| 4 | f8b007a | WEB-041 | `_layout/project_tabs.html` include with disabled-tab support; `task_status_options` Jinja global. |
| 5 | 517fc02 | WEB-013 | `Project.batch_stats` (ThreadPoolExecutor) + `?limit`/`?offset` pagination on `/`. |

Net code: +700 prod LOC, +1100 test LOC. Suite 418 → 441 (+23), of which
web-tests: 27 → 66 (+39).

## 2. Sweep — original audit findings

Cross-reference against the 16 findings in `2026-05-02-section-web-frontend.md`:

| Code | Title | Status after batch |
|---|---|---|
| SW-HI-1 | Web → infra direct import (db_resolver) | ✅ closed (WEB-040) |
| SW-HI-2 | Engine per request | ✅ closed (WEB-005) |
| SW-HI-3 | Index N+1 (`Project.stats()` per project) | ✅ closed (WEB-013) |
| SW-HI-4 | No alert/error model | ✅ closed (WEB-022) |
| SW-ME-1 | Tabs duplicated in 4 templates | ✅ closed (WEB-041) |
| SW-ME-2 | Tabs to 404 for unimplemented routes | ✅ closed (WEB-041) |
| SW-ME-3 | doc_show body raw markdown, broken anchors | open → WEB-006 |
| SW-ME-4 | Capability §3 status drift | ✅ closed (audit-prep, 2026-05-02) |
| SW-ME-5 | §4 skeletal templates absent | ✅ closed (audit-prep, 2026-05-02) |
| SW-ME-6 | `status_options` duplicated | ✅ closed (WEB-041, Jinja global) |
| SW-ME-7 | No agg dashboard on overview | open → WEB-014 |
| SW-LO-1 | htmx.min.js without version-fingerprint | open → WEB-051 |
| SW-LO-2 | Error-branch coverage gaps | partial — WEB-022 added 7 tests; SW-052 still queued |
| SW-LO-3 | No test for missing master_path | open → WEB-052 |
| SW-LO-4 | audit `2026-04-28-section-c-capabilities` still active | ✅ closed (flipped to `resolved` in WEB-040) |
| SW-LO-5 | `_alembic_upgrade` duplicated in two test files | open → WEB-053 |

**Tally:** 11 of 16 findings closed in batch. Remaining 5 are explicitly
queued tasks (WEB-006, 014, 051, 052, 053) — none blocking.

## 3. New surfaces / risks introduced by this batch

### 3.1 Process-wide module state in `cod_doc.api.deps`

`_ENGINE_CACHE: dict[Path, _CachedEngine]` is module state. **Risk:** tests
that don't dispose between runs leak engine references to deleted `tmp_path`
directories. Memory and FD pressure isn't dangerous in CI (each Engine is
small) but it's not hygiene.

`tests/api/test_deps_engine_cache.py` and `tests/api/test_web_alerts.py`
have an autouse `_clean_engine_cache` fixture; the older
`test_web_docs.py` / `test_web_tasks.py` / `test_web_scaffold.py` /
`test_web_tabs.py` / `test_web_index_pagination.py` do **not**.

Action: hoist the dispose autouse fixture into `tests/api/conftest.py` so
every API test starts and ends with an empty cache. Folded into **WEB-053**
(test fixture hygiene; raise to `medium` priority — was `low`).

### 3.2 flash_message cookie has no length cap

`server.py:web_error_handler` and `fragments.py:task_status_update` set
`flash_message` to the raw `WebError.message` (or service exception text),
percent-encoded. If a service throws a multi-line traceback or a long SQL
diagnostic, the cookie can blow past the ~4 KB browser limit.

Action: truncate to ≤512 chars with ellipsis at write time, in a single
helper. New low-priority task **WEB-054** (cookie-flash truncation).

### 3.3 4xx WebErrors are silent in logs

The exception handler renders a 4xx but doesn't `logger.info(...)` the
event. For local ops it's fine; for any deployed instance, repeated
NotFoundWebError or ConflictWebError events should be visible in stdout.

Action: add `logger.info("WebError: %s %d", request.url.path,
exc.status_code)` to the handler. Folded into **WEB-022b** (small follow-up,
low-priority).

### 3.4 Page-summary cosmetic: out-of-range offset shows "N+1–N of N"

When `offset >= total`, the template summary reads e.g. "11–10 of 10".
Numbers are technically correct (`from = offset+1`, `to = offset+0`) but
they read as a glitch. The Prev link is live so users can recover, but
visually it's noisy.

Action: clamp `showing_from` and `showing_to` to `[0, total]` when the page
is empty. **WEB-013b** trivial fix; can pick up next visit. Not severe
enough to gate progress.

### 3.5 No CSRF protection on POST endpoints

Pre-existing condition (capability §1.3 says auth/CSRF is deferred).
This batch introduced more POST surface (`task_status_update` was already
there; the alert flow now uses cookies, which makes CSRF more relevant if
the app ever leaves localhost). Not a regression — explicit non-goal.

Document this loud-and-clear in capability §1 before any deploy. Tracked,
no immediate action.

## 4. Health metrics

| Metric | Before | After | Δ |
|---|---:|---:|---:|
| Web endpoints shipped | 5 / 14 | 5 / 14 | — (focus was hardening) |
| Web LOC (api/web + templates + static) | 854 | ≈1100 | +29 % |
| Web tests | 27 | 66 | **+144 %** |
| Suite total | 418 | 441 | +5.5 % |
| Suite runtime (full) | 95 s | 108 s | +14 % |
| Suite runtime (web only) | 11 s | 18 s | +63 % |
| Ruff on touched files | clean | clean | — |
| Mypy on touched files | clean | clean | — |
| `cod_doc.infra.*` imports under `cod_doc/api/web/` | 2 | **0** | -100 % |
| Engines created per `GET /p/{slug}/...` request | 1 | ≤1 / TTL | (cache hit ≫ miss) |

## 5. Items to address before continuing

| Priority | Item | Why now |
|---|---|---|
| **medium** | Hoist `_clean_engine_cache` to `tests/api/conftest.py` | Prevents engine-leak contamination as more tests join the suite. Cheap (10 LOC). Folded into WEB-053 (raise priority `low → medium`). |
| **low** | `WEB-013b`: clamp empty-page summary numerals | Cosmetic; can wait. |
| **low** | `WEB-022b`: log WebError events | Operational hygiene, not user-facing. |
| **low** | `WEB-054`: cookie-flash truncation | Edge case; only matters for very long messages. |

None of these block the next batch. **Recommended next batch focus:**
WEB-006 (markdown rendering for doc_show — closes SW-ME-3) or WEB-014
(overview agg — closes SW-ME-7). WEB-006 is more visible to users and
rounds out Section B reads; WEB-014 unlocks plan-progress visibility on
the dashboard. Either is fine; my preference is WEB-006 since it removes
a daily-friction issue (anchors don't work).

## 6. Decisions deferred

- **`markdown-it-py` vs custom mini-renderer for WEB-006.** Add a one-line
  ADR in `capabilities/decisions-and-questions.md` when WEB-006 starts.
  Lean: custom renderer (Section structure already known by `DocService`,
  no new dep, ~120 LOC).
- **`hx-trigger="load"` lazy stats for `/`** — explicitly deferred; current
  pagination + threadpool covers proj-counts up to ~hundreds. Reopen when
  someone reports >100-project lag.
- **CSRF / auth model** — NOT this batch's concern; surfaces only when web
  ships outside localhost.

## 7. Changelog

| Дата | Событие |
|---|---|
| 2026-05-02 | Checkpoint после 5 коммитов batch-1 Section F+E (commits 10689ac → 517fc02). 11 / 16 находок baseline-аудита закрыты; 4 новых внутренних item (WEB-013b/022b/054 + bump WEB-053 → medium); 0 регрессий, suite 418 → 441. |
