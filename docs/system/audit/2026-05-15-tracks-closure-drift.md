---
date: 2026-05-15
scope: cycle-3 (G) + cycle-5 (H) + adr-system + observability-and-indexing
kind: module-audit
auditor: human:dakh + Claude
status: open  # findings pending remediation
---

# Module audit — 4 tracks closed 2026-05-15

> 5-dimensional drift check per skill `module-audit`. CI зелёный ≠ модуль
> готов. Audit applied to four section closures done today:
>
> 1. paperclip-adoption-task-plan/G — Cycle-3 agent-UX audit (19 done / 2 cancelled)
> 2. paperclip-adoption-task-plan/H — Cycle-5 agent-centric API (12 done)
> 3. adr-system-task-plan/A,B,C — ADR domain + UI + migration (8 done)
> 4. observability-and-indexing-task-plan/A..E — Metrics/Commits/Code-refs/RepoIndex/FTS5 (8 done)
>
> Total: **47 done + 2 cancelled** in one session.

## Findings

### Code drift

**F1 [code] · severity: C** — Web layer imports from infra directly.

`cod_doc/api/web/pages/code_refs.py` imports `cod_doc.infra.models.LinkModel`
to run the listing query. This violates the architectural invariant
enforced by `tests/api/test_web_layer_imports.py` (two assertions fail
on `main`):

```
test_web_layer_does_not_import_infra
test_web_layer_only_imports_from_cod_doc_allowed_set
```

**Fix:** Move the listing into `link_service.list_code_refs(session, project_id)`
and import that from `code_refs.py` instead.

**F9 [code] · severity: M** — `pathspec` dependency undeclared.

`cod_doc/services/repo_index_service.py` does `import pathspec` but
`pyproject.toml` declares no such dependency. It happens to be installed
in the venv as a transitive of another package, but a fresh `pip install`
on a clean environment would fail. The Docker image builds today only
because the layer cache still contains the indirect install.

**Fix:** Add `"pathspec>=0.12"` to `dependencies` in `pyproject.toml`.

### Logic drift

**F2 [logic] · severity: M** — `agent_pick` returns done tasks with stale locks.

`agent_service.pick`'s idempotency branch filters tasks by
`checked_out_by == agent_id` only — no status filter. When a task is
closed via a code path that doesn't release the lock (e.g. raw SQL
update from a migration script — exactly what I did to close PCA-9XX
during cycle-3 closure), the lock survives and the next `agent_pick`
call replays the done task.

Surfaced in actual usage today: the first `agent_pick(cod-doc, human:dakh)`
returned `PCA-940 [status=done]` with `idempotent_replay: true`.

**Fix:** Skip rows where `status in ('done', 'cancelled')` in the
idempotency lookup. Optionally also auto-release the dangling lock.

**F3 [logic] · severity: L** — `agent_complete` swallows release exception silently.

```python
try:
    checkout_service.release(session, task_id, agent=agent_id, force=False)
except Exception:
    pass  # not fatal
```

When the caller's `agent_id` differs from the holder of the lock (e.g.
admin closes someone else's checked-out task), release fails silently.
Task becomes `done` but lock leaks.

**Fix:** Log a warning (use `_log` not bare `pass`); document that the
caller should release explicitly first, or pass `force=True`.

**F10 [logic] · severity: L** — FTS5 / commit / repo indexes are *manual* — no event-driven sync.

`search_service.reindex_all`, `commit_link_service.import_from_git_log`,
`repo_index_service.scan_project` only run on explicit user trigger
(CLI / POST /reindex). As soon as a user creates a new task or
re-edits a doc, the indexes drift until the next manual rebuild.

This is a **design choice**, not a bug — was acceptable for cycle-5
scope. Documented for follow-up cycle if event-driven sync becomes
necessary.

**Fix (deferred):** Add hooks in `task_service.create` /
`doc_service.create` / `complete` etc. to incrementally update the FTS
index (and commit_link / repo_index can stay manual since they require
external state).

### Style drift

**F4 [style] · severity: M** — 124 ruff errors in `cod_doc/`, 308 in `tests/`.

```
F401  unused-import       — 18 in cod_doc/, ~50 in tests/
TC003 typing-only-import   — 5
UP037 quoted-annotations   — 16 (mostly auto-fixable)
F821  undefined-name       — 2 in cod_doc/api/web/pages/stories.py
SIM105 try/except/pass     — 4
B008  function-call-default — 1
RUF012 mutable-class-default — 1
... and more
```

Pre-existing technical debt amplified by ~10 new files added today
without `ruff check --fix` pass. Two F821 (undefined-name) in
`stories.py` (lines 39, 47) are pre-existing string-forward-refs that
should be guarded by `from __future__ import annotations`.

**Fix:** `ruff check --fix cod_doc/ tests/ && ruff format cod_doc/ tests/`
in a chore commit. F821 needs manual: add `from __future__ import annotations`
to `stories.py`.

**F5 [style] · severity: L** — 165 files would be reformatted by `ruff format`.

Includes today's new files. Format-on-save not enforced.

**Fix:** Run `ruff format cod_doc/ tests/` once + add pre-commit hook.

