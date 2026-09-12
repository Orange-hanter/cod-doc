---
name: validation
description: |
  When to apply structural validation (raise) vs advisory audit (issues).
  FM-002 / FM-003 are escalated as blockers; FM-004 / FM-005 are advisory
  comments. Triggers: write to MASTER.md, create/update doc, change hashes,
  frontmatter, sensitivity, validate, audit_*.
---

# Skill — Validation pattern

## When it loads

Tasks and contexts where a **write** to the DB happens (document, task,
section, link), especially if frontmatter, sensitivity or hashes are
involved. Trigger keywords: `validate`, `frontmatter`, `sensitivity`,
`FM-002`, `FM-003`, `audit_`, `audit-`, `structural`, `valid`, `verify`.

## Principles

Two-level validation:

1. **Structural — `validate_*`**: strict check of write-path invariants.
   Any violation → `ValidationError` (raise). Gates `DocService.create`,
   `TaskService.create`, `StoryService.create`, etc. The mandatory rules
   live here — without them the object **must not** appear in the DB.

2. **Advisory — `audit_*`**: returns `list[ValidationIssue]` without
   raising. Used for pre-commit `cod-doc audit`, future-CI and write-path
   as a soft highlight of problems. Does not block.

## FM-escalations (frontmatter)

| Code | Level | What to do |
|-----|---------|-----------|
| FM-002 | blocker | `validate_frontmatter` raises; the record is rejected |
| FM-003 | blocker | same |
| FM-004 | advisory | `audit_frontmatter` returns an issue; the record goes through |
| FM-005 | advisory | same |
| FM-006 | advisory (sensitivity) | written as a warning; not a blocker |
| FM-007 | advisory | warning when `sensitivity` is missing for
        `module-spec/architecture/standard` |

## Algorithm

1. Determine **what** we are writing — doc / task / story / section / link.
2. Find the corresponding `validate_*` in `cod_doc/services/validation/`.
3. If `ValidationError` — do NOT swallow; propagate it upward with
   `error_code` (FM-NNN or TP-NNN) for display to the human.
4. After a successful write — call `audit_*` and attach the issues to the
   response as soft warnings.

## What NOT to do

- Do not turn advisory into blockers at your discretion — this breaks the
  pre-existing pipeline.
- Do not skip `validate_*` "for speed" — the write-path must be gated.
- Do not add new FM codes in the skill; the rules live in
  `cod_doc/services/validation/_rules/` + `standards/frontmatter.md`.

## Related

- [standards/frontmatter.md](../../../docs/system/standards/frontmatter.md)
- [services/validation/](../../services/validation/)
