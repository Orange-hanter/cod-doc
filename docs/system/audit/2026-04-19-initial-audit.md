---
type: audit-report
scope: docs/system
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
audit_target_revision: initial-package-2026-04-19
related_docs:
  - ../MASTER.md
  - ../roadmap/audit-followups-task-plan.md
---

# Initial Audit — `docs/system/`

> Audit of the initial COD-DOC documentation package (16 files, ~3100 lines, status `draft`).
> The goal of the audit is to find contradictions, gaps, broken links, and under-specified models before turning anything into code.
> Severity: **critical** — blocks implementation, **high** — must be resolved before roadmap stage A, **medium** — after A, **low** — cosmetic.

## Summary

| Severity | Count | Where |
|----------|------:|------|
| critical | 2 | tracked in the follow-up plan as DOC-CRT-* |
| high     | 9 | ↳ DOC-HI-* |
| medium   | 7 | ↳ DOC-ME-* |
| low      | 5 | ↳ DOC-LO-* |
| **total** | **23** | |

The full list and prioritization is in [../roadmap/audit-followups-task-plan.md](../roadmap/audit-followups-task-plan.md).

## 1. Coverage of user requirements

The capabilities declared by the user were checked against the package:

| Requirement | Covered | Where |
|-------------|--------|-------|
| Task creation | ✅ | capabilities/task-creation, standards/task-plan |
| Documentation evolution | ✅ | capabilities/doc-evolution |
| Auto-linking | ✅ | capabilities/auto-linking, standards/document-link |
| Document links | ✅ | standards/document-link |
| Revision history | ✅ | standards/revision-history |
| Concentrated context | ✅ | capabilities/context-retrieval |
| Plan management | ✅ | capabilities/plan-management |
| User stories + dependency graph | ✅ | capabilities/user-stories-graph |

The baseline requirements are covered. Below are the gaps that surfaced during cross-reading.

## 2. Critical — blocks implementation

### CRT-1. The `revision_id` format is not defined

`revision-history.md` uses ids `r_abc123`, `r_01hq…`, `r_def456`; the format is not fixed. The ID must be stable over time and sortable.

**Fix:** ULID (`01HQX...`), documented in `standards/revision-history.md §2`.

### CRT-2. The `embedding` table is not defined

`capabilities/context-retrieval.md` promises semantic-search with per-section embeddings, but `DATA_MODEL.md` has no corresponding table (`embedding`, `embedding_chunk`).

**Fix:** add §3.14 to `DATA_MODEL.md`.

## 3. High — required before Section A of the roadmap

### HI-1. Open Questions / architectural decisions are not modeled

Restate has `Open Questions.md` as the canonical registry of unresolved questions. The package has neither a capability, nor an entity, nor a template for this.

**Fix:** a new capability `decisions-and-questions.md`, new document types in `frontmatter.md` (`decision`, `open-question`).

### HI-2. No catalog of agents / roles

Restate uses `.github/agents/` (task-steward, docs-review, logical-commits). COD-DOC refers to `agent:task-steward` in the author fields of `revision`, but does not define: which agents exist, what they are allowed to do, how they are declared in a project.

**Fix:** a new capability `agents-and-skills.md` + an `agents/` field in the project, analogous to Restate's `.github/agents/`.

### HI-3. No sensitive-data standard

In Restate `Sensitive Data Protection Standard.md` is a cross-project document. We have no analog; meanwhile the DB stores document bodies that may contain PII.

**Fix:** `standards/sensitive-data.md` + a `sensitivity` field on `Document`.

### HI-4. `cod-doc project new` is not described

Migration refers to `cod-doc project new --slug restate`, but what it creates in the DB, which skeleton documents, which agents — is described nowhere.

**Fix:** capability `project-bootstrap.md`.

### HI-5. Audit checks are scattered

Validation rules are listed in each of the 6 capabilities + frontmatter + task-plan. There is no single place where it is clear what exactly `cod-doc audit --strict` does.

**Fix:** capability `audit-and-ci.md` with a consolidated catalog of checks and CLI/CI integration.

### HI-6. The error model is not defined

Services return `revision_id`, but what happens on error? Which code, which structure, how MCP/REST convey it? — nowhere.

**Fix:** a new section in `ARCHITECTURE.md §11 Error model` + a reusable `Error` enum for all surfaces.

### HI-7. Concurrency and auth for the shared Postgres profile

ARCHITECTURE mentions a Postgres profile with REST API, but does not describe: optimistic-locking, request authorization, identity (how a surface distinguishes `human:dakh` from `mcp:claude`).

**Fix:** `ARCHITECTURE.md §12 Concurrency & Identity` + capability `multi-user-mode.md` or a subsection in `project-bootstrap`.

