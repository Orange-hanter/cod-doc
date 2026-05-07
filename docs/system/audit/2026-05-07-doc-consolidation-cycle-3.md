---
type: audit-report
scope: documentation-consolidation / paperclip-adoption Phase 2-4 + UX + Tooling
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-doc-consolidation-cycle-1.md
  - 2026-05-07-doc-consolidation-cycle-2.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/05-issue-documents.md
  - ../../../proposals/06-atomic-checkout.md
  - ../../../proposals/07-routines.md
  - ../../../proposals/08-status-taxonomy.md
  - ../../../proposals/09-activity-log.md
  - ../../../proposals/10-adapter-pattern.md
  - ../../../proposals/11-agents-md.md
  - ../../../proposals/12-approvals.md
  - ../../../proposals/13-import-ux-redesign.md
  - ../../../proposals/14-legacy-tasks-migration-ux.md
  - ../../../proposals/15-link-system-and-rendering.md
---

# Documentation Consolidation — Cycle 3 (Phase 2-4 + UX + Tooling)

> **Назначение.** Завершить mapping всех 15 RFC из `/proposals/` в
> структурированный беклог: 11 stories + 26 задач + Section F с tooling-
> фиксами по результатам Cycle 2.

## 1. TL;DR

- 11 новых stories: **US-009..US-019** (по одной на proposal 05-15).
- 26 новых задач распределены по 5 секциям плана (B/C/D/E/F).
- Section F (Tooling fixes) заведена для G1-G3 — обнаруженных в Cycle 2
  API-gap'ов, мешающих RFC-беклогу.
- Plan total: **43** задачи (17 Phase 1 + 6 Phase 2 + 7 Phase 3 + 3 Phase 4
  + 7 UX + 3 Tooling).

## 2. Cycle-3 deliverables

| # | Деливерабл | Файл/действие | Статус |
|---|------------|----------------|--------|
| D1 | Cycle-3 audit-report | `docs/system/audit/2026-05-07-doc-consolidation-cycle-3.md` | ✅ this file |
| D2 | Section B/C/D/E/F в DB plan | direct PlanSectionRepository × 5 | ✅ |
| D3 | Stories US-009..US-019 (11 шт) | `story_create` × 11 | ✅ |
| D4 | DB-задачи PCA-100..PCA-422 (23) + PCA-901..903 (3) = 26 | `task_create` × 26 | ✅ |
| D5 | Execution-plan markdown с full Section B-F | `Edit` | ✅ |

## 3. Story map (Cycle 3)

| Story | Status | Prop | Section | Tasks |
|-------|--------|------|---------|-------|
| US-009 | accepted | 05 | B | PCA-100, PCA-101 |
| US-010 | accepted | 06 | C | PCA-200, PCA-201 |
| US-011 | accepted | 07 | C | PCA-210, PCA-211 |
| US-012 | accepted | 08 | C | PCA-220, PCA-221 |
| US-013 | accepted | 09 | B | PCA-110, PCA-111 |
| US-014 | draft | 10 | D | PCA-300, PCA-301, PCA-302 |
| US-015 | accepted | 11 | C | PCA-230 |
| US-016 | accepted | 12 | B | PCA-120, PCA-121 |
| US-017 | accepted | 13 | E | PCA-400, PCA-401 |
| US-018 | accepted | 14 | E | PCA-410, PCA-411 |
| US-019 | accepted | 15 | E | PCA-420, PCA-421, PCA-422 |

US-014 (Adapter pattern) намеренно оставлена `draft` — высокий риск,
требует отдельного RFC-обсуждения; `accepted` для остальных 10.

## 4. Tooling fixes (Section F)

| Task | Gap | Priority | Description |
|------|-----|----------|-------------|
| PCA-901 | G1 | high | plan_create / plan_section_create MCP tools |
| PCA-902 | G2 | critical | task_create persists blocked_by as dependency edges |
| PCA-903 | G3 | high | task_create persists story_id and affects_files |

PCA-902 — `critical` потому что блокирует базовое use-case'ы plan_ready /
plan_audit / critical_path. До закрытия фикса беклог можно держать только
описательно (markdown table в плане).

## 5. Plan health

```
plan_progress(paperclip-adoption-task-plan)
→ total: 43, done: 0, in_progress: 0, remaining: 43
→ A: 17, B: 6, C: 7, D: 3, E: 7, F: 3
plan_audit
→ issues_total: 0 (но critical_path_length=1 из-за G2 — не отражает
  реальную глубину зависимостей)
```

## 6. Cross-section dependencies

```mermaid
graph LR
    A[Section A: Phase 1<br/>Skills+Heartbeat+Wake+Run-id] --> B[Section B: Phase 2<br/>Issue docs+Activity+Approvals]
    A --> C[Section C: Phase 3<br/>Checkout+Routines+Status+AGENTS]
    B --> C
    A --> D[Section D: Phase 4<br/>Adapter pattern]
    A --> E[Section E: UX & Migration<br/>Import/Legacy/Links]
    F[Section F: Tooling fixes<br/>plan_create + persistence] -.unblocks ergonomics.-> A
```

Строгие edges (через `blocked_by` в DB-задачах, при условии fix G2):
- PCA-101 ← PCA-100 (task_doc tool ← migration)
- PCA-111 ← PCA-110, PCA-031 (activity emitter ← migration + run_id)
- PCA-121 ← PCA-120, PCA-022 (approval ← migration + WakeContext)
- PCA-200 ← PCA-220 (checkout зависит от 7-state taxonomy)
- PCA-201 ← PCA-200
- PCA-211 ← PCA-210, PCA-022 (scheduler ← Routine entity + WakeContext)
- PCA-221 ← PCA-220
- PCA-301 ← PCA-300; PCA-302 ← PCA-301
- PCA-401 ← PCA-400; PCA-411 ← PCA-410; PCA-422 ← PCA-421

## 7. Acceptance for cycle 3

- [x] Все 15 proposals имеют ≥1 story.
- [x] Все 11 новых stories привязаны к 1+ задаче (плановой), статусы
      `accepted` (для активных) либо `draft` (для US-014 Adapter).
- [x] Sections B/C/D/E/F созданы в DB plan + наполнены.
- [x] Section F фиксирует G1/G2/G3 как actionable задачи с приоритетами.
- [x] Execution-plan markdown отражает полную картину (43 задачи,
      6 секций, dep-graph).

## 8. Out of cycle (handed off)

- **Cycle 4:** link integrity — `link_verify` по всем активным docs;
  dedup мусорного doc-record `arch/arch/architecture`; нормализация
  doc-keys (`MASTER` → `MASTER_root`?); reindex; sync хешей.
- **Cycle 5:** финальный close-out audit; обновление memory с шаблоном
  «cycle-N → audit-report». Возможно — extract нового feedback memory:
  «когда нет MCP-API для bootstrap-операции, заводить F-секцию в том же
  плане, не отдельный roadmap».
