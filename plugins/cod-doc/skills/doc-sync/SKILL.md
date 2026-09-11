---
name: doc-sync
description: |
  Markdown ↔ DB sync in cod-doc: doc import after editing a file, hash
  update for the MASTER.md registry, drift semantics, registering a new
  document. Triggers: drift, doc import, edited_in_place, stale_export,
  MASTER.md, hash, registry, editing docs, document not visible in the DB.
---

# Doc sync — markdown is a projection, the truth is in the DB

Editing a tracked `.md` on disk leaves the DB behind → drift
`edited_in_place`. The plugin's PostToolUse hook will remind you (it
checks the file against the `document` table, so it stays silent on
untracked files); then — manually.

Project slug: `cod-doc project list` or
`sqlite3 -readonly .cod-doc/state.db "select slug, root_path from project"`.

## Editing an existing document

Prefer MCP when the tools are live:

- `doc_import(project, path)` — frontmatter + body → DB
- `doc_drift_all(project)` — check: `edited_in_place == 0`

CLI fallback (no MCP session, or scripts/CI):

```bash
cod-doc doc import <file.md> -p <slug>    # frontmatter + body → DB
cod-doc doc drift -p <slug> --all         # check: edited_in_place == 0
```

If the file is in the hash registry of the root `MASTER.md`:

MCP: `hash_update(project)` then `doc_import(project, "MASTER.md")`.

CLI fallback:

```bash
cod-doc hash update                       # recompute the registry (edits MASTER.md)
cod-doc doc import MASTER.md -p <slug>    # and MASTER.md itself — into the DB too
```

## Drift semantics

| Status | Meaning | Action |
|---|---|---|
| `edited_in_place` | file edited, DB lagged | **defect** → `doc import` |
| `stale_export` | DB is fresher, the on-disk projection is older | **normal** in files-are-source; do not touch |
| `missing` | no file | investigate; do not recreate blindly |

`doc export` to disk is under a guard until byte-identical round-trip; do
not export outward.

## New document → register in the DB

The CLI `doc import` only works on already-known keys. A new file is
registered by the service:

```python
from cod_doc.domain.entities import DocumentType
from cod_doc.services import import_service
# inside transactional(sf) as s:
import_service.import_markdown(
    s, project_id=<id>, doc_key="docs/system/new-document",   # path without .md
    raw_markdown=raw, fallback_title="…",
    fallback_type=DocumentType.MODULE_SPEC,
    author="claude-x", reason="registration")
```

A repeated `import_markdown` of the same key fails — for updates there
is `import_or_update_markdown` (this is what `doc import` calls).

Bulk registration of a new documentation tree — `cod-doc import docs -p
<slug> --dry-run` first, and only after reviewing the plan — without
`--dry-run`.

## Do not forget

- The root `MASTER.md` is regen-on-write: if you edited it, import it.
- Numbers in the docs (tool counters, document counters) are guarded by
  the project's anti-drift tests: editing one side — edit both.
- Tests with CLI output run with `env -u FORCE_COLOR`: rich colors the
  output inside `CliRunner`, string asserts fail on ANSI codes.
