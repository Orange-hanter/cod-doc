---
type: capability
scope: auto-linking
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../standards/document-link.md
  - ../DATA_MODEL.md
---

# Capability — Auto-Linking

> Finding, resolving and maintaining links without manual work.

## 1. What is automated

### 1.1 Resolving existing links
Any link in the markdown body of a section is parsed and resolved against the DB. The result goes into `link` (see [DATA_MODEL.md §3.4](../DATA_MODEL.md)).

### 1.2 Discovering potential-links
When a module / task / document is mentioned in the text without an explicit link, COD-DOC offers to turn the mention into a link.

Patterns:

| Pattern | Interpretation |
|---------|-----------|
| `AUTH-025` outside code | → `[[task:AUTH-025]]` |
| `M1 AUTH` / `M1-auth` in text | → `[[doc:modules/M1-auth/overview]]` |
| `US-014` outside code | → `[[story:US-014]]` |
| Document name (by `title`) | → canonical ref candidate |

Suggestions are shown in `cod-doc audit --linkable`, but are not applied automatically without confirmation.

### 1.3 Support on rename
Rename of a document / task — all incoming links are updated atomically (see [standards/document-link.md §5](../standards/document-link.md)).

### 1.4 Broken-link verification
A regular job `cod-doc link verify`:

- Walks the entire `link` table.
- Internal links — tries to re-resolve.
- External URLs (with the `--external` flag) — HTTP HEAD with a timeout.
- Result in `link.resolved` / `link.broken_reason` / `link.last_checked`.

## 2. Indexing

On every patch `DocService`:

1. Extracts links from the body via a markdown-AST (remark) + regex for wiki/canonical links.
2. Diff vs the previous set → insert/update/delete in `link`.
3. Resolves new links via `LinkService.resolve`.
4. Caches the result.

## 3. The resolving algorithm

```text
INPUT: raw_link, from_section
1. Parse the form (canonical|wiki|markdown|task|story|url)
2. By form → strategy:
   canonical → doc_key exact
   wiki      → doc_key exact → title exact → fuzzy (Lev ≤ 2) → None
   markdown  → normalize(from_section.path, raw.path) → document.path exact → None
   task      → task_id exact → None
   story     → story_id exact → None
   url       → skip (verify only on request)
3. If resolved → set link.resolved=1 and target fields
4. Otherwise → link.resolved=0, link.broken_reason="not-found"
```

Fuzzy-match requires confirmation: on import — a warning; on auto-suggestion — shown as a candidate.

## 4. Cascade update on rename

The procedure:

```python
def rename(doc, new_doc_key):
    with tx():
        old_key = doc.doc_key
        doc.doc_key = new_doc_key
        # update incoming links
        for link in Link.query.filter_by(to_doc_key=old_key):
            link.to_doc_key = new_doc_key
            affected_sections.add(link.from_section)
        # revision on the document
        Revision.create(entity_kind='document', entity_id=doc.row_id,
                        diff=frontmatter_diff, reason=f"rename {old_key}→{new_doc_key}")
        # revision on each affected section (body does not change,
        # but the canonical-ref in the render is different — hence a projection diff)
        for section in affected_sections:
            refresh_projection(section)
```

Gotcha: if an incoming link was written as markdown-relative, the body text is also updated (because the path changed) — that is already a full diff.

## 5. Graph queries

Ready-made queries on the `link` table:

- **Backlinks** (`cod-doc link incoming <doc>`) — who links to me.
- **Outgoing** (`cod-doc link outgoing <doc>`) — where I link to.
- **Orphan documents** (`cod-doc audit --orphans`) — `source_of_truth=true`, but 0 incoming (except root MASTER and NAVIGATION).
- **Doc cluster** (`cod-doc graph cluster --around <doc>`) — BFS over `link` up to depth `N`.

### 5.1 Documentation graph (DOC-ME-1) — 🟡 planned

> ⚠️ **Planned, not implemented (as of 2026-06-08).** Specification of a future command;
> currently only the ADR-graph is available (`cod-doc adr graph`).

`cod-doc graph documentation --format mermaid|dot` — a generated analogue of
(Restate) `Documentation Graph.md`: renders the `link` graph between documents in
Mermaid or Graphviz/DOT for insertion into an overview or a CI artifact. Coverage levels:

- `--scope full` — the whole project (nodes = documents, edges = resolved links).
- `--scope module <prefix>` — the subtree of one module (`modules/M1-auth/*`).
- `--scope hottest --top N` — N documents with the most incoming links
  (ranking by `link.to_doc_key`), to see the "centers of gravity" of the documentation.

The data source is the same `link` table as the graph-queries above; the command
is read-only and does not write to the DB.

## 6. MCP surface

| Tool | Operation |
|------|----------|
| `link.list_broken` | List of broken links with reasons |
| `link.incoming` | Backlinks |
| `link.outgoing` | Forward links |
| `link.suggest` | For a piece of text, return potential auto-links |

## 7. No silent auto-replacement

COD-DOC **does not turn phrases into links without confirmation**. Argument: a false positive (mentioning "auth" in a general sense) creates junk links. Auto-replacement is done only:

- On an explicit command `cod-doc link autofix <doc>`.
- Via MCP `link.apply_suggestions(ids=[...])`.

## 8. Validation on write

The write-path for `DocService.patch_section` includes a hard-check:

- A new link that does not resolve, but is explicitly set by the author → error (with the message "target document does not exist; create or fix it").
- A link to a task that is not in the DB → error.
- A link to an anchor that does not exist → error.

This is the main safeguard against "silent documentation decay".

## 9. Handling Obsidian specifics

- `[[Document Name]]` is parsed as a wiki-link.
- `![[Document Name]]` (transclusion) — supported on export: rendered as a quote from the target document.
- Aliases (`[[Doc|alias]]`) — preserved on export, not lost on rename.

## 10. Integration with code-links

Besides doc-links, `related_code` / `implemented_in` frontmatter — these are links to code. COD-DOC:

- Checks the existence of paths during `audit`.
- Sets a `code-drift` warning if the ref is stale.
- Can update the array from the workspace-map (analogous to the Restate `Docs/workspace-map.yaml`) if the code-link is given as a prefix.

## 11. Performance

- The `link` table is indexed by `to_doc_key`, `to_task_id`, `resolved`.
- A full `link verify` on a Restate-level project (~500 docs, ~4000 links) fits in a few seconds — this is pure SQL.
- External URLs — separately and asynchronously, does not block the write-path.
