---
title: Documentation Drift and Source-of-Truth Checkpoint
type: execution-log
status: active
source_of_truth: true
sensitivity: internal
owner: cod-doc core
created: 2026-06-05
last_updated: 2026-06-06
scope: documentation-drift / db-source-of-truth
audience: [contributors, next-session-agent]
---

# Documentation Drift and Source-of-Truth Checkpoint

## TL;DR

The documentation drift pass on 2026-06-05 made the project database the
accepted structured source of truth for the current documentation set.

The safe model is not “throw markdown away”. The safe model is:

- Markdown files remain the human-editable projection and import input.
- The SQLite database is the structured source of truth for documents, sections,
  frontmatter, links, status, sensitivity, owners, and drift baselines.
- Projection drift is checked against both DB-rendered content and the accepted
  imported file hash, because the current renderer is not yet fully
  round-trip-compatible with the repository markdown.

## What Changed

- Project root in `.cod-doc/state.db` was aligned to the real workspace path.
- Markdown documentation was re-imported into the DB as 105 documents and 895
  sections before this checkpoint file.
- Existing document rows now sync document-level metadata from frontmatter on
  update, not only section bodies.
- Imported YAML frontmatter values are normalized into JSON-storable values.
- `source_of_truth` frontmatter is respected by `doc_service.create`.
- Generated package metadata under `cod_doc.egg-info` is excluded from document
  import.
- Link backfill now parses and resolves links in one pass.
- Drift detection now accepts an imported file hash baseline when DB-rendered
  markdown is semantically current but not byte-identical to the source file.

## Current Verification

As of the 2026-06-06 history-backed cleanup:

- The DB contains 106 documents and 902 sections.
- The link graph contains 607 links and 0 unresolved links.
- The project row in SQLite is `cod-doc`, rooted at
  `/Users/dakh/Git/_my/cod-doc`.
- The global CLI registry still contains an old `integration-test` entry, but
  `Config.get_project` now falls back to the nearest workspace `.cod-doc/state.db`.
  `cod-doc link backfill --project cod-doc` succeeds from the repo root.
- `cod-doc doc drift --project cod-doc --all` reports 106 `in_sync` docs and
  0 `stale_export` / `edited_in_place` / `missing` docs.
- The DB now has an enabled `doc_drift_daily` routine (`check_name=doc_drift`,
  `cron="0 0 * * *"`); its first manual run completed with 0 findings.
- The Web project overview surfaces the latest `doc_drift_daily` run as a
  `Doc drift` health badge that links to the routine card.
- `GET /api/projects/<slug>/health` exposes a compact JSON health payload for
  automation consumers: current project-wide doc drift, unresolved link count,
  and latest `doc_drift` routine status.
- `routine_service.tick()` now normalizes SQLite naive datetimes to UTC before
  comparing intervals, so scheduled routines do not fail on timezone arithmetic.

## Link Hygiene Pass 2026-06-06

The first follow-up pass reduced unresolved link noise from 191 to 67 without
hiding the useful failures. The later ADR history pass reduced it further to
31 after inline-code spans were excluded from link parsing.

Changes:

- Placeholder examples such as `KEY`, `ID`, `path`, `url`, and `...` are no
  longer inserted into the link graph.
- Markdown links to existing directories or files are accepted as resolved
  filesystem references when they are not DB documents.
- Code references with GitHub-style line fragments such as `#L10-L20` are
  resolved by checking line ranges instead of searching for a symbol named
  `L10-L20`.
- Python module refs that moved from `module.py` to `module/__init__.py` are
  accepted as package refs.
- Inline code spans are excluded from link parsing, matching fenced code
  behavior.

Second pass result:

- `docs/system/adr-vision.html` style relative code refs now resolve from the
  source document's directory, not only from repository root.
- Example/future ADR IDs were rewritten as `ADR-NNN` placeholders or inline code.
- ADR-system task IDs `ADR-001`..`ADR-008` are called out as task IDs where the
  prose needs that distinction.
- Historical links to removed files such as `cod_doc/api/web/db_resolver.py` and
  pre-split service test modules were preserved as historical text or redirected
  to current focused files.
- External memory refs such as `validation_pattern.md` were kept as historical
  memory references, not repository markdown links.

## ADR History Pass 2026-06-06

Git history shows that commit `5cf35ab` introduced `adr_migrator.py` to parse
legacy ADR tables from `arch/architecture.md` into DB rows. The current DB had
only `ADR-001`, and that row is a newer proposed decision named "LLM
Cross-Session Memory Layer", not the legacy "Многослойная архитектура с DIP".

Applied recovery:

- Ran the existing legacy migrator against `arch/architecture.md`.
- Created accepted ADR rows `ADR-002`..`ADR-005`.
- Skipped `ADR-001` because the ID is already occupied by a different DB row.
- Rebuilt link graph after recovery.
- Reconciled the `ADR-001` conflict by renumbering the live "LLM Cross-Session
  Memory Layer" row to `ADR-009`, preserving its row id, creation timestamp,
  author, context, and decision.
- Created the legacy accepted `ADR-001` ("Многослойная архитектура с DIP") via
  the same git-history migrator.
- Wrote an ADR revision and `adr.renumbered` activity event for the
  `ADR-001` -> `ADR-009` move, including the git-history rationale.

The ADR-system execution plan also uses task IDs `ADR-001`..`ADR-008`. To avoid
turning task refs into fake decision records, bare `ADR-NNN` tokens inside
execution-plan documents are treated as task links when a matching task exists.
Explicit ADR forms such as `[[adr:ADR-007]]` still remain ADR refs.

ADR gap resolution:

- `ADR-007` and `ADR-008` prose refs were rewritten where git history showed
  them as ADR-system task IDs or examples rather than decision records.
- Future/example IDs such as `ADR-012` were rewritten as `ADR-NNN` placeholders.
- `ADR-001` no longer has an ID conflict; it now resolves to the legacy
  architecture decision, while the LLM-memory proposal lives at `ADR-009`.

## Source-of-Truth Decision

The database can be the source of truth for automation only if import, export,
drift, and audit remain aligned. This pass fixed the immediate gaps that made
that unsafe:

- Existing imports previously did not refresh frontmatter metadata.
- Link backfill previously stored parse rows without resolving them.
- Drift detection treated every non-round-trip markdown file as dirty.
- Generated `egg-info` markdown was treated as project documentation.
- Project-wide drift monitoring now exists as
  `cod-doc doc drift --project <slug> --all`, with text and JSON output.
- The same project-wide summary is the shared service path for CLI, MCP
  `doc_drift_all`, and the `doc_drift` routine.

The remaining risk is renderer fidelity. Until markdown export is
round-trip-aware, bulk export should be treated as a controlled operation, not a
casual cleanup command.

## Next Backlog

1. Decide whether `modules/M1-auth/overview` and `AUTH-025` should stay as
   examples, become ignored sample refs, or be replaced with real COD-DOC refs
   if they reappear in future imports.
2. Improve markdown renderer round-trip fidelity before any mass export.
3. Improve the project health payload with optional routine history and richer
   per-issue remediation hints once downstream automation starts consuming it.
