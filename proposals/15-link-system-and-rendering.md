---
status: implemented
type: tech-proposal
author: human:dakh
date: 2026-05-06
scope: web-ui · link-service · markdown-renderer · semantic-backfill
related:
  - cod_doc/api/web/markdown.py
  - cod_doc/services/link_service/__init__.py
  - cod_doc/services/link_service/parser.py
  - cod_doc/services/link_service/resolver.py
  - cod_doc/services/doc_service.py
  - cod_doc/services/import_service.py
  - cod_doc/cli/link.py
  - cod_doc/core/reindex.py
---

# Proposal 15 · Improving the link system and document rendering

> 🎯 Goal: (1) fix the broken layout of ordered lists,
> (2) close the holes in `link_service` (import/bulk edits/URL),
> (3) add **semantic backfill** — smart recovery of
> outgoing/incoming links for documents that were created
> before COD-079 or imported as plain-markdown without `[label](key)`.

## 1. What is wrong now

### 1.1. List layout (visual bug on the screenshot)

In the UI the document `obsidian/Modules/INFRA Tools Architecture/sql-workflow`
sections "Step 4 — Handle Migration Drift" and "Step 5 — Report Findings"
render **as a single line**:

> 1. **Document the drift explicitly.** … 2. **Assess whether the drift
> blocks the current task:** … 3. **Do not auto-resolve structural
> drift** — surface it every time.

Expected — three separate `<li>`.

