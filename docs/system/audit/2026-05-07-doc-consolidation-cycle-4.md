---
type: audit-report
scope: documentation-consolidation / cross-links + drift + dedup
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-doc-consolidation-cycle-1.md
  - 2026-05-07-doc-consolidation-cycle-2.md
  - 2026-05-07-doc-consolidation-cycle-3.md
  - ../MASTER.md
---

# Documentation Consolidation — Cycle 4 (Cross-links & Integrity)

> **Purpose.** Record the state of link integrity after cycles 1-3,
> disambiguate DB-doc-records, update the MASTER index and
> record the discovered drifts as backlog.

## 1. TL;DR

- Hashes of hybrid links in `/MASTER.md` — **10/10 VALID** (`check_stale_refs`).
- Cycle-2/3 audit-reports are registered as doc-records (active).
- Found another link_service gap (G4) — relative-path resolution from sections
  does not work, which gives **39 broken-links on `docs/system/MASTER` →
  capabilities/standards/audit/roadmap**, although all target doc_keys
  exist.
- Found a fixture relic on disk: `arch/arch/architecture.md` (commit
  e51e85f) — a double path prefix, the content is the same as in the old "integration-test"
  bootstrap. Decided not to delete without an explicit command (see §4).
- Doc-drift on `docs/system/MASTER` and `MASTER`-root — `stale_export`:
  files were edited on-disk, the DB projection was not re-synced.
  This is the expected state after edit-in-place; resync is a backlog item.

## 2. Cycle-4 deliverables

| # | Deliverable | File/action | Status |
|---|------------|----------------|--------|
| D1 | Cycle-4 audit-report | `docs/system/audit/2026-05-07-doc-consolidation-cycle-4.md` | ✅ this file |
| D2 | DB doc-records for cycle-2 + cycle-3 audit docs | `doc_create` × 2 | ✅ |
| D3 | docs/system/MASTER.md §6 changelog: cycle-2 + cycle-3 entries added | `Edit` | ✅ |
| D4 | docs/system/MASTER.md §2 structure: 2026-05-06 audits + cycle-{1..5} + paperclip roadmap added | `Edit` | ✅ |
| D5 | check_stale_refs(cod-doc) re-run — 10/10 VALID | MCP | ✅ |
| D6 | Opened gap G4 (relative-path link resolver) → backlog item in Cycle 5 | description below | ✅ |

## 3. Findings

### F1 — Link resolver gap (G4): relative-paths are not resolved

`link_list(docs/system/MASTER)` returns 39 markdown-links with status
`broken_reason: "document not found: <key>"`, while all target doc_keys
(for example, `docs/system/VISION`, `docs/system/capabilities/context-retrieval`)
**exist** in the DB. The parser does not account for the source-document directory when
resolving `[label](relative/path.md)` — as a result, link_service looks for the
doc_key as `VISION` (without the `docs/system/` prefix).

This is the same problem described in [proposal 15 §1](../../../proposals/15-link-system-and-rendering.md),
but more fundamental (not an "import hole", but the absence of relative-resolution
altogether). PCA-421 in the paperclip-adoption plan should include this fix — add
explicitly to acceptance in Cycle 5 (memory-pattern: expand the scope of an F-task via
update_task, do not open a duplicate).

### F2 — Doc-record `arch/arch/architecture` — a fixture relic

`/Users/dakh/Git/cod-doc/arch/arch/architecture.md` (commit e51e85f, 2026-04-05):
- Heading: "🏗️ Application architecture: integration-test" (fixture name)
- Layout: `arch/arch/` — a double prefix, illogical; the real arch-doc lives in `/arch/architecture.md`.
- DB doc-record: `doc:arch_arch_architecture`, status `draft`, drift `stale_export`.

**Decision:** do not delete without an explicit request (the carpenter's principle "measure
twice, cut once"). Record as backlog `PCA-FIX-001` (TODO in Cycle 5)
with a proposal (a) `git rm arch/arch/architecture.md`, (b) doc-record
deprecate→delete or rename the doc_key to `_legacy_fixture/arch_architecture`.

### F3 — Stable doc_drift divergence for MASTER documents

`docs/system/MASTER` and root `MASTER` have `status=stale_export` after
cycles 1-3 (we edited the files directly via Edit, not via
`projection_service.import_document` or `doc.patch_section`).

**This is normal** for the current architecture:
- Source of truth — the DB for most docs.
- But MASTER documents were historically edited on-disk; a reconciliation-flow
  exists in `capabilities/doc-evolution.md`, but an automatic resync
  after an Edit is not launched.

**Decision:** in Cycle 5 run `import_document` for both MASTER-files
to sync the DB body (or accept the current delta as a known
state and update it on a planned basis).

### F4 — DB plan_ready shows blocked tasks as ready

Described in Cycle-2 G2 / Cycle-3 PCA-902: `task_create.blocked_by` is not
persisted. In Cycle 4 verified empirically: `plan_ready(paperclip-adoption-task-plan)`
returns PCA-002 (blocked_by=PCA-001) among ready, which is incorrect.

PCA-902 is already in Section F (priority `critical`).

## 4. Plan health

```
plan_progress(paperclip-adoption-task-plan)
→ total: 43, sections: A=17, B=6, C=7, D=3, E=7, F=3
plan_audit
→ issues_total: 0, cycles: [], done_with_unfinished_blocks: []
→ critical_path_length: 1 (reflects G2 — the real depth is unknown until the fix)

check_stale_refs(cod-doc)
→ 10/10 VALID on all hybrid-refs in /MASTER.md
```

## 5. Acceptance for cycle 4

- [x] Cycle-2 + Cycle-3 audit reports are registered as doc-records.
- [x] `docs/system/MASTER.md` §6 has changelog-entries for cycles 1, 2, 3.
- [x] `docs/system/MASTER.md` §2 structure reflects the current snapshot of audit/
      and roadmap/.
- [x] `check_stale_refs` 10/10 VALID after the edits.
- [x] The discovered gaps (F1/F2/F3/F4) are either linked to existing backlog
      tasks, or are moved to Cycle-5 backlog updates.

## 6. Out of cycle (handed off → Cycle 5)

1. **Expand PCA-421 acceptance** by adding a relative-path resolver
   (F1/G4) via `update_task`.
2. **Create a backlog task `PCA-FIX-001`** for cleaning up the fixture
   `arch/arch/architecture.md` (F2).
3. **Memory updates** (if a new pattern arises):
   - the case "MCP facade accepts a field but does not persist it" — a feedback-pattern.
   - the case "edit-in-place vs DB-import drift" — a standard handoff.
4. **Final close-out audit** of Cycle 5 with a summary of the 5 cycles and
   a self-check.
