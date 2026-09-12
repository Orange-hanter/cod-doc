---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-28
last_updated: 2026-08-29
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-27-m1-phase1.md
  - ../audit/2026-09-10-sprint-m1-phase1.md
  - ../../adoption-playbook.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
---

# Sprint 2026-08-28 → 2026-09-11 — "M2: feedback embedded"

> **Purpose.** Close milestone M2 "Feedback embedded" (ROADMAP):
> top friction-log findings fixed, route drift does not accumulate,
> audit tails of the previous sprint (F1/F2/F4) eliminated.
>
> **Not source of truth.** Task statuses are in the DB (plan `adoption-2026-08`);
> priorities are in [ROADMAP.md](ROADMAP.md).

## 0. Ground truth at sprint start (reconciled 2026-08-28)

- The previous sprint closed early: audit
  [2026-09-10-sprint-m1-phase1.md](../audit/2026-09-10-sprint-m1-phase1.md),
  suite **1528 passed**, drift 122/122 in_sync, git tree clean.
- Friction log ADO-005: 14 entries (M2 criterion ≥10 met). Top-1
  (projection_hash on import, entries #7/#12) closed as **ADO-023**.
- Unclosed log candidates: #5 (search without --reindex), #6 (no doc delete),
  #8 (dry-run limit of 50 lines), #9 (venv not in `_SKIP_DIRS`), #10 (broken path
  for `*.txt`), #11 (hidden directories — docs), #13 (MASTER.md template),
  #14 (Russian frontmatter / diataxis-quadrant).
- Audit tails in the DB: ADO-027 (F1, high), ADO-028 (F2, medium), ADO-029
  (F4, medium).
- ZAIrgRush pilot is alive: OS-cron `*/15 * * * * cod-doc routine tick -p
  zairgrush`, routines `approval_stale_default`, `doc_drift_daily`.

## 1. Goals

- **G1 — Friction findings closed (ADO-006).** By owner decision we take
  **four** slots beyond the closed top-1: #5 → ADO-030, #6 → ADO-031,
  #9 → ADO-032, #13 → ADO-033.
- **G2 — Drift does not accumulate.** ADO-011 (`audit --web-routes` → 0 WR-1/WR-2,
  `capabilities/web-frontend.md §3` synchronized) + ADO-012 (advisory step
  in CI).
- **G3 — Audit tails.** ADO-027 (F1: check alembic current==heads), ADO-028
  (F2: cron parsing in `tick()`), ADO-029 (F4: `project add` in
  onboarding skill).
- **Stretch — SYM-007.** ADR bridge for ZAIrgRush: 13 ADRs → adr-system +
  decisions.jsonl (blocked_by ADO-016 — done, ready).

## 2. Task contracts

### G1 — friction (new tasks, section C)

| ID | Friction | Contract (acceptance) |
|---|---|---|
| **ADO-030** | #5 | After `import docs`, search works without a manual `--reindex`: FTS is updated incrementally in the import transaction. Fallback (if incremental is expensive): the import prints an explicit hint. Decision goes in task-doc. Regression test "import → search hit" |
| **ADO-031** | #6 | CLI `cod-doc doc delete <key>` + bulk (`--path-glob`/`--type`), `--dry-run`, cascade of sections/links, activity event (proposal 09), cli tests. Cascade behavior is described in task-doc before implementation |
| **ADO-032** | #9 | `_SKIP_DIRS` += `venv`, `.venv`; regression test; also check coverage of `node_modules`/`__pycache__` |
| **ADO-033** | #13 | The MASTER.md template generates sections only for directories that actually exist in the pilot; regeneration on Orakul without references to `/specs/`, `/arch/`, `/models/` |

### G3 — audit tails (already in the DB)

- **ADO-027 (F1):** automatic "alembic current == heads" check (a routine or
  a verification step); divergence is reported explicitly; run on cod-doc.
- **ADO-028 (F2):** `tick()` computes next-fire from the full cron expression
  (`47 9 * * *` → next day at 9:47), without degrading to an interval. Tests:
  `*/N`, `0 */N`, `M H * * *`, invalid expressions. No new dependencies unless
  necessary (check the lockfile for croniter).
- **ADO-029 (F4):** the `project-onboarding` skill + runbook explicitly require
  `project add` (registration in `~/.cod-doc/config.yaml`) as a mandatory step.

### G2 — route drift (already in the DB)

- **ADO-011:** `cod-doc audit --web-routes` → 0 WR-1 and 0 WR-2.
- **ADO-012:** `audit --web-routes` in CI as an advisory step.

### Out of scope

Friction #8/#10/#11/#14 — backlog (assessed at the end of the sprint based on
remaining pace). SYM-008…011, Track B, STB-023. F3 (single-file upload hash) —
soft gap, not taken.

## 3. Execution order

1. G1: ADO-032 (cheap) → ADO-030 → ADO-031 → ADO-033.
2. G3: ADO-027 (high) → ADO-029 → ADO-028.
3. G2: ADO-011 → ADO-012.
4. Stretch: SYM-007.
5. Final: full suite, drift check, ADO-006 → done, M2 checkboxes in ROADMAP.md,
   audit report `docs/system/audit/2026-09-11-sprint-m2-feedback-loop.md`
   (skill `audit-cadence`).

## 4. Risks

- **ADO-030 may turn out expensive** (incremental FTS) — fallback to a hint
  is fixed in the contract.
- **ADO-031 cascades** — deleting a document with links/revisions must not
  break history; describe the behavior before implementation.
- **Pace of the previous sprint** (closed early) — stretch SYM-007 is realistic,
  but not a sprint failure if it does not fit.

## 5. Definition of Done

- [x] ADO-030/031/032/033 → done → ADO-006 → done (via `task_complete`
      with `commit_sha`)
- [x] ADO-027/028/029, ADO-011/012 → done
- [x] `ruff check`, `ruff format --check`, `mypy cod_doc/`, `pytest` — green
      (1562 passed)
- [x] Drift 100% in_sync (123/123, CLI)
- [x] ROADMAP.md: M2 checkboxes, active sprint pointer
- [x] Sprint audit report in `docs/system/audit/`
      ([2026-09-11-sprint-m2-feedback-loop.md](../audit/2026-09-11-sprint-m2-feedback-loop.md))

> **Sprint closed early — 2026-08-29.** All goals and the SYM-007 stretch
> were completed.
