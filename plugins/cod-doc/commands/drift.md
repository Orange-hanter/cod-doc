---
description: Check markdown ↔ cod-doc DB drift and fix edited_in_place via doc import
argument-hint: "[path to .md | --all] [--fix]"
---

Argument: `$ARGUMENTS` (empty = `--all`, without fixing).

Resolve the project slug as in `/cod-doc:status`.

**Check**

```
<cod-doc> doc drift -p <slug> --all --json      # or MCP ctx_drift / doc_drift_all
```

**Semantics — do not confuse**

| Status | Meaning | Action |
|---|---|---|
| `edited_in_place` | the file was edited on disk, the DB is behind | **defect** → `doc import` |
| `stale_export` | the DB is ahead of the on-disk projection | **norm** in files-are-source mode; do not touch |
| `missing` | the file is not on disk | investigate, do not recreate blindly |

**Fix** (only if `--fix` is in the argument)

For each `edited_in_place`:

```
<cod-doc> doc import <path> -p <slug>
```

If the edited file is part of the hash registry of the root `MASTER.md` — after
the import also `<cod-doc> hash update`, and then `doc import MASTER.md`,
otherwise the registry will diverge from the files.

Do not run `doc export` to disk — it is under a guard until byte-identical
round-trip.

Finale: a repeat `doc drift --all`, in the answer — before/after for each status
and the list of files that were imported. Do not import anything without `--fix`.
