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

> **Purpose.** Entry point for the next session of work on the web section.
> Contains: context, state, first tick, readiness criteria, commands.
>
> **Not source of truth.** Canonical documents — the capability and roadmap
> (see `canonical_source` in the frontmatter). This file lives until Section F
> closes, after which it is archived.

## 1. TL;DR

- Web section: **5/14 endpoints** implemented (WEB-001..003, WEB-010, WEB-011).
  Section A (Scaffold) is closed.
- 2026-05-02 audit run → 13 new tasks, 2 raised to high.
- **The root of the problems is the missing foundation**: an engine per
  request (perf), web → infra direct imports (architecture), `<div id="alerts">`
  without a model (UX).
- **Batch-1 closed 2026-05-02:** WEB-005, WEB-040, WEB-022, WEB-041, WEB-013 ✅.
  11 / 16 baseline findings closed; checkpoint #1 → [batch-1](../audit/2026-05-02-checkpoint-web-batch-1.md).
- **Batch-2 closed 2026-05-02:** WEB-006 (markdown), polish (013b/022b/054),
  WEB-014 (overview agg + complete), WEB-021 (revisions log) ✅.
  13 / 16 baseline findings closed; suite 441 → 483; endpoints 5→8/14;
  checkpoint #2 → [batch-2](../audit/2026-05-02-checkpoint-web-batch-2.md).
- **Batch-3 closed 2026-05-02:** WEB-004 (plan view), WEB-060 (settings),
  WEB-051 (asset versioning) ✅. **Section B closed (6/6).**
  14 / 16 baseline findings closed; suite 483 → 500; endpoints 8→10/14;
  checkpoint #3 → [batch-3](../audit/2026-05-02-checkpoint-web-batch-3.md).
- **Batch-4 closed 2026-05-02:** WEB-012 (section patch), polish bundle
  (WEB-052/053/053b/014b) ✅. **Section C closed (3/3).**
  **16 / 16 baseline findings closed — audit `2026-05-02-section-web-frontend`
  moved to `resolved`.** Suite 500 → 512; endpoints 10→13/14;
  checkpoint #4 → [batch-4](../audit/2026-05-02-checkpoint-web-batch-4.md).
- **Next step:** WEB-030 (SSE run console) — the last endpoint and the last
  disabled tab. Possibly a separate session — needs integration with
  `Orchestrator` and `hx-ext="sse"`.

## 2. Where things live

