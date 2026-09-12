---
name: doc-style
description: |
  Documentation style: language, headings, hybrid references, statuses.
  Triggers: style, format, language, frontmatter, hybrid, link, heading,
  prose.
---

# Skill — Documentation style

## When it loads

Tasks where **documentation prose is written or edited** — capability,
audit, kickoff, README, HANDBOOK. Trigger keywords: `style`, `format`,
`frontmatter`, `hybrid`, `link`, `markdown`, `prose`, `documentation`,
`doc`.

## Language

- **Prose — in the project language** (for cod-doc default English, unless
  the user says otherwise).
- **Identifiers** (fields, types, status, entity and table names,
  task_id, doc_key) — **always in English**, regardless of prose language.
- Do not mix translit and the prose language in identifiers.

## Headings

- `# Title` — one per document, at the top (after frontmatter).
- `## N. Section` — numbering for long docs; for short ones — without
  numbers.
- `### Subsection` — third level. Deeper is a rare exception.

## Hybrid references

Format:

```
📁 /path/to/file.ext | 🗃️ doc:sanitized_path | 🔑 sha:12hexchars
```

The full format is for entries in the `MASTER.md` Validation Table
(section 5.1). For inline — markdown-relative: `[label](relative/path.md)`.

Statuses (badges):

- `🟢 VERIFIED` — all three components agree.
- `🟡 DRAFT` — draft, has not passed validation yet.
- `🟡 LEGACY` — correct but reviewable; canonical_source is elsewhere.
- `🔴 STALE` — hash is stale.
- `🔴 BROKEN` — file / doc-key is missing.

## Frontmatter

Every document in `docs/system/` has YAML-frontmatter; mandatory fields
are `type`, `status`, `source_of_truth`, `owner`. Full spec:
[standards/frontmatter.md](../../../docs/system/standards/frontmatter.md).

## Structure

- Do not duplicate information between files. If a section is needed in
  two docs — extract it into a separate markdown and link to it.
- Long tables → markdown-table; not pseudo-asciiart.
- Mermaid diagrams for dependencies; code blocks with ```mermaid.
- Lists — `- ` (not `*`); numbered `1. 2. 3.` (not `1) 2)`).

## Paragraph style

- Brevity > completeness. One paragraph — one thought.
- Do not write "very", "sufficient", "simply", "in general" — usually
  filler.
- Active voice: "the service writes a revision", not "a revision is
  written by the service".

## What NOT to do

- Do not invent examples that do not exist in the code. If you reference
  a file — check it exists (`check_stale_refs` or a direct read).
- Do not insert ASCII-art from characters; use mermaid for diagrams.
- Do not leave a `TODO` without an owner and context.

## Related

- [standards/document-link.md](../../../docs/system/standards/document-link.md)
- [standards/frontmatter.md](../../../docs/system/standards/frontmatter.md)
- skill `audit-cadence` — when creating an audit-report / kickoff.
