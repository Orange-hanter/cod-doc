---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-30
last_updated: 2026-08-30
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-29-hardening-m3-kickoff.md
  - ../audit/2026-09-05-sprint-h1-hardening.md
  - ../audit/2026-09-11-sprint-m2-feedback-loop.md
---

# Sprint 2026-08-30 → 2026-09-06 — "M3: friction-log leftovers"

> **Purpose.** Close the leftovers of the ADO-005 friction log — entries
> **#8 / #10 / #11 / #14** (findings F1/F2/F3/F4 of the M2 audit). The
> decision to retarget M3 from RFC 16–21 to the friction log was made by
> the owner in sprint H1 (ADO-056) and recorded in the H1 audit report.
> Window: 1 week (2026-08-30 → 2026-09-06).
>
> **Not source of truth.** Task statuses are in the DB (plan `adoption-2026-08`);
> priorities are in [ROADMAP.md](ROADMAP.md).

## 0. Ground truth at sprint start (reconciled 2026-08-30)

- H1 closed early: audit
  [2026-09-05-sprint-h1-hardening.md](../audit/2026-09-05-sprint-h1-hardening.md),
  final commit `672659e`.
- Suite 1585 passed (`pytest tests/ --timeout=120`), mypy clean (317 files),
  drift 127/127 in_sync, git tree clean.
- Ratchet baseline: `pyproject.toml [per-file-ignores]` — 6 entries.
- Friction log ADO-005: exactly #8 (dry-run limit of 50 lines),
  #10 (broken path for `*.txt` → drift missing), #11 (hidden directories —
  docs), #14 (foreign frontmatter `type:` → silent substitution in module-spec)
  remain open.
- Stretch from H1: ADO-039 (enforce atomic checkout, high, section D) and
  SYM-008 (E5-C loop) — both tasks are already in the DB, no new ones created.

## 1. Goals

- **G1 — bug #10.** The importer normalizes `path` for non-md files
  (`.txt/.rst/.markdown`): drift no longer reports them as `missing`.
- **G2 — UX #8.** `--dry-run` of import: the full list of candidates is
  available (`--limit` / output to a file); the default preview limit of 50
  is preserved with an explicit tail hint.
- **G3 — importer discipline #14 + #11.** Mapping of foreign frontmatter
  `type:` (diataxis/quadrant) to DocumentType with a warning instead of a
  silent substitution (analog of ADO-015); skipping hidden directories is
  documented and visible in dry-run.
- **Stretch (subject to remaining pace):** ADO-039, SYM-008.
- **Out of scope:** ADO-057 (ctx CLI), ADO-040 (large write-path wrapper),
  ADO-042…051, implementation of Track B RFC features.

## 2. Task contracts

Code bindings reconciled 2026-08-30.

- **#10 (bug, high):** after `import docs`, for `.txt/.rst/.markdown`
  `Document.path` points to the real file, not `<doc_key>.md`;
  `doc drift` → in_sync. Root cause: `_doc_key_for` strips the extension
  (`cod_doc/services/restate_importer.py:195-200`), and path is assembled
  as `<doc_key>.md` by default. The regression test fails on main without
  the fix (red run recorded in task-doc 'verification').
- **#8 (feature, high):** `cod-doc import docs --dry-run --limit N`
  (0 = no limit) and/or `--output FILE` for the full list; default 50 +
  tail line "… and K more (use --limit 0)". Code: `_DRY_RUN_PREVIEW_LIMIT`
  (`cod_doc/cli/cmd_import.py:25`). Test on a synthetic corpus of >50
  files.
- **#14 (bug, medium):** a mapping table for foreign `type:` values
  (observed in pilots: diataxis/quadrant) → `DocumentType` in
  `import_service`; an unknown type produces a warning in the import
  payload, not a silent substitution in `module-spec`. Code:
  `_enum_or_default` (`cod_doc/services/import_service.py:140-147`).
  Precedent: ADO-015 (`f77bd75`). Regression test with a red run.
- **#11 (docs, medium):** skipping hidden directories is documented
  (skill `project-onboarding` + import help); dry-run prints a
  counter/list of skipped hidden dirs. Code: `_SKIP_DIRS` and the
  `p.startswith(".")` filter (`restate_importer.py:52-56,187`).
- **Stretch ADO-039:** the Phase-2 enforce decision is recorded in task-doc
  'design' before coding; the todo→in_progress transition happens only via
  `task_checkout` on all surfaces (MCP/web/CLI); AGENTS.md §5.3 is in sync
  with the code.
- **Stretch SYM-008:** only if reconciliation confirms it is unblocked.

## 3. Execution order

1. Setup: this sprint doc (doc create + import), pointer in
   ROADMAP.md, tasks #10/#8/#14/#11 in the DB (section C — precedent
   ADO-030…033).
2. G1: friction #10 (bug → red run first).
3. G2: friction #8.
4. G3: friction #14 (bug → red run) → #11.
5. Stretch: ADO-039 → SYM-008.
6. Final: full suite + ruff + mypy + drift; audit report
   `docs/system/audit/2026-09-06-sprint-m3-friction.md`; DoD checkboxes;
   closing the sprint.

## 4. Risks

- **#10 may reveal desync of existing txt rows in the DB:** backfilling
  the path for already-imported records is a separate item of the task;
  we do not inflate the scope.
- **#14 — risk of skewing the mapping toward a foreign standard:** we map
  only observed values; anything unknown is a warning, not a guess.
- **Pace:** H1 closed in a day; the one-week window has plenty of margin,
  the stretch is realistic.

## 5. Definition of Done

Each item is verified by a single command/artifact — not "done", but
"proven".

- [x] Each bug task (#10, #14) has a regression test that fails on main
      without the fix; the red run is recorded in task-doc 'verification'.
- [x] All sprint tasks went through `task_checkout` → `task_complete` with
      `commit_sha`; no "dangling" in-progress items.
- [x] Each edit of a tracked `.md` is closed by `doc import` in the same
      commit; the final `doc drift --all` is 100% in_sync.
- [x] `ruff check` + `ruff format --check` + `mypy cod_doc/` +
      `pytest tests/ --timeout=120` — green on the last commit.
- [x] The ratchet did not grow: `per-file-ignores` ≤ 6 entries; new
      `# noqa`/`# type: ignore` without justification — zero (the test-file
      idiom `no-untyped-def` is allowed).
- [x] Friction log ADO-005: 0 open entries.
- [x] Audit report `2026-09-06-sprint-m3-friction.md` — status active,
      in the DB, with links to all sprint commits.
