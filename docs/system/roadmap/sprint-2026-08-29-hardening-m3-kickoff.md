---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-29
last_updated: 2026-08-29
closed: 2026-08-29 (early)
audit: ../audit/2026-09-05-sprint-h1-hardening.md
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-28-m2-feedback-loop.md
  - ../audit/2026-09-11-sprint-m2-feedback-loop.md
  - ../audit/2026-08-29-contract-audit.md
---

# Sprint 2026-08-29 → 2026-09-05 — "H1: hardening + M3 kickoff"

> **Purpose.** Close the criticals and high findings of the contract audit
> (ADO-034) and run an analytical M3 kickoff — choosing a feature by demand
> without implementation. Owner decisions: "hardening + M3 kickoff" format,
> 1-week window (2026-08-29 → 2026-09-05).
>
> **Not source of truth.** Task statuses are in the DB (plan `adoption-2026-08`);
> priorities are in [ROADMAP.md](ROADMAP.md).

## 0. Ground truth at sprint start (reconciled 2026-08-29)

- M2 closed early: audit
  [2026-09-11-sprint-m2-feedback-loop.md](../audit/2026-09-11-sprint-m2-feedback-loop.md),
  commit `30c43f7`.
- Contract audit ADO-034 closed: report
  [2026-08-29-contract-audit.md](../audit/2026-08-29-contract-audit.md),
  commit `fd84f0b`; 21 tasks in backlog section D (ADO-035…055).
- Suite 1562 passed, drift 125/125 in_sync (CLI), git tree clean.
- Reconciliation of SYM-005/006 done 2026-08-29: **both tasks done** — code
  and tests cover acceptance (hub init, migrations 0026/0027/0028, dedup
  times_seen, 8 concurrent ingest, api/v1, MCP finding_*/ctx_*).
  The only gap — CLI `cod-doc ctx` — moved to ADO-057 (medium, backlog).
- Friction log ADO-005: open candidates #8 (dry-run limit of 50 lines),
  #10 (broken path for `*.txt`), #11 (hidden directories — docs), #14
  (Russian frontmatter / diataxis-quadrant) — input for G3.

## 1. Goals

- **G1 — Audit criticals.** ADO-035 (loopback guard for `PATCH /api/config`),
  ADO-036 (DocumentRepository round-trip: `frontmatter_raw`/`title_in_body`/
  `content_sha256_head`), ADO-037 (legacy `/api/projects/*` — the decision
  "delete vs move to services" goes in task-doc before implementation).
- **G2 — High hardening.** ADO-038 (`complete()` + `validate_transition`),
  ADO-055 (import does not silently drop sections), ADO-053 (audience-export
  does not write to the canonical path), ADO-052 (MCP profile docs ↔ code:
  default `agent`, counts 6/20/107/111).