The root is [`cod_doc/api/web/markdown.py:243-248`](cod_doc/api/web/markdown.py#L243-L248):

```python
# Bullet list?
if line.startswith(("- ", "* ")):
    flush_paragraph()
    flush_blockquote()
    list_items.append(line[2:])
    i += 1
    continue
```

The parser catches **only** `- ` / `* `. The prefix `1. `, `2. `, `10) ` is
not recognized as a list marker → the line falls into
`paragraph_lines.append(line)` ([markdown.py:251](cod_doc/api/web/markdown.py#L251)),
all items glue into one `<p>` via a newline that CSS collapses to a space.
Hence "list in one line".

Additionally in the docstring [`markdown.py:17`](cod_doc/api/web/markdown.py#L17)
ordered lists are not even mentioned as out-of-scope —
essentially an undocumented gap.

### 1.2. Link system — what works and where it leaks

The architecture is correct, 5-stage (parse → sync → resolve → verify →
cascade), see [`link_service/__init__.py:1-68`](cod_doc/services/link_service/__init__.py#L1-L68).
The parser ([`parser.py`](cod_doc/services/link_service/parser.py)) catches
`[[doc:KEY]]`, `[[doc:KEY#anchor]]`, `[[task:ID]]`, `[[story:ID]]`,
`[[Wiki Title]]`, markdown `[txt](href)`, bare URL.

Auto-sync is enabled from [`doc_service.py:256,342`](cod_doc/services/doc_service.py#L256)
via `_sync_section_links_safe()` — best-effort, errors are logged.
COD-079 fixed the "links always empty" regression.

| Problem | Where | Impact |
|---|---|---|
| **An imported document may have no links.** Before COD-079 import bypassed `_sync_section_links_safe`. The user must manually remember `cod-doc link backfill`. | [`import_service.py:144-206`](cod_doc/services/import_service.py#L144-L206), [`cli/link.py:204-258`](cod_doc/cli/link.py#L204-L258) | After migration/bulk import documents look "orphaned" — the Outgoing/Incoming sections are empty, as on the screenshot. |
| **Plain markdown without `[label](key)`.** If the author has no habit of putting canonical links, the document will forever stay without graph edges — backfill returns nothing because the syntax is absent. | all of `parser.py` | The link graph degrades to "islands". |
| **URL links are never verified.** `verify_section` skips kind=`URL` — no HTTP check. | [`resolver.py`](cod_doc/services/link_service/resolver.py) (verify branch) | Broken external links silently sit as `resolved=true`. |
| **patch_section outside UI bypasses auto-sync.** If someone edits body via direct SQL/merge-operation or an MCP-tool bypassing `doc_service.patch_section`, links are not recomputed. | [`doc_service.py:342`](cod_doc/services/doc_service.py#L342) | Drift between body and the `link` table. |
| **No cross-project links.** Declared as deferred. | [`link_service/__init__.py:22-28`](cod_doc/services/link_service/__init__.py#L22-L28) | Documents from different projects cannot reference each other. |
| **No fuzzy/semantic resolve.** `[[Some Title]]` is searched only by exact title match in DB. | `resolver.py` | A typo in a wiki-link → `broken_reason=not_found`. |

### 1.3. The semantic layer exists, but is not used for links

In [`cod_doc/core/reindex.py`](cod_doc/core/reindex.py) there is already a
ChromaDB index and an embedding backend (OpenAI or local
sentence-transformers, see `config.embedding_backend`,
`config.embedding_model`). It powers `search_docs()` for the agent —
but **never** used either for (a) auto-suggestions of links during
editing, or for (b) graph backfill after import. The infrastructure
exists, the surface to it does not.

## 2. What is proposed

### 2.1. Fix ordered lists (P0, a small edit)

In [`markdown.py`](cod_doc/api/web/markdown.py):

1. Add a regex `_OL_ITEM = re.compile(r"^(\d+)[.)]\s+(.+)$")`.
2. Introduce `ol_items: list[str]` and `flush_ol_list()` by analogy
   with `flush_list()`. Output `<ol>…</ol>`. Take the start number from
   the first item (`<ol start="N">`) — needed for cases where the author
   breaks a list with a paragraph and continues with "5.".
3. Before the `if line.startswith(("- ", "* "))` branch add a branch
   `if m := _OL_ITEM.match(line): …`.
4. In `flush_all()` add `flush_ol_list()`.
5. In the docstring lines 14-19 explicitly list supported/unsupported.
6. Tests in [`tests/api/web/test_markdown.py`](tests/api/web/test_markdown.py)
   (create if absent): single-item, multi-item, mixed `1.`/`1)`,
   breaking a list with an empty line, continuing numbering, mixed
   bullet+numbered.

**Do not** in this iteration: nested lists (requires a stack of indent
levels — a separate task), GFM task-lists `- [ ]`.

### 2.2. Guarantee auto-sync on all write paths (P1)

Now auto-sync hangs on `add_section`/`patch_section` in
`doc_service`. We need:

1. Raise it to the **repository** level or a single
   `_apply_section_body_change()` hook — so any future call cannot
   bypass it.
2. In `import_service.import_markdown()` ([import_service.py:144-206](cod_doc/services/import_service.py#L144-L206))
   after the `add_section` loop add a **final** pass of
   `link_service.resolve_section()` for all inserted sections —
   now resolve is called from sync, but between them neighboring sections
   manage to insert, and a forward-link `[[doc:NEW-DOC]]` to a not yet
   created document stays `resolved=false`. A two-pass import fixes this.
3. In CLI/MCP keep `link backfill` as a safety-net for legacy data,
   but in UI remove the need to remember it — see 2.3.

### 2.3. **Semantic backfill** — smart analysis of documentation

This is the central proposal. Scenario: the user imported 200
markdown-files from Obsidian. They have either wiki-style `[[Note]]` or
plain-text mentions at all. The link graph is empty.

#### 2.3.1. Signal source

Use the **already working** ChromaDB index from
[`reindex.py`](cod_doc/core/reindex.py). It indexes the body of each
section with embeddings `config.embedding_model`. This gives us a free
"semantically similar sections" search without a new store.

#### 2.3.2. Algorithm (per project, idempotent)

For each section `S` of the project:

1. **Pull candidates via embedding** — top-K (K=20)
   nearest sections from ChromaDB, excluding `S` itself and sections of
   the same document. Similarity by cosine.
2. **Noise filter** — keep only candidates with similarity ≥ τ
   (starting τ=0.78, configurable via `config.semantic_link_threshold`).
3. **Re-rank with lexical signals** — for each candidate `C`:
   - `+0.10` if the title of `C` occurs as a substring in the body of `S`
     (case-insensitive, word-boundary). This catches "mentions without a
     link".
   - `+0.15` if the doc_key of `C` occurs as a substring (catch for
     imports from Obsidian, where people wrote the path by hand).
   - `+0.05` if `C` already has incoming-links from the same
     parent module (means the concept is connected).
   - `−0.10` if `C` is an orphan-section without incoming/outgoing at all
     (likely noise).
4. **Cap-and-confirm** — top-N (N=5) after re-ranking are offered
   as **suggestions**, not written to the `link` table automatically.
5. Suggestions land in a new table `link_suggestion`
   (`from_section_id`, `to_doc_key`, `to_section_id`, `score`,
   `evidence` JSON, `state` ∈ `pending|accepted|rejected`,
   `created_at`).

#### 2.3.3. Surface for the user

- **CLI**: `cod-doc link suggest --project P [--threshold 0.78]
  [--apply-above 0.92]` — prints a table
  "section → suggested target → score → evidence", with a flag
  `--apply-above X` immediately accepting what is above X (for the case
  "I trust, everything > 0.92 is definitely a link").
- **Web**: on the document page in the footer (where "No outgoing
  links yet" is now) — a "Suggested links" section with a list of
  pending-suggestions and `Accept` / `Reject` buttons per each. Accept
  inserts `[label](doc-key)` at the end of the body of the corresponding
  section (or into a special auto-managed "See also" block), triggers the
  usual sync-resolve pipeline, transitions the suggestion to `accepted`.
- **MCP**: tool `link_suggest_for_section(section_id)` — for an
  agent-assistant so it can suggest links autonomously during writing.

#### 2.3.4. Why this is not brute force

A full enumeration of section pairs is O(N²), on a project of 5k sections
that is 2.5×10⁷ comparisons. Via the ChromaDB ANN-index → O(N·log N) with
a top-K search constant. On 5k sections — seconds, not hours. Re-ranking
runs only on 20×N = 100k small operations (substring match), which is also
cheap.

#### 2.3.5. Where it lives in code

Create `cod_doc/services/link_service/semantic.py`:

```
def suggest_for_section(session, section_id, *, k=20, tau=0.78) -> list[Suggestion]:
    ...

def backfill_project(session, project_id, *, apply_above=None, dry_run=False) -> Report:
    ...
```

Dependencies: `core.reindex` (embeddings), `infra.repositories.section_repo`
(metadata), `infra.repositories.link_repo` (filter of already-existing
edges). **No** new embedding stack is introduced.

### 2.4. URL verification (P2, optional)

Not in this proposal. A separate task: a background periodic job
(routine) that polls URL-links with a HEAD request, sets
`broken_reason=http_5xx|http_4xx|timeout`. Not on the write-path —
there we still skip (rule §7).

## 3. Roadmap

| Step | Content | Files | Size |
|---|---|---|---|
| **15.1** | Ordered lists in renderer + tests | [`markdown.py`](cod_doc/api/web/markdown.py), `tests/api/web/test_markdown.py` | S |
| **15.2** | Two-pass import: after `import_markdown` — a batch `resolve_section` for all inserts | [`import_service.py`](cod_doc/services/import_service.py) | S |
| **15.3** | Raise auto-sync to the level of a single hook; document the invariant "any body edit → sync" | [`doc_service.py`](cod_doc/services/doc_service.py) | M |
| **15.4** | `LinkSuggestion` model + Alembic migration | `cod_doc/infra/models/`, `alembic/versions/` | M |
| **15.5** | `link_service/semantic.py` — algorithm 2.3.2, unit tests on a synthetic embedding store | `cod_doc/services/link_service/semantic.py` | L |
| **15.6** | CLI `cod-doc link suggest` and `link suggest --apply-above` | [`cli/link.py`](cod_doc/cli/link.py) | M |
| **15.7** | Web-UI "Suggested links" in the document footer | [`templates/web/_frag/section_view.html`](cod_doc/templates/web/_frag/section_view.html), [`api/web/pages/docs.py`](cod_doc/api/web/pages/docs.py) | M |
| **15.8** | MCP-tool `link_suggest_for_section` | [`mcp/tools/link_tools.py`](cod_doc/mcp/tools/link_tools.py) | S |

P0 — step 15.1 (visual bug, fixed in one commit). The rest —
sequentially, semantic backfill (15.4–15.7) — a separate phase with
a kickoff-brief per the project standard.

## 4. What we do **not** do

- Do not introduce a second embedding stack. Only what is already in `reindex`.
- Do not write suggestions to `link` directly — a separate table, so
  false positives do not pollute the graph.
- Do not do real-time suggestion-on-keystroke — a batch CLI/UI-flow
  is sufficient and predictable.
- Do not touch cascade on rename — it already works correctly.
- Do not cover nested lists and task-lists (`- [ ]`) — a separate task.

## 5. Open questions

1. The starting value of τ — 0.78 is empirical. A dataset of "known good
   links" from the current project is needed to calibrate precision/recall.
2. Where to write the accepted link: at the end of the section, into an
   auto-section "See also", or offer the user to pick the place? Default —
   "See also", an auto-managed block (like the footer-summary in COD-078).
3. Should suggestions be triggered automatically on every import,
   or only on an explicit command? Proposed: on import — only
   update the index, compute suggestions on demand (their generation
   is invalidated by adding new sections).