| Document | Purpose |
|---|---|
| [docs/system/audit/2026-05-02-section-web-frontend.md](../audit/2026-05-02-section-web-frontend.md) | Audit report. 16 findings, severity, links to code. |
| [docs/system/capabilities/web-frontend.md](../capabilities/web-frontend.md) | Capability (target behavior). §3 — routes with status. §7 — DI convention. §11 — current state. |
| [docs/system/roadmap/web-frontend-task-plan.md](web-frontend-task-plan.md) | Execution plan. Section A..F. Productivity backlog. Mermaid graph. |
| [cod_doc/api/web/](../../../cod_doc/api/web/) | Code: pages, fragments, db_resolver (to be removed), templates_env. |
| [cod_doc/templates/web/](../../../cod_doc/templates/web/) | Templates: base, index, project/*, _frag/. |
| [tests/api/test_web_*.py](../../../tests/api/) | 27 tests, all green. |

## 3. Implementation state (matrix)

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
| WEB-006 | Markdown render for doc_show | B | ✅ done 2026-05-02 | medium |
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

## 4. First tick — WEB-005 (Engine cache + DI helper)

**Goal.** Replace per-request `make_engine + dispose` with a cached engine
with a TTL by mtime; introduce a `get_project_db` FastAPI dependency to
remove boilerplate from handlers and set the stage for WEB-040.

**Files:**
- [cod_doc/api/deps.py](../../../cod_doc/api/deps.py) — add
  `get_engine_for_slug`, `get_project_db`, `dispose_all_engines`.
- [cod_doc/api/server.py](../../../cod_doc/api/server.py) — in the lifespan
  shutdown call `dispose_all_engines()`.
- historical `cod_doc/api/web/db_resolver.py` —
  keep for now; **in WEB-040 it is removed**. Inside, rewrite it to use the
  cache from deps (a minimal change so the tests stay green).
- `tests/api/test_deps_engine_cache.py` — **NEW**.

**Acceptance (repeated from the plan):**
1. `get_engine_for_slug(slug)` returns a cached engine; the cache is
   `dict[Path, tuple[Engine, float]]` with a TTL by mtime of the state.db file.
2. `get_project_db(slug) -> Iterator[tuple[Session, int]]` — a yield-style
   FastAPI dependency that closes the session after the response.
3. `dispose_all_engines()` is called in `app.lifespan` shutdown.
4. A perf/micro-test: 100 sequential `GET /p/{slug}/tasks` get faster thanks
   to the cache. Record the measured number (X→Y ms).
5. 3 tests: cache hit, cache invalidation by mtime, dispose-on-shutdown.
6. All 27 existing tests stay green.

**Architectural questions to ponder:**
- **mtime vs explicit invalidation.** mtime is simple, but the fs cache on
  macOS/Linux has 1-second resolution. For embedded sqlite this is fine (the
  write flow changes the file explicitly). Alternative — events. Start:
  mtime, switch if races show up.
- **TTL vs eternal cache.** Eternal cache + an mtime-check on every lookup is
  safe, but a lookup costs an fs-stat. A 5-second TTL is a compromise, after
  which a stat check. Start: TTL=5s + stat-on-stale.
- **Behavior on `OperationalError`** (schema not applied). Return `None` →
  the handler emits a graceful warning, as `db_resolver.py` does now. Do not
  crash.

## 5. Development commands

```bash
# Green suite before starting
.venv/bin/pytest tests/ -q

# Run only web tests
.venv/bin/pytest tests/api/ -q

# Run the dev server (optional, for manual checking)
.venv/bin/uvicorn cod_doc.api.server:app --reload --port 8765
# then GET http://localhost:8765/

# Lint + types
.venv/bin/ruff check cod_doc tests
.venv/bin/mypy cod_doc

# Before commit
.venv/bin/pytest tests/ -q && .venv/bin/ruff check cod_doc tests && .venv/bin/mypy cod_doc
```

## 6. Definition of Done for Section F

Section F closes when:

- [x] WEB-005 done — the engine is cached; `get_project_db` is available. (2026-05-02)
- [x] WEB-040 done — `db_resolver.py` is removed; the ruff banned-imports rule
      works; audit `2026-04-28-section-c-capabilities.md` moved to `resolved`. (2026-05-02)
- [x] WEB-022 done — `WebError` + middleware + `_frag/alert.html`;
      `<div id="alerts">` is alive. (2026-05-02)
- [x] WEB-013 done — the index loads in a single pass for N=20. (2026-05-02)
- [ ] WEB-050 done — the DI pattern is fixed in capability §7 as
      "the only correct one".
- [ ] WEB-051, WEB-052, WEB-053 done — versioning, error-branch coverage,
      conftest extract.
- [ ] WEB-013b/022b/054 (sub-tickets from the checkpoint audit) — closed.
- [ ] Suite green (>66 tests, on each task note the added ones).
- [ ] Audit report `2026-05-02-section-web-frontend.md` moved to `resolved`.
- [ ] Capability §11 "Current state" updated: endpoints shipped,
      LOC, tests; "Architectural debt" cleared of closed lines.

## 7. Known ADR questions (deferred until their time)

| When | Question | Options |
|---|---|---|
| WEB-006 | Markdown rendering | (a) `markdown-it-py` (new dep) (b) own mini-renderer over `DocService` (no deps) |
| WEB-013 | Async stats or single-pass | (a) `asyncio.gather` + cache (b) global DB aggregation (needs cross-DB) |
| WEB-022 | Alerts: cookie-flash or session | (a) Signed cookie (b) Server-side session (new dep `itsdangerous` already in FastAPI) |
| WEB-030 | SSE: in-memory pub/sub or EventBridge | (a) inmem (b) shared with CLI/MCP via event bus (new abstraction) |
| P-3 (palette) | Search backend | (a) SQL LIKE (b) FTS5 (c) chromadb (already present) |

Each ADR is a separate record in [docs/system/capabilities/decisions-and-questions.md](../capabilities/decisions-and-questions.md)
after the corresponding task starts.

## 8. Checklist "before closing any web task"

Every PR in the web section is checked for:

- [ ] Only `cod_doc.services.*`, `cod_doc.api.deps`, `cod_doc.domain.entities` (enums)
      are imported into `cod_doc/api/web/`. No `infra.*`.
- [ ] The HTMX fragment has a `<form>` fallback without JS (POST/Redirect/GET).
- [ ] Error-branch coverage: validation/conflict/integrity/domain — all 4 branches
      have tests.
- [ ] Capability §3 (route table) updated: status `✅` + link to the task id.
- [ ] Capability §11 (metrics, current state) updated if the numbers changed.
- [ ] Audit report of 2026-05-02: if the fix closes a finding — put ✅
      next to its SW code.
- [ ] Roadmap §Progress Overview recomputed.
- [ ] Suite green, new tests named with the `WEB-XXX` prefix.

## 9. Risk register (what can break)

| Risk | Mitigation |
|---|---|
| Engine cache is not invalidated on an external `cod-doc db migrate` | an mtime-check on lookup catches this; +a test with touch state.db |
| Removing `db_resolver.py` breaks orphan imports somewhere | grep the repo in WEB-040; banned-imports rule in ruff after rm |
| `WebError` middleware conflicts with `routes.py` exception handlers | Register via `app.exception_handler(WebError)` (not middleware) — isolated scope; test both routers |
| Markdown-it-py adds ~150 KB to dependencies | See the ADR in WEB-006; the choice will be fixed before the task starts |
| SSE and the Background Daemon (run_daemon) compete for the asyncio loop | Deploy WEB-030 with an explicit task-pool; an integration test |

## 10. What we definitely do NOT do in Section F

- Do not add new routes (Section B/C are waiting).
- Do not touch the CSS design (capability §1 forbids it).
- Do not introduce dark mode, icon emojis, animations.
- Do not write the `markdown-it-py` integration (that is WEB-006).
- Do not try to "quickly" close the P-1..P-15 backlog — they are in the queue.

---

## 11. Changelog

| Date | Event |
|---|---|
| 2026-05-02 | Kickoff brief created after the web section audit. Entry point to Section F. |
