# Reference — Hybrid references

> Loaded additionally when working with MASTER.md, hash verification,
> drift resolution.

## 1. Format

```
📁 /path/to/file.ext | 🗃️ doc:sanitized_path | 🔑 sha:12hexchars
```

Three components, separated by ` | `:

| Part | Meaning |
|-------|------------|
| `📁 path` | Absolute or root-relative path of the file on disk. Must exist. |
| `🗃️ doc:KEY` | Stable doc_key in the DB (normalized path, `/` → `_`). |
| `🔑 sha:12hex` | First 12 hex of SHA-256 of the file contents. Matches `hash_file()`. |

## 2. Statuses

| Status | Meaning |
|--------|----------|
| `🟢 VERIFIED` | All three components agree: file on disk → hash matches the recorded one → doc_key is bound in the DB. |
| `🟡 DRAFT` | The document is created, but has not passed validation yet (typically newly authored). |
| `🟡 LEGACY` | The document is correct in content, but moved to legacy status with a `canonical_source`. |
| `🔴 STALE` | The file on disk changed, the hash in the reference is stale. Needs `update_master_hashes`. |
| `🔴 BROKEN` | The file is missing on disk or the doc_key is not found in the DB. Needs recovery via `task_create`. |

## 3. Edge cases

### 3.1 Doc-key normalization

`docs/system/MASTER.md` → `doc:docs_system_MASTER_md` (underscores instead
of `/` and `.`). Case is preserved.

### 3.2 Hash mismatch on a human edit

If the file was edited outside the revision-flow and `check_stale_refs`
shows `stale_export` → run `update_master_hashes` after manual
verification that the content is valid.

### 3.3 Multiple references to one doc_key

Allowed: one reference from `MASTER.md`, another — from a section of
another doc. On rename — `link_service.rename_cascade` updates all.

## 4. What is NOT a reference

A markdown-relative `[label](path/to.md)` without the trio is a
**markdown-link**, not a hybrid reference. It is resolved via
`link_service`, not via `check_stale_refs`.
