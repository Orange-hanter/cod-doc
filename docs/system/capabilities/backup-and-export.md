---
type: capability
scope: backup-and-export
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-06-08
last_updated: 2026-06-08
related_docs:
  - ../DATA_MODEL.md
  - ../standards/revision-history.md
  - audit-and-ci.md
audience: [contributors, agents]
---

# Capability — Backup, Export & Recovery

> ⚠️ **Planned, not implemented (as of 2026-06-08).** This is a specification of a future
> capability (DOC-ME-2). The `cod-doc backup|restore|export` commands do not yet
> exist in the CLI; the document sets the target contract so that implementation and
> audit are agreed in advance.

## 1. Why

The DB (`.cod-doc/state.db`) is the source of truth for documents, tasks, links and
history (see [DATA_MODEL.md](../DATA_MODEL.md)). Markdown is a projection. So
data reliability = DB reliability, and three operations are needed: take a snapshot, restore
it, and export the data to a neutral format for migration to another storage.

## 2. Backup

`cod-doc backup --output state.tar.gz`

- Archives `.cod-doc/state.db` **plus** the projection-hash baseline (so that
  restoration can be checked against the accepted projection state).
- Includes the schema version (alembic revision), so `restore` can verify
  compatibility.
- Safe on a hot DB: the snapshot is taken via `VACUUM INTO` / SQLite backup,
  not by copying the file under load.

## 3. Restore

`cod-doc restore <archive>`

- Checks migration compatibility: if the archive's alembic-revision is newer/older
  than the current code — refuses with an instruction (apply migrations / update the package), rather than
  silent corruption.
- Restores the DB and checks the projection-hash: a mismatch → a
  `restore-drift` warning, not a silent markdown overwrite.
- Idempotent with respect to an already-matching state.

## 4. Export

`cod-doc export --format markdown|json|sqlite-dump`

An export for migration to another storage or external analysis:

- `markdown` — render all documents into a projection (a controlled operation, see
  the round-trip warning below).
- `json` — a structured dump of entities (documents/sections/tasks/links/
  revisions) for import into another system.
- `sqlite-dump` — a raw SQL dump of the schema and data.

## 5. Risks and limitations

- **Round-trip fidelity.** Closed in ADO-010 (finding F7 of the audit 2026-07-29):
  `import → export` is byte-identical for all 71 documents in `docs/` — frontmatter
  is re-emitted verbatim (`document.frontmatter_raw`), H1 is restored
  from `document.title`, `preamble` is separated from the first section. Re-serialization
  of frontmatter is enabled only when the DB has diverged from the file on
  `type/status/owner/sensitivity/source_of_truth/title`.
  Known normalizations (not documents, but service files): if there was no
  empty line after the closing `---`, one appears; a missing empty
  line after a section heading is also added. The previous checkpoint —
  [audit/2026-06-05-doc-drift-source-of-truth.md](../audit/2026-06-05-doc-drift-source-of-truth.md).
- **Overwrite protection.** `doc export` refuses to write over a file
  that matches neither the last export nor the last accepted
  import (a manual edit); over a foreign repository (on CLI/MCP); and —
  ADO-022 — over a file whose form the DB does not remember (`frontmatter_raw` /
  `title_in_body` = NULL on rows older than migration `0025_projection_fidelity`);
  and — ADO-015 — over a file whose `type:` in the DB was subverted by an old
  build's coercion (`frontmatter_json` names a type that the row does not store — exactly
  the rows that `0026_document_type_recoercion` fixes).
  `--dry-run` shows a unified diff, `--force-write` lifts all four
  protections.
- **Healing a legacy DB (ADO-022).** The third protection is lifted not by `--force-write`,
  but by `cod-doc doc backfill-projection --project <slug>` (MCP:
  `doc_backfill_projection`): it restores `frontmatter_raw` /
  `title_in_body` from files on disk and does not touch domain fields, so
  metadata edits made in the DB and not yet exported survive the operation —
  unlike `doc import`, which would apply the file's frontmatter back to the
  row. No file on disk → the row stays NULL (`file_missing`); the file is
  byte-for-byte equal to our last export → nothing to restore
  (`skipped`). A re-run is idempotent, `--dry-run` only counts.
- **Healing a legacy DB (ADO-015).** The fourth protection is lifted only
  by applying migrations (`cod-doc project init <slug>` / `alembic upgrade
  head`): `0026_document_type_recoercion` restores in `document.type`
  the authorial value from `frontmatter_json`. Until the migration is applied, the guard
  fires — updating the package without updating the DB has become dangerous precisely because
  ADO-015 made `capability`/`audit-report`/… stored and thus removed the former
  protection "an unknown value is emitted verbatim".
- **Schema compatibility.** `restore` without an alembic-revision check is forbidden.
- **Secrets.** A backup may contain `sensitivity: internal` content — store it
  as a secret, do not commit it to a public repository.

## 6. Relationship with CI

A regular `cod-doc backup` can be set up as a routine (cron) or CI-job; the artifact
is cached/uploaded the same way as described in
[audit-and-ci.md §4.4](audit-and-ci.md).
