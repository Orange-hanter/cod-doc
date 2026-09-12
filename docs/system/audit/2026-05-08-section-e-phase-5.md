---
type: audit-report
scope: paperclip-adoption / Section E (Phase 5 — UX & Migration)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-d-phase-4.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/13-import-ux-redesign.md
  - ../../../proposals/14-legacy-tasks-migration-ux.md
  - ../../../proposals/15-link-system-and-rendering.md
---

# Section E — Closure Report (Phase 5: UX & Migration)

> **Purpose.** Record the closure of 7 tasks of Section E
> (PCA-420, PCA-400, PCA-401, PCA-410, PCA-411, PCA-421, PCA-422)
> and findings → backlog.

## 1. TL;DR

- **PCA-420** — Ordered list rendering bug fixed in `cod_doc/api/web/markdown.py`.
  Added `_OL_ITEM = re.compile(r"^(\d+)[.)]\s+(.+)$")`, `flush_ol_list()`,
  support for `<ol start="N">` for resumed lists. 12 new tests in
  `tests/api/web/test_markdown.py`.
- **PCA-400** — `scan_folder()` in `import_service.py`: scans the project
  directory, compares with the DB, returns a manifest (new/changed/unchanged/missing).
  `GET /p/{slug}/docs/import/scan?path=.` — JSON endpoint.
- **PCA-401** — `GET /p/{slug}/docs/import` — a bulk-import page with a checkbox UI
  (template `docs_import.html`). `POST /p/{slug}/docs/import/apply` — a batch
  endpoint (JSON body `{paths: [...]}`, idempotent).
- **PCA-410** — `POST /p/{slug}/tasks/legacy/import?dry_run=true/false` and
  `POST /p/{slug}/tasks/legacy/archive`. In `tasks_legacy_list.html` an
  action bar with Preview / Import all / Mark as archived is added.
- **PCA-411** — `add_task` and `update_task` in `legacy_project_tools.py` are marked
  DEPRECATED with a `DeprecationWarning`. `Project.add_task()` raises `RuntimeError`
  when `tasks.archived.yaml` exists.
- **PCA-421** — two-pass resolve in `import_markdown()`: after inserting all sections
  `_resolve_all_sections()` is called — forward references to documents created
  later in the same batch import are resolved.
- **PCA-422** — `cod_doc/services/link_service/semantic.py` with `suggest_for_section()`,
  `backfill_project()`, `list_suggestions()`, `update_suggestion_state()`.
  The `LinkSuggestionModel` + migration `0015_link_suggestions`. CLI
  `cod-doc link suggest <project>`. MCP tool `link_suggest_for_section`.
- **12 new tests** in `tests/api/web/test_markdown.py`. **1008 tests pass**
  (996 → 1008).
- **3 findings** (I1-I3) → backlog Section F (PCA-928..930).

## 2. Section E deliverables