- **G3 — M3 kickoff (analytics, no feature implementation).** ADO-056: choose
  a Track B RFC (16–20) based on the M2 friction log (#8/#10/#11/#14) and the
  contract audit; re-verify the "Current state" section of the chosen RFC
  against the code; record the rationale for the choice in ROADMAP; decide on
  `plan_create`. Rejecting stale RFCs → notes in `proposals/README.md`
  (closes ADO-013).
- **Bookkeeping:** reconcile the statuses of SYM-005/006 with the code
  (progress in code vs DB), correct the DB.

## 2. Task contracts

### G1 — criticals

- **ADO-035:** `PATCH /api/config` from a non-local address → 403; symmetry
  with `POST /settings` is covered by a test (`routes.py:49-55` currently
  without `ensure_loopback_client`).
- **ADO-036:** a round-trip through `DocumentRepository` preserves
  `frontmatter_raw`/`title_in_body`/`content_sha256_head` — the test reads
  the DB row directly, not the dataclass (`document_repo.py:23-66`).
- **ADO-037:** the decision (delete / move to services) is recorded in
  task-doc 'design' **before** code; if deletion — endpoints return 410/404
  and are removed from `web-frontend.md §3` (audit 0/0); if services — Revision +
  activity event + parity tests.

### G2 — high hardening

- **ADO-038:** `complete()` on a task in `cancelled`/`backlog` →
  StatusTransitionError; legal transitions (`in_progress`→done, etc.)
  keep working — a parameterized test over ALLOWED_TRANSITIONS
  (`task_service.py:516-550`).
- **ADO-055:** an artificially broken section is visible in
  `ImportReport.warnings`, the import neither crashes nor stays silent — a
  test (`import_service.py:461-488` double `except: pass`).
- **ADO-053:** after `export(audience=...)` `doc drift` stays in_sync and
  `doc import` of the same path does not change the canonical body — an
  integration test (`export.py:226-272`).
- **ADO-052:** a runtime measurement of counts is reproducible with one
  command (script in task-doc 'verification'), the numbers in
  AGENTS.md/profiles.py/server.py help match the measurement
  (agent=6/minimal=20/standard=107/full=111);
  `test_mcp_lists_tools` or a new smoke test catches a divergence between
  the registry and the profiles.

### G3 — M3 kickoff

- **ADO-056:** the RFC choice contains links to specific friction-log entries
  (ADO-005 journal) and/or contract-audit findings; the "Current state"
  section of the chosen RFC is re-verified against the code with a
  reconciliation date; the decision "implement / all rejected" is recorded in
  ROADMAP.md and task-doc 'acceptance'. Each rejected RFC from 16–21 is
  marked in `proposals/README.md` with a one-line reason.

### Stretch (subject to remaining pace)

- ADO-039 (enforce atomic checkout — record the Phase-2 decision).
- ADO-041 (agent_service ← mcp.tools._db import).
- ADO-054 (`on_finding='create_task'`).
- SYM-008 (E5-C loop) — only if reconciliation confirms it is unblocked.

### Out of scope

ADO-040 (large write-path wrapper — candidate for a separate sprint),
ADO-042…051, SYM-009/010/011, STB-023, ADO-014, M3 feature implementation.

## 3. Execution order

1. Setup: this sprint doc (doc create + import), pointer in ROADMAP.md, task
   ADO-056 in the DB, reconciliation of SYM-005/006.
2. G1: ADO-035 → ADO-036 → ADO-037 (each: checkout → fix + test →
   ruff/mypy/pytest → commit with confirmation → task_complete with sha).
3. G2: ADO-038 → ADO-055 → ADO-053 → ADO-052.
4. G3: ADO-056 — a research subagent over RFC 16–20 + friction log; record
   the choice in ROADMAP; close ADO-013 as part of the rejection pass.
5. Stretch in the listed order.
6. Final: full suite + ruff + mypy + drift; audit report
   `docs/system/audit/2026-09-05-sprint-h1-hardening.md`; checkboxes in
   ROADMAP.md; closing the sprint.

## 4. Risks

- **ADO-037 — the "delete" decision breaks someone's integrations:** legacy
  `/api/projects/*` is public; mitigation — first a task-doc with the owner's
  decision, a deprecation warning before removal.
- **ADO-036 may pull a desync of existing rows:** the fields are already in
  the schema (migration 0025), only the mappings change — no migration
  needed; if a desync is discovered — a separate task, do not inflate.
- **G3 may not find a worthy RFC** (friction points at importer/docs, not at
  RFC 16–20): then the honest outcome is "M3 rebuilt around friction", a
  note in ROADMAP, this is not a sprint failure.
- **Pace:** previous sprints closed in 1–2 days; the one-week window has
  margin, the stretch is realistic.

## 5. Definition of Done

Each item is verified by a single command/artifact — not "done", but "proven".

- [x] Each bug task in G1/G2 has a **regression test that fails on main
      without the fix** (output of `pytest <test>` on the commit before the
      fix — in task-doc 'verification').
- [x] ADO-037: decision in task-doc 'design' before code (see the contract
      above).
- [x] ADO-056 + rejection of RFC 16–21 in `proposals/README.md` (closes
      ADO-013).
- [x] SYM-005/006: statuses in the DB match the code (finding_tools.py and
      migrations 0027/0028 in main ↔ task statuses) — reconciliation
      2026-08-29, both done, gap `cod-doc ctx` → ADO-057.
- [x] All sprint tasks went through `task_checkout` → `task_complete` with
      `commit_sha`; the history has no "dangling" in-progress.
- [x] Each edit of a tracked `.md` is closed by `doc import` in the same
      commit; the final `doc drift --all` is 100% in_sync (126/126).
- [x] `ruff check` + `ruff format --check` + `mypy cod_doc/` +
      `pytest tests/ --timeout=120` — green on the last commit of the sprint.
- [x] The ratchet did not grow: `pyproject.toml [per-file-ignores]` — no more
      lines than at the start; new `# noqa`/`# type: ignore` without a
      justification comment — zero (grep check over the sprint diff).
- [x] Audit report `docs/system/audit/2026-09-05-sprint-h1-hardening.md` —
      status active, in the DB, with links to all sprint commits.
