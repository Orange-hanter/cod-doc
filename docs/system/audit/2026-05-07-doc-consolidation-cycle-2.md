---
type: audit-report
scope: documentation-consolidation / paperclip-adoption Phase 1
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-doc-consolidation-cycle-1.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../roadmap/paperclip-adoption-kickoff-2026-05-07.md
  - ../../../proposals/01-skills-layer.md
  - ../../../proposals/02-heartbeat-context.md
  - ../../../proposals/03-wake-payload.md
  - ../../../proposals/04-run-id-audit.md
---

# Documentation Consolidation — Cycle 2 (Phase 1 Backlog)

> **Purpose.** Close the RFC vacuum between proposals/ and the DB backlog for Phase 1
> (proposals 01-04) and record the discovered API gaps.

## 1. TL;DR

- Opened `paperclip-adoption-task-plan` (plan_id=2) + Section A (8 tasks —
  actually 17 after finalize) + kickoff brief.
- Created 4 stories US-005..US-008 with status `accepted` and acceptance criteria.
- Created 17 tasks PCA-001..PCA-034, linked to section A.
- Discovered 3 API gaps in MCP that prevent full use of the backlog
  (see §3) — moving them as F-tasks to Cycle 4.

## 2. Cycle-2 deliverables

| # | Deliverable | File/action | Status |
|---|------------|----------------|--------|
| D1 | Cycle-2 audit-report | `docs/system/audit/2026-05-07-doc-consolidation-cycle-2.md` | ✅ this file |
| D2 | Kickoff brief | `docs/system/roadmap/paperclip-adoption-kickoff-2026-05-07.md` | ✅ |
| D3 | Execution plan | `docs/system/roadmap/paperclip-adoption-task-plan.md` | ✅ |
| D4 | DB plan (`paperclip-adoption-task-plan`) + Section A | direct PlanRepository (no MCP API) | ✅ |
| D5 | Stories US-005..US-008 (`accepted`) with acceptance | `story_create` × 4 | ✅ |
| D6 | DB tasks PCA-001..PCA-034 (17 items) in Section A | `task_create` × 17 | ✅ |
| D7 | doc-records for plan + kickoff + cycle-1 audit | `doc_create` × 3 | ✅ |

## 3. Discovered API gaps (for Cycle 4 / future tasks)

### G1 — No MCP-tool to create a Plan / PlanSection

To open the plan "paperclip-adoption-task-plan", we had to call
`PlanRepository.add()` directly through Python (bypassing the MCP facade).
`task_create` fails with `Plan '<scope>' not found` if the plan does not exist.

**Recommendation:** add `mcp.plan_create(project, scope, principle, sections=[{letter, title, slug, position}])`
and/or `mcp.plan_section_create(plan_scope, letter, title, slug, position)`
to `cod_doc/mcp/tools/plan_tools.py`. This is needed at minimum to bootstrap new
directions (paperclip-adoption, future migrations).

### G2 — `task_create.blocked_by` is accepted but not persisted as dependency-edges

We pass `blocked_by=["PCA-001"]` to `task_create` for PCA-002 — the response
echoes the value, but `task_get(PCA-002)` returns `blocked_by: []`.
`plan_ready` returns PCA-002 as ready, although it should be blocked by
PCA-001. Similarly — `plan_audit.critical_path_length=1` (instead of the 4-5
expected for the Phase 1 chain).

**Hypothesis:** the input `blocked_by` is validated (type ok), but not turned
into a `dependency` row with `kind='blocks'`. The existing `task_set_blocker` /
`task_clear_blocker` only manipulate the `blocked_reason` field (external
blocker text), not the graph edges.

**Recommendation:** either in `task_create`, after the insert, also create
`dependency` rows for each `blocked_by` id, or add a
`task_add_dependency(from_task_id, to_task_id, kind='blocks')` tool and
do it as a second phase on bootstrap.

### G3 — `task_create.affects_files` is accepted but returns `[]`

`affects_files=[<list>]` echoes `[]` in the response already at the create stage.
Most likely, the field goes into a formal "accepted", but is not written to the
`affected_file` table.

**Recommendation:** check the `task_create` MCP serializer and persistence in
`AffectedFileRepository`; or document the behavior and remove the field from
the signature until the fix.

> All three gaps will be recorded as tasks **F1-F3** in the plan
> [paperclip-adoption-task-plan.md](../roadmap/paperclip-adoption-task-plan.md)
> in Cycle 3 (Section F: Tooling fixes), since they block any planned
> work with the RFC backlog.

## 4. Story coverage snapshot (after Cycle 2)

| Story | Status | Tasks | Acceptance |
|-------|--------|-------|------------|
| US-001 | delivered | 0 (legacy) | — |
| US-002 | delivered | 0 (legacy) | — |
| US-003 | delivered | 0 (legacy) | — |
| US-004 | delivered | 0 (legacy) | — |
| US-005 | accepted | 4 (PCA-001..004) | 4 criteria |
| US-006 | accepted | 3 (PCA-010..012) | 4 criteria |
| US-007 | accepted | 5 (PCA-020..024) | 4 criteria |
| US-008 | accepted | 5 (PCA-030..034) | 4 criteria |

> The story-task linkage shows `tasks_total=0` in `story_coverage`, because
> task→story links are passed as the `story_id` parameter to `task_create`,
> but are not persisted as `story_link` rows. Also recorded in the G2 list.

## 5. Plan health

```
plan_progress(paperclip-adoption-task-plan)
→ total: 17, done: 0, in_progress: 0, remaining: 17
→ section A: 17 pending
plan_audit
→ issues_total: 0, cycles: [], done_with_unfinished_blocks: []
→ critical_path_length: 1 (see G2)
```

## 6. Acceptance for cycle 2

- [x] Phase 1 RFC (proposals 01-04) has a canonical execution-plan on disk.
- [x] A kickoff brief exists and is registered in `docs/system/roadmap/`.
- [x] Stories US-005..US-008 are created, status `accepted`, acceptance criteria
      are defined, the hibernated draft is moved.
- [x] 17 DB tasks of Phase 1 are created in `paperclip-adoption-task-plan / Section A`.
- [x] `plan_audit` has no cycles and no done-with-unfinished-blocks.
- [x] Discovered API-gaps (G1/G2/G3) are recorded in this report and move
      to Cycle 3 to be formalized as F1-F3 tasks.

## 7. Out of cycle (handed off)

- **Cycle 3:** Phase 2-4 (proposals 05-12) + UX (13-15) → detailing
  Sections B/C/D/E + stories US-009..US-019. Also open Section F with
  F1-F3 (fixes for the API-gaps).
- **Cycle 4:** link integrity, dedup the `arch/arch/architecture` doc-record,
  link_verify across all active docs.
- **Cycle 5:** final consolidation audit + memory updates.
