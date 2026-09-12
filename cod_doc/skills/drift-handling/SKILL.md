---
name: drift-handling
description: |
  What to do on STALE / BROKEN / drift divergence: hash mismatch,
  edited_in_place, missing files. Triggers: drift, stale, broken, hash,
  mismatch, sync, sha, verify, projection_hash.
---

# Skill — Drift handling

## When it loads

Tasks / contexts where a divergence appears between the DB projection
and the file on disk, or a stale hash in a hybrid reference.
Trigger keywords: `drift`, `stale`, `broken`, `hash`, `mismatch`,
`sha:`, `verify`, `projection_hash`, `update_master_hashes`,
`check_stale_refs`.

## Canonical statuses (`doc_drift`)

| Status | What it means | What to do |
|--------|-------------|-----------|
| `in_sync` | DB content hash == projection_hash == file hash | nothing |
| `stale_export` | DB content changed relative to the last export projection | `doc.export(force=False)` or `force=True` after manual verification — but first check ADO-022 (below) |
| `edited_in_place` | The file was edited bypassing the revision-flow (file hash diverges from projection_hash) | reconciliation-flow: either `import_document`, or a manual merge with a recorded revision |
| `missing` | File deleted | if the doc was deleted — `doc.deprecate`; if accidental — restore from git history |

## Canonical statuses (`check_stale_refs`)

| Status | What to do |
|--------|-----------|
| `VALID` | OK |
| `STALE` | hash in `MASTER.md` is stale → `update_master_hashes` after content verification |
| `BROKEN` | file is missing on disk → recovery task via `task_create` |

## Algorithm on detecting drift

1. Read the current status via `doc_drift(doc_key)`.
2. If `stale_export` and the content in the DB is the source of truth →
   `doc_export`.
3. If `edited_in_place`:
   a. Read the file and DB-content.
   b. If the edits are **intentional** → `import_document(file_path)`
      (will apply the content to the DB + write a revision).
   c. If the edits are **accidental** → restore from the DB
      (`doc_export force=True`).
4. If `missing` → decide whether deleted or lost; depends on
   `last_updated` and git log.

## Old DB: first `doc_backfill_projection` (ADO-022)

If the project DB was set up before the `0025_projection_fidelity`
migration, documents have `frontmatter_raw` and `title_in_body` = NULL —
the DB does not remember how the file was structured. A bulk `doc_export`
on such a DB **rewrites frontmatter** (keys get reordered, a file without
frontmatter gets a fabricated `type/status/owner` block appended) and
adds an `# H1` that was not in the source. This is exactly how 107 of 121
files were once damaged.

- `doc_export` now refuses to write such a file itself. The message
  contains the words `frontmatter_raw` and `backfill`.
- The cure is `doc_backfill_projection(project)`: it restores the two
  form columns from files on disk. It does not touch domain fields, so
  metadata changes made in the DB and not yet exported (e.g.
  `doc_accept`) survive the operation.
- `force_write=true` on this error is **not a cure, but the corruption
  itself.** Apply it consciously and only when the file on disk is
  knowingly not needed.
- `doc import` also lifts the refusal, but applies the file's frontmatter
  back to the DB, i.e. rolls back unexported metadata edits. This is the
  second option, not the first.

Order on the pilot project: `doc_backfill_projection` → `doc_drift_all` →
only then `doc_export`.

## What NOT to do

- Do not run `update_master_hashes` "at random" — first make sure the
  content is valid; otherwise you will fix a broken state.
- Do not edit `projection_hash` by hand; this field is updated only by
  `doc_export`.
- Do not swallow `STALE` / `BROKEN` silently — raise a task via
  `task_create` with type `bug` or `chore`.

## Related

- [capabilities/doc-evolution.md](../../../docs/system/capabilities/doc-evolution.md)
- [services/projection_service](../../services/projection_service/)
- skill `validation` (write-path checks).