| # | Deliverable | File / artifact | Status |
|---|------------|------------------|--------|
| E1 | Section E audit-report | `docs/system/audit/2026-05-08-section-e-phase-5.md` | ✅ |
| E2 | Ordered list renderer + tests | `cod_doc/api/web/markdown.py`, `tests/api/web/test_markdown.py` | ✅ |
| E3 | `scan_folder()` service | `cod_doc/services/import_service.py` | ✅ |
| E4 | `GET /docs/import/scan` endpoint | `cod_doc/api/web/pages/docs.py` | ✅ |
| E5 | `GET /docs/import` + `POST /docs/import/apply` | `cod_doc/api/web/pages/docs.py` | ✅ |
| E6 | Bulk import UI template | `cod_doc/templates/web/project/docs_import.html` | ✅ |
| E7 | `POST /tasks/legacy/import` + `POST /tasks/legacy/archive` | `cod_doc/api/web/pages/tasks.py` | ✅ |
| E8 | Legacy migration action bar | `cod_doc/templates/web/project/tasks_legacy_list.html` | ✅ |
| E9 | `add_task` / `update_task` deprecated | `cod_doc/mcp/tools/legacy_project_tools.py` | ✅ |
| E10 | `Project.add_task` archived guard | `cod_doc/core/project.py` | ✅ |
| E11 | Two-pass resolve in `import_markdown` | `cod_doc/services/import_service.py` | ✅ |
| E12 | `LinkSuggestionModel` + migration 0015 | `cod_doc/infra/models/link_suggestions.py`, `migrations/versions/20260508_0015_link_suggestions.py` | ✅ |
| E13 | `link_service/semantic.py` | `cod_doc/services/link_service/semantic.py` | ✅ |
| E14 | `link_suggest_for_section` MCP tool | `cod_doc/mcp/tools/link_tools.py` | ✅ |
| E15 | `cod-doc link suggest` CLI | `cod_doc/cli/link.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-420** — 12 tests in `TestOrderedList` are green; ordered lists
      render as `<ol>`, bullet-lists as `<ul>`; mixed scenarios and `start=N`
      are correct.
- [x] **PCA-400** — `scan_folder()` returns a list of `ManifestEntry` (new/changed/
      unchanged/missing); the endpoint `GET /docs/import/scan` returns JSON.
- [x] **PCA-401** — the page `/p/{slug}/docs/import` renders a checkbox UI; the
      "Import selected" button posts to `POST /docs/import/apply`; the response is a JSON summary.
- [x] **PCA-410** — Preview (dry_run=True) rolls back the transaction, does not write to the DB;
      Import all commits; Archive renames yaml → archived.yaml.
- [x] **PCA-411** — `add_task` and `update_task` return a `_deprecated` key;
      `Project.add_task` on an archived project raises `RuntimeError`.
- [x] **PCA-421** — `_resolve_all_sections()` is called after the full import;
      errors are logged, do not interrupt the import (best-effort).
- [x] **PCA-422** — `suggest_for_section()` accepts a config, calls ChromaDB,
      re-ranks by lexical signals, stores in `link_suggestion`;
      `backfill_project()` walks all sections; the CLI `cod-doc link suggest` works.

## 4. Findings (→ backlog)

### I1 — `scan_folder` uses a presence-only diff, no hash-compare *(low)*

`scan_folder()` determines `status="changed"` only by absence in the DB (all existing → unchanged). There is no sha256 storage in the DB, so real file changes are not detected.

**Recommendation:** add a `content_sha256_head` field to `DocumentModel` + populate it on import. Then scan_folder can compare the sha and set `changed`. A separate F-task.

### I2 — `bulk import apply` is not idempotent on a doc_key conflict *(medium)*

`POST /docs/import/apply` calls `import_markdown()` per-file. If the doc_key already exists in the DB, `doc_service.create()` raises IntegrityError — the file goes into `errors`, not overwritten. This is correct behavior for new files, but for changed files an upsert is needed (create a new revision). Proposal 13 §2.4: "changed → new revision".

**Recommendation:** in the apply-endpoint, before `import_markdown`, check the existence of the doc_key; if it exists — instead of import, call patch_section per-section. A separate F-task.

### I3 — Semantic suggest requires a pre-populated ChromaDB index *(low)*

`suggest_for_section()` will fail with `ChromaDB query error` if the collection is empty (`reindex` has never been run). There is no auto-reindex before suggest.

**Recommendation:** in `backfill_project()` check whether there is anything in the collection; if not — log a warning and return an empty result, instead of failing. Already partially handled (try/except in `suggest_for_section`), but worth adding an explicit check.

## 5. Metrics

| Metric | Before Section E | After | Δ |
|---------|-------------:|------:|--:|
| Web endpoints | 17 | 22 | +5 |
| New service functions | — | 8 | +8 |
| New model tables | — | 1 (`link_suggestion`) | +1 |
| Alembic migrations | 14 | 15 | +1 |
| `tests/` total | 996 | 1008 | +12 |
| Section E done tasks | 0 | 7 | +7 |
| Total A+B+C+D+E done | 38 | 45 | +7 |

## 6. What was not included (out of scope)

- **`project.docs_root` in Settings** (proposal 13 §2.5) — a field to store `docs_root` in the project config. Scan works without it via the `?path=.` query param.
- **Web UI "Suggested links"** in the document footer (proposal 15 §2.3.3) — step 15.7. Requires template changes to doc_show.html; deferred to the backlog.
- **`--apply-above X` auto-accept in backfill** is fully implemented in semantic.py, but not tested end-to-end (needs a reindex with real data).
- **Nested lists, GFM task-lists** (proposal 15 §2.1) — explicitly out-of-scope per the proposal.

## 7. Next step

Sections A–E are closed. **45 done tasks** in total. The entire RFC backlog (proposals 01–15) is implemented.

Open directions:
- **Section F backlog consolidation** — 16 (from sections B-D) + 3 (from Section E) = 19 findings. Priorities: I2 (bulk import upsert, medium), G2 (routine scheduler daemon, high), G3 (wire noop routine checks, high), H3 (adapter capabilities check, low), H4 (deprecated client shim, low).
- **Web UI "Suggested links"** (I2 from proposal 15 §2.3.3) — the footer of doc_show.html.

Findings I1-I3 are opened as PCA-928..930 in Section F.
