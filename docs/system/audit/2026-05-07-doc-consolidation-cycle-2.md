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

> **Назначение.** Закрыть RFC-вакуум между proposals/ и беклогом БД для Phase 1
> (proposals 01-04) и зафиксировать обнаруженные gap'ы в API.

## 1. TL;DR

- Заведён `paperclip-adoption-task-plan` (plan_id=2) + Section A (8 задач —
  фактически 17 после finalize) + kickoff brief.
- Созданы 4 stories US-005..US-008 со статусом `accepted` и acceptance-критериями.
- Созданы 17 задач PCA-001..PCA-034, привязанные к секции A.
- Обнаружены 3 API-gap'а в MCP, которые мешают полноценно использовать беклог
  (см. §3) — переносим как F-задачи в Cycle 4.

## 2. Cycle-2 deliverables

| # | Деливерабл | Файл/действие | Статус |
|---|------------|----------------|--------|
| D1 | Cycle-2 audit-report | `docs/system/audit/2026-05-07-doc-consolidation-cycle-2.md` | ✅ this file |
| D2 | Kickoff brief | `docs/system/roadmap/paperclip-adoption-kickoff-2026-05-07.md` | ✅ |
| D3 | Execution plan | `docs/system/roadmap/paperclip-adoption-task-plan.md` | ✅ |
| D4 | DB plan (`paperclip-adoption-task-plan`) + Section A | direct PlanRepository (нет MCP API) | ✅ |
| D5 | Stories US-005..US-008 (`accepted`) с acceptance | `story_create` × 4 | ✅ |
| D6 | DB-задачи PCA-001..PCA-034 (17 шт) в Section A | `task_create` × 17 | ✅ |
| D7 | doc-records для plan + kickoff + cycle-1 audit | `doc_create` × 3 | ✅ |

## 3. Discovered API gaps (для Cycle 4 / future tasks)

### G1 — Нет MCP-tool для создания Plan / PlanSection

Чтобы завести план «paperclip-adoption-task-plan», пришлось вызвать
`PlanRepository.add()` напрямую через Python (с обходом MCP-фасада).
`task_create` валится с `Plan '<scope>' not found`, если plan не существует.

**Рекомендация:** добавить `mcp.plan_create(project, scope, principle, sections=[{letter, title, slug, position}])`
и/или `mcp.plan_section_create(plan_scope, letter, title, slug, position)`
в `cod_doc/mcp/tools/plan_tools.py`. Это нужно как минимум для bootstrap новых
направлений (paperclip-adoption, future migrations).

### G2 — `task_create.blocked_by` принимается, но не персистится в dependency-edges

Передаём `blocked_by=["PCA-001"]` в `task_create` для PCA-002 — ответ
echo'ит значение, но `task_get(PCA-002)` возвращает `blocked_by: []`.
`plan_ready` возвращает PCA-002 как ready, хотя должен быть заблокирован
PCA-001. Аналогично — `plan_audit.critical_path_length=1` (вместо 4-5,
которые ожидались для Phase 1 chain'а).

**Гипотеза:** входной `blocked_by` валидируется (тип ок), но не превращается
в `dependency`-row с `kind='blocks'`. Существующие `task_set_blocker` /
`task_clear_blocker` манипулируют только полем `blocked_reason` (внешний
блокер-текст), не дугами графа.

**Рекомендация:** либо в `task_create` после insert'а тики дорезать
`dependency` rows для каждого `blocked_by`-id, либо добавить
`task_add_dependency(from_task_id, to_task_id, kind='blocks')` тул и
делать это второй фазой при bootstrap.

### G3 — `task_create.affects_files` принимается, но возвращается `[]`

`affects_files=[<list>]` echo'ит `[]` в response уже на этапе create.
Скорее всего, поле уходит в формальный «принят», но не пишется в
`affected_file`-table.

**Рекомендация:** проверить serializer `task_create`-MCP и persistence в
`AffectedFileRepository`; либо документировать поведение и убрать поле из
сигнатуры до фикса.

> Все три gap'а зафиксируются как задачи **F1-F3** в плане
> [paperclip-adoption-task-plan.md](../roadmap/paperclip-adoption-task-plan.md)
> в Cycle 3 (Section F: Tooling fixes), поскольку они мешают любой плановой
> работе с RFC-беклогом.

## 4. Story coverage snapshot (после Cycle 2)

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

> Story-task linkage показывает `tasks_total=0` в `story_coverage`, потому что
> task→story связи передаются как `story_id` параметр в `task_create`,
> но не персистятся в `story_link`-rows. Также заведено в G2 list.

## 5. Plan health

```
plan_progress(paperclip-adoption-task-plan)
→ total: 17, done: 0, in_progress: 0, remaining: 17
→ section A: 17 pending
plan_audit
→ issues_total: 0, cycles: [], done_with_unfinished_blocks: []
→ critical_path_length: 1 (см. G2)
```

## 6. Acceptance for cycle 2

- [x] Phase 1 RFC (proposals 01-04) имеют каноничный execution-plan на диске.
- [x] Kickoff brief существует и зарегистрирован в `docs/system/roadmap/`.
- [x] Stories US-005..US-008 созданы, статус `accepted`, acceptance-критерии
      заданы, hibernated draft переведён.
- [x] 17 DB-задач Phase 1 созданы в `paperclip-adoption-task-plan / Section A`.
- [x] `plan_audit` без cycles и без done-with-unfinished-blocks.
- [x] Discovered API-gaps (G1/G2/G3) зафиксированы в этом отчёте и переходят
      в Cycle 3 для оформления как F1-F3 задачи.

## 7. Out of cycle (handed off)

- **Cycle 3:** Phase 2-4 (proposals 05-12) + UX (13-15) → детализация
  Sections B/C/D/E + stories US-009..US-019. Также завести Section F с
  F1-F3 (фиксы API-gaps).
- **Cycle 4:** link integrity, dedup `arch/arch/architecture` doc-record,
  link_verify по всем активным docs.
- **Cycle 5:** финальный consolidation audit + memory updates.
