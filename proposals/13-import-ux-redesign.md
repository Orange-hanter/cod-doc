---
status: implemented
type: ux-proposal
author: human:dakh
date: 2026-05-06
scope: web-ui · docs-import
related:
  - cod_doc/templates/web/project/docs_list.html
  - cod_doc/api/web/pages/docs.py
  - cod_doc/services/import_service.py
---

# Proposal 13 · Document import redesign

> 🎯 Goal: remove manual `doc_key` entry and single-file upload, replace
> with folder scanning + selection from a list. Minimize text input
> everywhere it is not yet done.

## 1. What is wrong now

[`docs_list.html:128-162`](cod_doc/templates/web/project/docs_list.html#L128-L162) and
[`docs.py:439-496`](cod_doc/api/web/pages/docs.py#L439-L496):

| Symptom                                                                   | Cause                                                                         |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| The "📥 Import markdown" button in the top bar does not work              | `href="#import-section"` — the anchor exists, but `<details>` does not open, the scroll goes "nowhere" |
| The `Doc key` field (`modules/M1-foo/overview`) — the user must know the key scheme | Import is "one file at a time", key = manual input path-as-string           |
| Import one file at a time via `<input type="file">`                       | No batch mode. To upload 30 modules — 30 clicks                            |
| `Type` duplicates what is usually in the frontmatter `type:`              | A redundant control given that parse_markdown already reads FM                   |
| The hint "or: `cod-doc doc import …`" appears on every import             | Teaches CLI instead of making the UI self-sufficient                            |

And a cross-cutting pain (see user feedback): across the project there are too many places where a human types strings by hand instead of picking from a list.

## 2. Proposed model

### 2.1. Source of truth — the project folder, not a single upload

The user points to a **folder** (once, in project Settings or on import
via drag-n-drop). The server builds a **manifest** — an index of all `.md`
files with their frontmatter. Compares with what is already in the DB.
Shows a diff: "new / changed / deleted / unchanged".

### 2.2. Scanning without LLM

No AI passes. What the parser already can do is enough:

- `parse_markdown()` from [`import_service.py`](cod_doc/services/import_service.py)
  → frontmatter + H1 + sections.
- Reading all `*.md` in the folder (`os.walk` + `.gitignore`-aware filter).
- Content is loaded **only on import**; for the index it is enough to have
  `(path, mtime, sha256(head_4kb), frontmatter_dict, h1_title)`.

`doc_key` is derived automatically:
- if the frontmatter has `doc_key:` — take it;
- otherwise the path relative to root, without `.md`, without the `docs/`
  prefix (`modules/M1-foo/overview.md` → `modules/M1-foo/overview`).

That is, the "Doc key" field **disappears from the form** in 95% of cases.
It remains only as an "advanced override" in the expanded row detail.

### 2.3. UI — a list with checkboxes

```
┌─ Import from folder ─────────────────────────────────────────┐
│  📁 docs/  · 47 .md files  [Rescan]                          │
│                                                              │
│  [✓] Select all new (12)   [ ] Select all changed (3)        │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ ✓ NEW       modules/M1-foo/overview     module-spec  │    │
│  │ ✓ NEW       modules/M2-bar/overview     module-spec  │    │
│  │ □ CHANGED   architecture/decisions      adr          │    │
│  │ ─ UNCHANGED roadmap/q3-2026             roadmap      │    │
│  │ ⚠ MISSING   modules/M0-old/overview     (was active) │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  [Import 12 selected]    [Cancel]                            │
└──────────────────────────────────────────────────────────────┘
```

Each row is a button (the whole row is a clickable toggle). No text
fields. The type is set from the frontmatter; if not specified — a
selection from a dropdown in the expanded detail (but not a mandatory
action — there is a default).

### 2.4. Backend — endpoints

- `GET /p/{slug}/docs/import/scan?path=...` → JSON manifest:
  `[{path, doc_key, type, title, status: "new"|"changed"|"unchanged"|"missing", reason}]`.
- `POST /p/{slug}/docs/import/apply` with body `{paths: [...]}` — import
  of the selected. Idempotent: changed → new revision, unchanged → noop.
- The existing `POST /p/{slug}/docs/import` stays as backward-compat
  for CLI/REST, but is not used from UI.

### 2.5. Folder source

Three variants in descending order of automation:
1. **Auto** — the folder is set in project Settings (`project.docs_root`,
   relative to git-root). The most common case.
2. **One-shot** — drag-n-drop a folder into the browser. Uses
   `webkitdirectory` or the File System Access API.
3. **Path input** — a text field as a fallback, hidden under "advanced".

## 3. Input minimization — a general principle of the project

Make the test measure for each form the question: "can the input be
replaced with a select / chip / drag-n-drop / checkbox?". Candidates
for the next pass (out of scope of this proposal, but in the same vein):

- Task creation (`task_create`): `module`, `priority`, `assignee` —
  all selects, but `title` is still by hand. Templates can be offered.
- New blank doc: `doc_key` is also by hand. It can be assembled from
  `(folder picker, title input)` where folder = a choice from existing.
- Filter bar — already good.

## 4. Implementation steps

1. Fix the "Import markdown" button in the header (open `<details>` with JS
   on anchor click) — so it is not left broken until the big rework.
   **A small separate COD-task.**
2. Introduce `project.docs_root` (migration + a field in Settings).
3. Add `services/import_service.scan_folder()` — a pure function,
   returns a manifest without writing to the DB.
4. Endpoint `GET /docs/import/scan` + page `/docs/import` with a checkbox-list.
5. Endpoint `POST /docs/import/apply` (batch) + reuses
   `import_markdown()` per-file inside one transaction.
6. Replace the old form in `docs_list.html` with a "Bulk import →" link.
7. Remove the old `<details>` after migrating existing flows.

## 5. Open questions

- **What to do with MISSING?** (the file was deleted in FS, but in DB it
  is active.) Variants: do nothing (decoupled storage), mark `deprecated`,
  show as a warning without a default action. → leaning toward
  warning-only, action via a separate button.
- **`doc_key` conflict** between two files with the same
  frontmatter `doc_key:` — show an error in the manifest row, block the
  import.
- **Sub-projects** — if `docs_root` contains nested projects with
  their own `.cod-doc/`, skip them.