### HI-8. `Document.body` vs `Section.body` duplicate content

In DATA_MODEL both tables store body. It is not stated which is the materialized projection (production) and which is the denormalized cache. On writes there will be divergence.

**Fix:** declare `Section.body` derived (`body` is stored only in `document`; sections are extracted via a view with substring indexes over anchor boundaries), or the reverse — `document.body` is a derived view over sections. Decide in `DATA_MODEL.md §3.2-§3.3`.

### HI-9. No `proposal` table for the review-flow

`doc-evolution.md` promises `doc.propose_edit` with `pending_approval`. There is no table to store proposals between propose and approve.

**Fix:** add `proposal` to `DATA_MODEL.md §3.15`.

## 4. Medium — after the foundation

### ME-1. Documentation Graph — the generated artifact is not described

Restate renders `Documentation Graph.md` by hand. Our service has all the information, but the generation recipe and file format are not described.

**Fix:** in `capabilities/auto-linking.md` (or in a new `documentation-graph.md`).

### ME-2. No DB backup/restore procedure

The local `.cod-doc/state.db` is the single source of truth. What to do if it is corrupted.

**Fix:** capability `backup-and-export.md`.

### ME-3. No CI blueprint

It says "CI: cod-doc audit, plan next, link verify" — without specifics (GitHub Actions, GitLab CI). No template.

**Fix:** add to `audit-and-ci.md` (see HI-5) an example pipeline.

### ME-4. `frontmatter.md` and `task-plan.md` overlap on `status`

Frontmatter lists 4 values (`draft`/`review`/`active`/`deprecated`), task-plan — 3 (`pending`/`in-progress`/`done`). Restate clearly showed the confusion. We mention it in passing — need to explicitly separate the sets of dictionaries.

**Fix:** `standards/frontmatter.md §3.1 Status sets — by document type`.

### ME-5. No handling of `transclusion` (`![[…]]`)

Auto-linking mentions support, but how it integrates with rebuild and embeddings is not said (the same content indexed twice?).

**Fix:** a subsection in `standards/document-link.md §12 Transclusion`.

### ME-6. No task prioritization rules

`task-plan.md` defines 4 priority levels, but no rules for *how* to set them.

**Fix:** §5.6 in `standards/task-plan.md` — expand.

### ME-7. `audit_log` vs `revision` — boundaries overlap

`revision` is written when an entity changes, `audit_log` — on a write-path call. But a successful write-path produces a revision; why then audit_log? Currently the rules overlap.

**Fix:** `revision-history.md §13 audit_log boundary` — explain (audit_log: read requests, failed attempts, MCP metadata; revision: only successful state-mutations).

## 5. Low — cosmetic

### LO-1. Typo "zamyka" in `doc-evolution.md:152`

A Cyrillic word written in Latin letters. Fix.

### LO-2. Inconsistent terms "section file" / "section files"

Mixed in task-plan. Unify to "section file" (singular) when describing structure.

### LO-3. `tools/task-plan-ecosystem.md §3` reference to Restate without prefix

In `frontmatter.md §7` — a reference to a Restate doc without an explicit marker. Add "(Restate)" for clarity.

### LO-4. No document language standard

All docs are in Russian, entity names in English. Fix in `MASTER.md §7`.

### LO-5. `MASTER.md` has no changelog table

It declares that after `active` any document must keep a revision, but does not show the template itself.

## 6. Confirmed (where the audit found no issues)

- Cross-references between files are mostly correct (after fixing §7→§3.13 in ARCHITECTURE).
- Conformance to Restate formats (frontmatter, task-plan) is exact.
- Boundary rules (cycle detection, depends_on gate, projection_hash) — are described and consistent.
- The migration plan is realistic: there is a rollback (frozen projection), there is a warn-mode for the legacy format.
- The roadmap covers all capabilities; dependencies in the graph are valid (no cycles).

## 7. Audit decisions

1. **Apply trivial fix** immediately (done: `ARCHITECTURE.md` §9 reference `§7` → `§3.13`).
2. **Create stub-capabilities** for HI-1..HI-5 in this same step (compact documents).
3. **Open a follow-up plan** ([../roadmap/audit-followups-task-plan.md](../roadmap/audit-followups-task-plan.md)) — all 23 items as tasks.
4. **Block implementation of section A of the roadmap** (`COD-001..005`) until CRT-1, CRT-2, HI-8, HI-9 are closed — otherwise the schema will be redone.

## 8. Subsequent audits

- After the audit-followups plan is closed → a second pass (focus: implementation vs documentation).
- Before `status: active` of the package → formal sign-off from the owner.
- Once a month after the Restate migration → automatic `cod-doc audit --strict` on the package docs themselves (dogfood).
