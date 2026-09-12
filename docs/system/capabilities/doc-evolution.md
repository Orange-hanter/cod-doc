---
type: capability
scope: doc-evolution
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../standards/revision-history.md
  - ../standards/document-link.md
  - auto-linking.md
---

# Capability — Documentation Evolution

> Managed evolution of documents: creation, patching, renaming, merging, decomposition — without desynchronization from code and links.

## 1. The problem with the manual approach

In Restate:

- A new section of a module spec is added by editing a large file → conflicts during parallel work.
- Renaming a document (`Modules/M1 AUTH.md` → `Modules/M1 AUTH v2.md`) requires editing all incoming links.
- Decomposing a module (turning one file into a directory with `overview.md`, `domain.md`, …) is done by hand.
- The `last_updated`, `last_reviewed` fields are forgotten.

COD-DOC takes this on.

## 2. Operations

| Operation | Service | What it does atomically |
|----------|--------|---------------------|
| Create document | `DocService.create` | Skeleton by `type`, frontmatter, first revision |
| Patch section | `DocService.patch_section` | Diff, revision, re-index links, re-embed |
| Replace section entirely | `DocService.replace_section` | Same, but diff = full replace |
| Insert section | `DocService.insert_section` | Positioning by anchor, automatic anchors |
| Rename | `DocService.rename` | Cascade update of all `link.to_doc_key` |
| Decompose | `DocService.split_into_folder` | Splits into subdocuments, creates an entrypoint |
| Merge | `DocService.merge` | The reverse of split |
| Move to deprecated | `DocService.deprecate` | Updates frontmatter, sets `canonical_source` |

## 3. Skeleton templates

Each `type` has its own template. Templates are stored in `cod_doc/templates/` (like the current `MASTER.md.j2`), extended with:

- `module-spec.md.j2` — structure from the Restate `standards/module-spec.md`.
- `execution-plan.md.j2` — Navigation + Progress Overview + Gap Analysis + Next Batch.
- `task-section.md.j2` — `# Section X:` + back-link + separator.
- `user-story.md.j2` — persona, narrative, acceptance.
- `architecture.md.j2`, `standard.md.j2` — minimal.

`cod-doc doc new --type module-spec --module M1-auth` generates a document from the template and saves it to the DB.

## 4. Patch API

### 4.1 Via CLI

```bash
cod-doc doc patch modules/M1-auth/overview \
  --section "Data Model" \
  --from-file /tmp/new-data-model.md \
  --reason "Add account_status column"
```

### 4.2 Via MCP

```
→ doc.patch_section({
    doc_key: "modules/M1-auth/overview",
    anchor: "data-model",
    new_body: "...",
    reason: "Add account_status column"
  })
← { revision_id: "01HQX5Z9F0K8RNG6CB7VHQK4XX", updated_links: 3, reindex_queued: true }
```

## 5. Inline edits under patch-review

For large changes (usually by an agent) a proposal mode is available:

```
→ doc.propose_edit({ doc_key, patch, reason })
← { proposal_id, pending_approval: true }
→ doc.approve(proposal_id) | doc.reject(proposal_id)
```

This is needed when COD-DOC is in a team environment and not all agent edits go through directly. For solo mode you can enable auto-approve (`config.doc.auto_approve_from: ["agent:task-steward"]`).

## 6. Renaming and decomposition

### 6.1 Rename

```bash
cod-doc doc rename \
  modules/M1-auth \
  modules/M1-auth/overview \
  --reason "Decomposing into folder"
```

Atomically:

1. Moves the body/frontmatter to the new `doc_key`.
2. Deletes the old row **only** after recomputing links.
3. Generates a legacy-redirect document with `canonical_source = new_doc_key` if the `--keep-redirect` flag is set.

### 6.2 Split into folder

```bash
cod-doc doc split modules/M1-auth-v2 --into \
  overview,domain,contract,database,implementation,roadmap \
  --strategy by-heading
```

Cuts by H2, creates subdocuments, generates an entrypoint `modules/M1-auth/overview` with "Decomposition Status" + "Quick Links Table".

## 7. Keeping fields up to date

The service supports automatic fields:

- `last_updated` — written on every patch.
- `last_reviewed` — written by an explicit command `cod-doc doc review <doc> --by <owner>`.
- `implemented_in` — can be refreshed from the workspace-map (see [capabilities/auto-linking.md](auto-linking.md)).
- `affected_files` for linked tasks are updated when code files are renamed (if `code-tracking` is enabled).

## 8. Stale detection

`cod-doc audit --stale`:

- Finds documents with `status=active` and `last_updated > 180d`.
- For module specs: if `implemented_in` points to non-existent code — a `code-drift` warning.
- For task-plans: if the plan `last_updated` lags behind `max(last_updated)` of its tasks — an error "plan not synced".

## 9. Working with sections

Structural changes:

- `cod-doc section move <doc> <anchor> --after <other-anchor>` — reorders.
- `cod-doc section promote <doc> <anchor>` — raises the heading one level.
- `cod-doc section extract <doc> <anchor> --to <new-doc>` — moves a section into a new document, puts a link in its place.

All operations write a revision and update links.

## 10. Code drift detection

A side effect of the capability: if the frontmatter has `implemented_in: [restate-api/src/auth/]`, COD-DOC can:

- On every `cod-doc sync` check the existence of the directories.
- Overlay the frontmatter with the list of real subfolders (prompt: "the following new files are not mentioned in the spec…").
- Generate a `docs`-type task "Docs: document new files in M1 AUTH" with `affected_files = ...` for the unclosed difference.

The mechanics rely on [standards/task-plan.md §9 affected_files](../standards/task-plan.md) and close the doc ↔ code loop.

## 11. Example of a full cycle

1. The author asks the agent: "write up the billing module".
2. Agent: `doc.new(type="module-spec", module_id="M1-billing")` → skeleton.
3. Agent: several `doc.patch_section` — section by section.
4. For each section `ContextService.build` returns only "sufficient" context.
5. As structural facts are written, tasks are automatically created (`task.create` from Acceptance).
6. Via a link in MASTER — `link` resolves automatically.
7. The revision log of the document — a full history for review.

## 12. Relationship with manual editing

Manual markdown edits are supported, but:

- On import: parsing + diff vs current body + revision with `author: human:<login>`.
- Markdown not in the DB (a new file under `docs/system/`) is registered as `type=guide` with `last_updated = now`.
- Files with a `projection_hash` mismatch stay "pending approval" until `cod-doc doc accept <doc>`.
