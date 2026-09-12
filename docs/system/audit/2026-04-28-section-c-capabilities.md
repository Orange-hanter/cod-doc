---
type: audit-report
scope: cod_doc/* + docs/system/* (Section C — capability layer + system docs coherence)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-04-28
last_updated: 2026-05-02
audit_target_revision: HEAD = 37c45b1 (post COD-013/014/020/022/023 + Section B/C done)
related_docs:
  - ../MASTER.md
  - ../roadmap/cod-doc-task-plan.md
  - ../roadmap/audit-followups-task-plan.md
  - 2026-04-25-section-a-data-core.md
  - 2026-04-25-section-b-services.md
  - 2026-05-02-section-web-frontend.md
---

# Section C (Capability Layer) — System Audit

> Deep audit of COD-DOC along 4 axes: conformance of code to capability documents, internal coherence of the `docs/system/` package, roadmap synchronization with reality, test and infrastructure coverage.
> The state of the core (Section A) and services (Section B) is healthy, both audits are closed as `resolved`. This report focuses on the capability layer and the surrounding shell.

## Summary

| Severity | Count | Fixed inline | New task | Deferred |
|----------|------:|-------------:|---------:|---------:|
| critical | 1 (DocService without FM validation) | 1 ✅ | — | 0 |
| high     | 4 (CI missing; sensitive-data without infra; web bypasses services; markdown-cascade) | 1 ✅ (web docs hint) | 4 (COD-024, COD-025, WEB-040, COD-014a) | 0 |
| medium   | 6 (frontmatter type/status; MASTER §2; COD-020 status; FM-007 reserved; capability/CLI gaps; TUI tests) | 5 ✅ | 1 (COD-026) | 0 |
| low      | 5 (audit-followups stale; examples in backticks as "broken"; orphan capabilities; api_navigation TBD; LinkService.rename markdown-paths) | 3 ✅ | 1 (COD-014a) | 1 (api_navigation — waits for COD-051) |
| **total** | **16** | **10 ✅** | **6 opened** | **1** |

326 tests pass (plus 3 new ones for FM-002/FM-003 in `test_doc_service.py`).

---

## 1. Critical

### SC-CR-1. ✅ DocService.create bypasses frontmatter validation

**Where:** [cod_doc/services/doc_service.py:93-137](../../../cod_doc/services/doc_service.py)

**Symptom:** COD-020 is declared as implemented, validation is wired in `TaskService.create` and `StoryService.create`, but **not in `DocService.create`**. You can create a document with `status=active` without `owner` (FM-002 violation), or with `source_of_truth=false` without `canonical_source` (FM-003 violation).

**Fix (applied in this revision):**
- Added a `_gate_frontmatter()` helper in `doc_service.py` — escalates `severity=error` issues from `validation.audit_frontmatter` to `ValidationError`. FM-004/FM-005 (freshness) remain advisory — they surface through `cod-doc audit`, not through the write-path.
- Wired into `create()`.
- 3 new tests in `tests/services/test_doc_service.py`: FM-002 reject, FM-003 reject, draft-without-owner accept.
- Existing tests that created `status=ACTIVE` without `owner` (`test_link_service`, `test_web_docs`, `test_story_service`, `_new_doc` helper) are brought into conformance with the spec.
- The COD-020 card in the roadmap is moved to `done` with an explicit note about FM-002/FM-003 escalation.

---

## 2. High

### SC-HI-1. ✅ COD-024 closed. CI workflow created

**Where:** [.github/workflows/ci.yml](../../../.github/workflows/ci.yml) (COD-024 done 2026-04-28).

**Symptom (was):** 326 tests, mypy strict, ruff were not run on PRs. Regressions were caught only locally.

**Fix:** a workflow with three jobs:
- **pytest** (matrix 3.11 + 3.12) — blocking, 329/329 green.
- **ruff** + **mypy** — `continue-on-error: true` (advisory) due to pre-existing debt: 407 ruff errors, 101 mypy errors. Raised as a new task **COD-024a** (lift advisory gates to blocking). The decision is to keep them visible-but-non-blocking for as long as needed, rather than block productive work until full cleanup.
- The concurrency-group cancels superseded runs.

### SC-HI-2. ✅ → task COD-025. Sensitive-data: only a DB field, no infrastructure

**Where:** [docs/system/standards/sensitive-data.md](../standards/sensitive-data.md) describes `SensitivityScanner`, redaction in `ProjectionService.export()`, filtering in `ContextService.get()`, the `agent.sensitivity_clearance` field.

**Symptom:** in the code there is only the `Sensitivity` enum on `Document.sensitivity`. The scanner itself, redaction, the clearance field and the filter are missing. The standard "hangs" without obligations.

**Fix:** task **COD-025** opened. In parallel, code `FM-007` is reserved in [standards/frontmatter.md §6](../standards/frontmatter.md) for the warning "missing `sensitivity` for module-spec/architecture/standard".

### SC-HI-3. ✅ → task WEB-040. Web layer bypasses the service layer

**Where:** historical `cod_doc/api/web/db_resolver.py` imported `DocumentModel` directly from `cod_doc.infra.models`; the file was deleted in WEB-040.

**Symptom:** violates the rule [capabilities/web-frontend.md §7](../capabilities/web-frontend.md): "A web page is not allowed to bypass the service".

**Fix:** task **WEB-040 (Section E: Architecture Hygiene)** opened — delete `db_resolver.py`, move `pages.py`/`fragments.py` to `cod_doc.services.*` + DI via `cod_doc.api.deps`. A linter rule (banned imports) will prevent regression.

### SC-HI-4. ✅ → task COD-014a. LinkService.rename does not cascade markdown-relative links

**Where:** [cod_doc/services/link_service.py:rename_cascade](../../../cod_doc/services/link_service.py).

**Symptom:** COD-013 in `rename_cascade` intentionally skips markdown-relative links `[label](../path.md)` — too fragile without a path mapping. Canonical refs `[[doc:KEY]]` are updated. This is a documented gap, not a bug, but it creates broken links in projections after a rename.

**Fix:** task **COD-014a** opened — build a path mapping and rewrite markdown-relative refs through the same diff-flow.

---

## 3. Medium

### SC-ME-1. ✅ The frontmatter standard did not define a `type → status` table

**Where:** [standards/frontmatter.md §2](../standards/frontmatter.md).

**Symptom:** the value `status: resolved` was used in audit reports, but was not in the list of allowed values. The same for `accepted` (user-story), `superseded` (audit-report).

**Fix (applied):** added §2a "Allowed `status` by `type`" — an explicit table for all types, including `audit-report` (active/resolved/superseded). Added codes FM-006 (incompatible type/status pair) and FM-007 (reserved sensitivity). DOC-ME-4 moved to `done`.

### SC-ME-2. ✅ MASTER.md §2 incomplete

**Where:** [docs/system/MASTER.md §2](../MASTER.md).

**Symptom:** `standards/sensitive-data.md` and three audit reports are not mentioned in the package structure index.

**Fix (applied):** added `sensitive-data.md` to the `standards/` section, listed all audit files. Expanded the §5 table with explicit statuses of audit/roadmap documents.

### SC-ME-3. ✅ COD-020 status not synchronized

**Where:** [roadmap/cod-doc-task-plan.md:391](../roadmap/cod-doc-task-plan.md).

**Symptom:** the COD-020 card showed `status: pending`, although commit `426b33a` had already implemented validation. It blocked COD-030/COD-031 (they depend on COD-020).

**Fix (applied):** moved to `done` with a note about FM-002/FM-003 escalation in DocService and about uncovered rules (TP-006…TP-011 — section-level cross-checks; FM-006/FM-007 — partially activated together with this audit).

### SC-ME-4. ✅ `source_of_truth` as a nested dict not documented in §2

**Where:** [standards/frontmatter.md §2](../standards/frontmatter.md).

**Symptom:** §2 defined the field as a boolean, but execution-plans use a nested dict (mentioned only in §7).

**Fix (applied):** §2 now explicitly refers to the §7 variant for execution-plan; §7 expanded with an example and an explanation that FM-003 does not apply to the dict variant.

### SC-ME-5. ⚪ Capability gaps (CLI/MCP/ContextService)

**Where:** [cod_doc/cli/](../../../cod_doc/cli/), [cod_doc/mcp/server.py](../../../cod_doc/mcp/server.py).

**Symptom:** CLI covers ~10% of the spec (no `cod-doc doc/task/plan/link/decision/context/audit`). The MCP server is legacy on the file-based `Project.tasks[]`, not on services. ContextService is missing (only legacy `core/context.py` exists).

**Decision:** already tracked in the plan as **COD-030, COD-031, COD-032, COD-040** (Section D + E). Not a duplicate, not a task — the actual situation matches the `pending` status in the plan.

### SC-ME-6. ✅ → task COD-026. TUI without tests

**Where:** [cod_doc/tui/](../../../cod_doc/tui/) — 1100+ lines, 0 tests.

**Fix:** task **COD-026** opened — smoke tests via `textual.pilot.Pilot` (low priority).

---

## 4. Low

### SC-LO-1. ✅ audit-followups-task-plan stale

**Where:** [roadmap/audit-followups-task-plan.md](../roadmap/audit-followups-task-plan.md).

**Symptom:** `last_updated: 2026-04-19`, not updated for 9 days.

**Fix (applied):** updated to 2026-04-28; DOC-ME-4 moved to `done`; Progress Overview recalculated (13/23 done).

### SC-LO-2. ✅ False-positive "broken links" in standards

**Where:** [standards/document-link.md §1](../standards/document-link.md), [capabilities/plan-management.md §3](../capabilities/plan-management.md).

**Symptom:** the audit agent flagged "broken links" to `tasks/section-a.md`, `../modules/M1-auth/overview.md`, etc.

**Fix (analysis):** all these links are inside fenced code blocks or in backticks — not active, but synthetic examples. The link linter should ignore code-fence content (this is already accounted for in `LinkService.parse` — it skips code blocks). No action required.

### SC-LO-3. ✅ Orphan capabilities (mentioned only in the audit)

**Where:** `capabilities/decisions-and-questions.md`, `audit-and-ci.md`, `project-bootstrap.md`.

**Symptom:** created as stubs during the first audit, no one references them from other documents.

**Fix (applied):** both MASTER.md (§2 structure and §4 matrix) already reference these files. Further references will appear naturally as COD-024/030/032 are implemented.

### SC-LO-4. ⏳ `api_navigation` marked "later"

**Where:** [migration/from-restate.md:71](../migration/from-restate.md).

**Decision:** related to COD-051 (Restate importer) — deferred for now. No action required until Section F starts.

### SC-LO-5. ✅ → task COD-014a. LinkService.rename markdown-paths skip

See SC-HI-4. Described as a low-severity follow-up to the closed COD-013.

---

## 5. Closed inline in this revision

| Code | Fix | Files |
|------|-----|-------|
| SC-CR-1 | DocService.create FM validation | `cod_doc/services/doc_service.py`, `tests/services/test_doc_service.py` (+3 tests) |
| SC-ME-1 | §2a type→status table | `standards/frontmatter.md` |
| SC-ME-2 | MASTER.md §2 + §5 | `docs/system/MASTER.md` |
| SC-ME-3 | COD-020 → done | `roadmap/cod-doc-task-plan.md` |
| SC-ME-4 | source_of_truth dict in §7 | `standards/frontmatter.md` |
| SC-LO-1 | audit-followups refresh | `roadmap/audit-followups-task-plan.md` |
| SC-LO-2 | false-positive (analysis) | — |
| SC-LO-3 | MASTER orphans | `docs/system/MASTER.md` |
| (DOC-ME-4) | type→status in frontmatter | moved to `done` |

## 6. Opened in the roadmap

| Task | Section | Priority | Notes |
|------|---------|----------|-------|
| **COD-024** | G-Hardening | high | CI workflow — starts first |
| **COD-025** | G-Hardening | high | Sensitive-data infra (depends on COD-020) |
| **COD-026** | G-Hardening | low | TUI smoke-tests |
| **COD-014a** | G-Hardening | medium | rename markdown-cascade |
| **WEB-040** | E-Architecture-Hygiene (web) | medium | remove db_resolver bypass |

## 7. What remained out of scope

- **Capability-completeness** (CLI / MCP / ContextService / Decisions+Questions) — already tracked in the plan as Section D/E. Implementation — future COD-030..032, COD-040.
- **TP-006…TP-011** — section-level cross-checks from the task-plan standard; implemented as part of COD-031 (cod-doc audit CLI).
- **api_navigation usage** — waits for COD-051 (Restate importer).

---

## 8. Changelog

| Date | Event |
|------|---------|
| 2026-04-28 | Audit conducted; 10 inline fixes applied, 5 tasks opened in the roadmap (COD-024, COD-025, COD-026, COD-014a, WEB-040). 326+3 tests green. |
| 2026-04-28 | COD-024 closed — `.github/workflows/ci.yml` created (pytest blocking, ruff/mypy advisory). Follow-up COD-024a opened (lint debt cleanup). |
| 2026-05-02 | **Resolved.** The last task SC-HI-3 (web → infra bypass) closed in WEB-040: `cod_doc/api/web/db_resolver.py` removed, the web layer moved to `cod_doc.api.deps.{get_project_db, try_open_project_db}`, the architectural rule pinned by AST tests in `tests/api/test_web_layer_imports.py`. Suite of 45 web tests green. |
