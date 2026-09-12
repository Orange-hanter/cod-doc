---
type: audit-report
scope: sprint-h1-2026-08-29
status: active
owner: cod-doc core
created: 2026-08-29
audience: [next-session-agent, contributors]
related_docs:
  - ../roadmap/sprint-2026-08-29-hardening-m3-kickoff.md
  - ../roadmap/ROADMAP.md
  - 2026-08-29-contract-audit.md
---

# Audit — Sprint H1 "hardening + M3 kickoff" (2026-08-29 → 2026-09-05, closed early 2026-08-29)

## TL;DR

All three goals are closed in one day, plus 2 of 4 stretch tasks. 11 tasks done
(10 sprint ones + ADO-013 along the way), 11 commits `ae46936`…`7e2b9ec`. The suite
is green, drift 126/126 in_sync. The main strategic outcome — the M3 kickoff
(ADO-056): **all RFCs 16–21 are rejected** (none closes the M2 demand),
M3 is retargeted by the owner's decision to the remainder of the friction log (#8/#10/#11/#14).

## Deliverables

### G1 — audit criticals

- **ADO-035** (`147a1c5`) — the loopback guard `ensure_loopback_client` moved
  to `api/deps.py`, `PATCH /api/config` is protected; the test `tests/api/test_api_config_loopback.py`.
- **ADO-036** (`17cd6e0`) — the `Document` dataclass + repository mappings
  preserve `frontmatter_raw`/`title_in_body`/`content_sha256_head`; a round-trip
  test `tests/infra/test_document_repo_roundtrip.py`.
- **ADO-037** (`f340fc3`) — the owner's decision (task-doc 'design'): move to
  task_service. The legacy `/api/projects/{name}/tasks` (3 endpoints) work through
  `task_service`/`cod_doc/api/legacy_tasks.py`: Revision + the status machine +
  activity events, 409 without `state.db`; tests `tests/api/test_api_tasks_legacy_db.py`.

### G2 — high hardening

- **ADO-038** (`c59026e`) — `task_service.complete()` validates the transition
  with the status machine; for todo/pending — an explicit checkout leg; `cancelled`/`backlog`
  →done are rejected. Full slice services+api+integration at the time of the task:
  1199 passed.
- **ADO-055** (`a8baa34`) — the double `except: pass` in the update-path of import
  is replaced: a legal fallback only on `SectionNotFoundError`, other failures of
  patch/add section → `logger.warning` + `CoercedField` in `ImportReport.warnings`.
- **ADO-053** (`7a2ef96`) — the audience export is written to a derived path
  `<stem>.<audience>.md`; the canonical file and `projection_hash` are untouched,
  drift remains in_sync, re-import does not pull the redacted body. The change of the path
  contract is documented in the docstring `export_document`.
- **ADO-052** (`a5926ae`) — the MCP profile docs are synchronized with the code:
  default=`agent`, counts 6/20/107/111 (AGENTS.md §5.9, profiles.py, server.py
  help, mcp-integration.md); the smoke test `test_profile_counts_match_documented_values`
  catches future registry drift.

### G3 — M3 kickoff

- **ADO-056** (`955eb1b`) — verification of RFC 16–21 against the code (2026-08-29) + comparison
  with the M2 friction log (ADO-005 #8/#10/#11/#14) and the findings of the contract audit →
  the decision "all rejected", recorded in ROADMAP.md and task-doc 'acceptance';
  notes in `proposals/README.md`. **ADO-013 is closed by the same commit.**
  Owner's decision: M3 is retargeted to the remainder of the M2 friction log.

### Stretch

- **ADO-041** (`61873b9`) — `task_to_dict` in `cod_doc/services/serializers.py`;
  services no longer imports mcp; the AST gate `tests/services/test_services_layering.py`.
- **ADO-054** (`7e2b9ec`) — the routine `on_finding='create_task'` is implemented
  (a new task per finding, without dedup).
- ADO-039 (enforce checkout — a policy decision) and SYM-008 (Symbiosis phase 2)
  are consciously moved to the next sprint.

### Bookkeeping

- Verification of SYM-005/006 (at the start of the sprint, `ae46936`): both done, gap
  `cod-doc ctx` CLI → ADO-057.

## Findings

- **F1. M3-by-RFC did not happen as a concept.** The M2 friction log points to the
  import pipeline (#8 dry-run limit, #10 path for `*.txt`, #11 hidden directories,
  #14 frontmatter mapping), and the hackathon RFCs 16–20 were written for hypothetical
  vibecoders. The "feature by demand" rule worked as intended — there is no demand for them.
  Lesson: open new RFCs only with a link to friction entries.
- **F2. The contract findings of the audit turned out to be real bugs.** ADO-053
  and ADO-055 — both with a demonstration of data corruption on a red run (redacted
  body in the DB; silent loss of sections). The ai-reviewer pilot (ADO-034) paid off.
- **F3. Docs drift faster than code.** ADO-052: the default profile and counts
  got outdated in four places at the same time. The mitigation is accepted: the counts
  are pinned by a test, the failure-message lists all the edit locations.
- **F4. The FTS-savepoint in import_service** (the secondary part of finding ADO-055)
  is consciously left out of scope — a candidate for the backlog of the next sprint.

## Acceptance (Sprint DoD)

- [x] Each bug task G1/G2 has a regression test failing on main
      (the output of red runs — in the task-doc 'verification' of each task;
      pattern: `git stash push <fix>` → pytest → `git stash pop`).
- [x] ADO-037: the decision in task-doc 'design' before the code ("move to
      task_service" — the owner's decision).
- [x] ADO-056 + rejection of RFC 16–21 in `proposals/README.md` → ADO-013 done.
- [x] SYM-005/006: DB statuses ↔ code are verified 2026-08-29; gap → ADO-057.
- [x] All tasks went through `task_checkout` → `task_complete` with `commit_sha`;
      there are no dangling in-progress.
- [x] Each edit of a tracked `.md` is closed with `doc import` in the same commit;
      the final drift — 126/126 in_sync.
- [x] `ruff check` + `ruff format --check` + `mypy cod_doc/` + the full
      `pytest tests/ --timeout=120` — green on the last commit of the sprint.
- [x] The ratchet did not grow: `per-file-ignores` were not touched; new `# noqa` —
      zero; new `# type: ignore[no-untyped-def]` only by the existing idiom of test
      files (fixture/test signatures, like the neighboring tests in
      the same files); the only re-export is marked `as task_to_dict` —
      a re-export idiom, not noqa.
- [x] This audit report: status active, in the DB, with links to all commits.

## Next step

Sprint M3 (retargeted): tasks on the M2 friction log — #8 (dry-run limit),
#10 (path for `*.txt`), #11 (document the skip of hidden directories), #14
(mapping `diataxis`/`quadrant` → DocumentType); each fix is verified on the
pilot Orakul corpus (405 docs). Candidates for the same sprint: ADO-039
(enforce checkout — requires an owner's decision to enable), FTS-savepoint
(F4), ADO-057 (`cod-doc ctx` CLI), the stretch SYM-008.
