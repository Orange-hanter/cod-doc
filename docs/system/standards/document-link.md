---
type: standard
scope: document-link
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../capabilities/auto-linking.md
  - ../DATA_MODEL.md
---

# Document Link Standard

> The format of links in the markdown projection and the rules for resolving them in the DB.

## 1. Supported forms

| Form | Example | When |
|-------|--------|-------|
| **Canonical ref** | `[[doc:modules/M1-auth/overview]]` | Preferred; independent of real paths |
| **Wiki-link** | `[[M1 AUTH v2]]` | For compatibility with Obsidian / Restate |
| **Markdown relative** | `[overview](../modules/M1-auth/overview.md)` | For compatibility with the GitHub viewer |
| **Task ref** | `[[task:AUTH-025]]` | A hard link to a task |
| **Story ref** | `[[story:US-014]]` | A hard link to a story |
| **Section ref** | `[[doc:modules/M1-auth/overview#data-model]]` | A link to a specific anchor |
| **URL** | `https://...` | External; not resolved by COD-DOC, only reachability check (optional) |

## 2. Canonical ref — the preferred format

`[[doc:<doc_key>]]`, where `doc_key` is taken from the DB (`document.doc_key`) and does not depend on the real path in the projection.

- Renamed a document — `doc_key` does not necessarily change (the meaning does not change), and if it does, COD-DOC atomically updates all incoming links.
- A new export generates the canonical ref as a relative-markdown link for readability on GitHub.

## 3. Resolving

The `LinkService.resolve(link)` service:
1. **Canonical ref** → direct lookup of `document` by `doc_key`.
2. **Wiki-link** → lookup by `title` / `doc_key` (exact match), with a fallback to fuzzy (Levenshtein ≤ 2) and a warning.
3. **Markdown relative** → path normalization → lookup by `document.path`.
4. **Task / Story ref** → lookup by `task_id` / `story_id`.

The result is written to `link.resolved` + `link.to_doc_key` / `to_task_id` / `to_story_id`.

## 4. Broken links

A link is considered broken if:

- The target is not found.
- The target has `status=deprecated` and no `canonical_source`.
- The target is a section-ref to a non-existent anchor.

A broken link is recorded with `broken_reason`. It is not deleted.

Policy:

- Hard error on the write-path if the author explicitly added a new broken link.
- Warning in `cod-doc audit` if the link broke after refactoring the target document (a rare case, should be caught by auto-linking).

## 5. Update on rename

When `DocService.rename(doc_key, new_doc_key)`:
1. All `link.to_doc_key` are updated transactionally.
2. The markdown projection is regenerated for all affected documents.
3. One `revision` is written per section with a changed body.

This is one transaction. No manual edits are required.

## 6. Section anchors

- The anchor is kebab-case from the heading (`## Data Model` → `data-model`).
- Duplicates are auto-disambiguated: `data-model`, `data-model-1`, …
- Stored in `section.anchor`; exported to markdown as an explicit `<a id="..."></a>` is not needed — GitHub and Obsidian readers know how to handle it.

## 7. External URLs

- Not resolved automatically (no network requests on the write-path).
- On demand `cod-doc link verify --external` — a background HTTP-status check. The result is written to `link.last_checked` / `link.broken_reason`.

## 8. Cross-project links

When project A needs a link to a document of project B (managed by the same COD-DOC):

```
[[doc:project:restate/modules/M1-auth/overview]]
```

Resolved via a query to the `document` table with `project_id` by slug.

## 9. Forbidden practices

- Hard-coded absolute paths (`/Users/...`) → error.
- Long chains of `../../../` → warning (suggest a canonical ref).
- Plaintext mentions of documents without links → a warning on audit (auto-linking can suggest turning it into a link, see [capabilities/auto-linking.md](../capabilities/auto-linking.md)).

## 10. Storage

All links are in the `link` table (see [DATA_MODEL.md §3.4](../DATA_MODEL.md)). A link belongs to a `section`, not a `document`, to know the location precisely.

## 11. Examples

```markdown
See [[doc:modules/M1-auth/overview]] and task [[task:AUTH-025]].

Story [[story:US-014]] is fully covered by section
[[doc:modules/M1-auth/overview#api-specification]].

GitHub-friendly form (generated for the projection):
see [Auth Overview](../modules/M1-auth/overview.md) and [AUTH-025](../plans/M1-auth/tasks/section-c-lifecycle.md#auth-025).
```

## 12. Transclusion (`![[...]]`) and interaction with the index (DOC-ME-5)

Transclusion is an insertion-link: `![[doc:<doc_key>]]` or `![[doc:<doc_key>#<anchor>]]`.
Semantically it is a **link**, not a copy: the target's body is not duplicated in the source,
but substituted at the render/export stage.

Rules:

- **Storage.** Transclusion lives in the `link` table as an ordinary link with the flag
  `transclude=true` (not a separate copy of the text). The source of truth for the body is
  always the target document.
- **Render / export.** On projection, `![[doc:key#anchor]]` expands into the body of the
  corresponding target section (with a source banner). Cycles (A includes B
  includes A) break on the first repeat with a warning `LK-008`.
- **Indexing (ContextService / embeddings).** Only the **source of truth** is indexed —
  the target section. The source that transcludes it **does not**
  re-index the embedded text: otherwise the same content would enter the index
  twice and distort similarity. So for search/context, transclusion is transparent —
  the original is found, not its insertions.
- **Drift.** Since the body is not copied, transclusion does not create projection-drift:
  a change to the target is automatically reflected in the source on the next render.

The difference from an ordinary link: `[[...]]` leads to the target (navigation), `![[...]]`
embeds its body (composition), but both are links, both are resolved via `link`.
