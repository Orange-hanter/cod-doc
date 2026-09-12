---
type: audit-report
scope: sprint-m1-phase1
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-28
last_updated: 2026-08-28
related_docs:
  - ../roadmap/ROADMAP.md
  - 2026-07-29-state-of-the-project.md
  - ../../adoption-playbook.md
audience: [contributors, agents]
---

# Audit — Sprint 2026-08-27 → 2026-09-10 "M1 + Phase 1"

> **Context.** The first sprint per the new [ROADMAP.md](../roadmap/ROADMAP.md)
> (plan `adoption-2026-08`). Three goals: G1 — M1 "Pilot works",
> G2 — SYM-005/006 (shared hub + findings pipeline), G3 — friction log and
> routines on the pilots. The report closes the sprint: all goals and the stretch are confirmed
> on 2026-08-28.

## TL;DR

**All three goals are closed early (2026-08-28), the stretch SYM-006C/D is also taken.**
M1 is reached on two pilots (ZAIrgRush 31 docs, Orakul 405 docs), the findings
pipeline works end-to-end (ingest → dedup → promote → stability), routines
fire outside cod-doc. Along the way the main friction of M2 is closed (projection_hash on
import, ADO-023) and two hashing bugs found along the way. Full suite:
**1528 passed**, drift 121/121 in_sync.

## 1. Deliverables

### G1 — M1 "Pilot works"

- **ADO-016** (ZAIrgRush, 31 docs) and **ADO-017** (Orakul, 405 docs) — done,
  caveats are documented in the closing reason. ROADMAP checkboxes: `24ca045`.
- Merge worktree branches: `1a66aaa` (ADO-015 document types, SYM-004
  `--exclude`, ADO-022 projection fidelity, migration 0026).

### G2 — Shared hub + findings pipeline

| Task | Commit | Content |
|---|---|---|
| SYM-005A | `aca5028` | ProjectEntry.db_url, `db_for_entry` with an alembic_version check, `cod-doc hub init` |
| SYM-005B | `7e532d0` | Migration `0027_shared_hub`: UNIQUE(project_id, task_id), explicit downgrade |
| SYM-005C | `d3f3255` | Migration `0028_findings` + ORM + FTS scope "finding" |
| SYM-005D | `db627a3` | `finding_service/`: fingerprint/dedup/promote, atomic upsert, `finding.promoted` |
| SYM-006A | `78c9678` | `ingest_service/`: registry + adapters ai_review v1 / zairgrush_findings / zairgrush_tasks |
| SYM-006B | `d363169` | CLI `cod-doc ingest` + `cod-doc finding stability`, dry-run, 12 cli tests |

### G3 — Friction log + routines on the pilots

- **ADO-005** — 14 friction-log entries (≥5 per acceptance); top-1 is moved to
  ADO-023.
- **ADO-007** — routine `doc_drift_daily` on ZAIrgRush, 2 successful runs in
  `routine_run`; the pilot DB is upgraded 0026→0028.

### Beyond the plan (during the sprint)

- **ADO-023** (M2 top-1, bug high): bulk import did not set
  projection_hash → the whole corpus in stale_export. Fix `63c68d4` +
  a regression test "import → drift in_sync".
- **ADO-025**: `remove_dependency` in service/MCP/CLI (`2bdb1f9`) — gap
  found when closing ADO-007 (the blocker was removed with direct SQL).
- **ADO-026** (bug): web bulk apply and scan_folder hashed the first 4 KB →
  a false edited_in_place for >4 KB. Fix `f18c8d2` (full-file sha256).
- **Stretch SYM-006C/D**: API v1 (`ad413bf` — findings/context/search,
  the Bearer-gate contract RFC 22 §3.3 is documented) + MCP
  finding_*/ctx_* (`1d17329` — standard=107, full=111, minimal/agent
  byte-identical to the previous ones).
- **ADO-024**: CLI `cod-doc routine list/tick/run` (`d707307`) + OS-cron on
  the pilot (`*/15 * * * * … routine tick -p zairgrush`). A real cron-fire
  is confirmed: the probe routine `*/2` fired on the 16:30 tick
  (`routine_run.status=done`), after which it was removed. The first tick (16:15) failed
  with `Project not found` — the pilot was not in the global registry (F4), fixed with
  `project add`.
- Reconcile drift: migrations 0026–0028 are applied to the working DB (it was on
  0025), 35 stale_export documents are re-exported, drift 121/121 in_sync.

## 2. Engineering health on 2026-08-28

| Check | Result |
|---|---|
| `pytest tests/` | **1528 passed**, 0 failed (426 s) |
| `ruff check cod_doc/ tests/` | All checks passed |
| `ruff format --check` | 484 files, clean |
| `mypy cod_doc/` | Success, 313 source files |
| `doc drift -p cod-doc --all` | 121 in_sync, 0 stale / edited / missing |

## 3. Findings

- **F1. Migrations were not applied to the working DB.** 0026–0028 were written and
  tested, but `.cod-doc/state.db` remained on 0025 — this was discovered
  only by the export guard failures. A check "alembic current == heads" is needed in
  the verification before hand-off (a candidate for a routine or pre-commit).
- **F2. tick() treats cron as an interval.** `47 9 * * *` degrades to
  "once an hour"; only `*/N`, `0 */N`, `0 0 * * *` are recognized. For
  the ADO-024 cron routine it is replaced with `0 0 * * *`; a full cron parser —
  a separate task, if precise time of day is needed.
- **F3. Single-file web upload does not write content_sha256_head** — a soft gap,
  drift shows missing/stale instead of in_sync. Not a blocker.
- **F4. The global registry `~/.cod-doc/config.yaml` did not know the pilot** — the CLI
  resolved the project only from cwd until `project add`. It is worth reflecting in
  the onboarding-skill that registration in the registry is a mandatory step.
- **F5. `remove_dependency` was missing** in service/MCP/CLI (ADO-025,
  closed in the sprint).
- **F6. The MCP process of the session ages relative to main** — enum errors on
  fresh document types were fixed with the CLI. With active dogfooding the MCP server
  should be restarted after a merge.

## 4. Acceptance by goals

- **G1** ✓ — both pilots are set up, plan ready is non-empty, search finds by
  domain terms (M1 criteria from ROADMAP).
- **G2** ✓ — ingest/dedup/promote/stability + API v1 + MCP tools; the suite
  is green.
- **G3** ✓ — friction log 14 entries; the routine on the pilot worked
  (run_now ×2) **and a real cron-fire via OS-cron is confirmed**
  (probe routine, 16:30 tick, ADO-024).

## 5. Next step (next sprint)

- M2 per ROADMAP: the remaining friction items from ADO-005 (the top-3 slots are
  not selected — return to the log).
- F1/F2/F4 → tasks to the backlog (plan `adoption-2026-08`, section C/D).
- The Bearer gate on /api/v1 — is activated when the first remote caller appears
  (RFC 22 §3.3).