### Test drift

**F6 [test] · severity: L** — 5 flaky tests in `tests/test_adapters.py`.

Symptoms:
- Pass in isolation (`pytest tests/test_adapters.py` → 28/28).
- Fail in multi-suite (`pytest tests/`) — RuntimeError "There is no current event loop".

Root cause: the fixture in `test_adapters.py:31` uses
`asyncio.get_event_loop().run_until_complete(...)` (deprecated API);
when other tests leak event-loop state, this fixture sees a closed loop.

Pre-existing — not introduced by today's work. Logged here so future
audits don't keep rediscovering it.

**Fix:** Replace the fixture with `asyncio.new_event_loop()` + explicit
`set_event_loop` + cleanup. Or use `pytest-asyncio` `@pytest.mark.anyio`.

### Documentation drift

**F8 [docs] · severity: M** — No audit-reports for any section closures today.

Memory `audit_cadence.md`: "закрытая секция → audit-report в
`docs/system/audit/`". Closed today:

| Section | Audit-report? |
|---|---|
| paperclip-adoption-task-plan/G (cycle-3) | ❌ missing |
| paperclip-adoption-task-plan/H (cycle-5) | ❌ missing |
| adr-system-task-plan A+B+C (whole plan)  | ❌ missing |
| observability-and-indexing A..E (whole plan) | ❌ missing |

This audit-report itself partly closes the gap (covers all four at
once), but a per-track 5-dim audit would have been more rigorous.

**Fix:** This document. Going forward, write a per-track audit before
closing the last task of a section.

**F7 [docs] · severity: L** — `arch/architecture.md` §5 retains 5 ADRs already in DB.

After `adr_migrator.migrate_from_file` populated DB rows for ADR-001..005,
the markdown source remained intact. Now there are two sources of truth.

**Fix:** Either (a) replace §5 with a redirect note pointing to
`/p/cod-doc/adr`, or (b) keep markdown as the human-readable view and
add a `synced_from_db: true` frontmatter flag.

**F12 [docs] · severity: M** — `docs/HANDBOOK.md` lacks promised ADR section.

ADR-008 acceptance: "end-to-end ADR flow + **HANDBOOK section**". The
e2e tests landed; the HANDBOOK addition didn't. `grep -i adr
docs/HANDBOOK.md` returns nothing.

**Fix:** Add a top-level "## Architecture Decision Records" section to
`docs/HANDBOOK.md` covering: when to write an ADR, lifecycle
(proposed → accepted → superseded), CLI/web entry points,
`adr_migrator` for legacy import.

**F11 [docs] · severity: L** — Project has 14 web tabs after this push.

`project_tabs.html` now lists: Overview / Agent / Docs / Tasks /
Stories / Plans / ADRs / Metrics / Commits / Code refs / Search /
Routines / Costs / Revisions. Tab strip wraps on narrow screens;
information-architecture review needed.

**Fix (deferred):** Group tabs into dropdowns or move
observability/indexing items behind a single "Insights" parent tab.

## Summary

| Измерение | F-count | Severity (C/M/L) |
|-----------|---------|------------------|
| code      | 2 | C: 1 · M: 1 |
| logic     | 3 | M: 1 · L: 2 |
| style     | 2 | M: 1 · L: 1 |
| test      | 1 | L: 1 |
| docs      | 4 | M: 2 · L: 2 |
| **total** | **12** | C: 1 · M: 5 · L: 6 |

## Remediation plan

Per skill `module-audit`: ≥1 finding ⇒ open a remediation plan
(`plan_create`).

**Recommended plan:** `cycle-5-drift-remediation-2026-05-15`,
principle "Закрыть все F# из 2026-05-15 audit-report до перехода в
cycle-6".

Critical (block any next major release):

- F1 — fix `code_refs.py` infra import. The architecture-invariant
  test must be green before merging anything else.

Medium (do before cycle-6 starts):

- F2 — `agent_pick` filter out done tasks from stale-lock branch.
- F4 — run `ruff --fix` + `ruff format`; fix F821 with `__future__`.
- F8 — write per-track audit-reports retroactively, or adopt this one.
- F9 — declare `pathspec` in `pyproject.toml`.
- F12 — add ADR section to HANDBOOK.

Low (housekeeping):

- F3, F5, F6, F7, F10, F11 — do when convenient; non-blocking.

## What was checked (so audit ≠ pencil-whip)

- Code: `ruff check cod_doc/ tests/` (308+ errors enumerated),
  architecture-invariant tests (`test_web_layer_imports`),
  pyproject.toml deps vs actual imports.
- Logic: walked through `agent_service.pick` idempotency branch by
  hand; reproduced stale-lock replay in this session
  (PCA-940 case); reviewed acceptance criteria of OBI-020/030/040 vs
  shipped tests.
- Style: `ruff check --statistics` + `ruff format --check`.
- Tests: full project test suite (1217 tests, 7 failed — 5 flaky
  asyncio + 2 architecture violation from F1).
- Docs: `find docs/system/audit/` + grep through HANDBOOK + verified
  `arch/architecture.md §5` survives DB migration.
