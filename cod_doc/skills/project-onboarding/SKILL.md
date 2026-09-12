---
name: project-onboarding
description: |
  How to onboard an existing repository under COD-DOC: register → init →
  import docs → plan → first task. Step order, what to import and what
  not, how not to break someone else's markdown, "project onboarded"
  criteria. Triggers: onboard, onboarding, project add, project init,
  import docs, bootstrap, adopt, adoption, register project, start using.
---

# Skill — Project onboarding

## When it loads

Tasks where an **existing repository** (not cod-doc) is onboarded under
COD-DOC. Trigger keywords: `onboard`, `project add`, `project init`,
`import docs`, `bootstrap`, "start using".

Not for creating documentation from scratch — that is
[`docs/cod-doc-guide.md`](../../../docs/cod-doc-guide.md).

## Invariant you must not break

**Import does not rewrite the user's markdown.** `import docs` only
reads files and creates `Document` records; the sources stay untouched
until the first explicit `doc export`. Until the user asks for an
export — the DB catches up to the files, not the other way around.

Hence the rule: **onboarding always starts with `--dry-run`.**

## Canonical order

```bash
# 1. Global registry ~/.cod-doc/config.yaml: path + name (slug from --name).
#    Step is MANDATORY (ADO-029): without it the CLI resolves the project
#    only from cwd — cron/routines and any run outside the repository
#    directory will fail with "Project not found" (live case: first cron
#    tick ADO-024 on the pilot).
cod-doc project add /path/to/repo --name my-app

# 2. Local state: .cod-doc/state.db + migrations
cod-doc project init my-app

# 3. Reconnaissance: what will actually land in the DB (--dry-run prints the final list)
cod-doc import docs my-app --dry-run

# 3b. Saw garbage — cut it out and look again, before writing to the DB
cod-doc import docs my-app --exclude 'experiments/stand*' --exclude '*/_archive' --dry-run

# 4. Import (default cap 1000 files) — with the same --exclude
cod-doc import docs my-app --exclude 'experiments/stand*' --exclude '*/_archive'

# 5. Code index: repo_file / repo_symbol / imports
cod-doc reindex files -p my-app

# 6. Verification: drift must be 0, frontmatter findings — advisory
cod-doc doc drift -p my-app --all
cod-doc audit -p my-app
```

`import all` runs the pipelines in sequence — fine when the repository
is already reconnoitered with `--dry-run`, but on the first pass prefer
the step-by-step variant.

## What to import and what not

Extensions: `.md`, `.rst`, `.txt`, `.markdown`. The walker **itself**
skips dotfiles, any dot-directories and noisy build directories
(`_SKIP_DIRS`: `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`,
`dist`, `build`, `.pytest_cache`, `.mypy_cache`, `.cod-doc`, `.chroma`,
`cod_doc.egg-info`).

Skipping dot-directories (`.cursor`, `.claude`, …) is not silent:
`--dry-run` prints them as a list — "Hidden directories skipped (N): …".
A long candidate list dry-run truncates to 50 lines; the full list —
`--limit 0`.

Everything else is decided by `--exclude` (SYM-004) — a repeatable glob
pattern by path **relative to the repository root**:

- `--exclude 'experiments/stand*'` — stands and all their contents;
- the pattern is matched against both the file path and each of its
  ancestor directories, so a directory pattern cleans the whole subtree
  (`--exclude 'docs/_archive'`);
- this is **not** gitignore: `*` crosses `/`, so `docs/*` will match
  `docs/a/b/c.md`. Verify the pattern via `--dry-run`, not by eye.

The decision is still yours — the flag only executes it:

| Category | Decision |
|---|---|
| `docs/`, `README.md`, ADR, specs, plans, `CHANGELOG.md` | import |
| `_archive/`, `Archive/`, `old/` | **decide explicitly**: an archive inflates FTS and pulls dead context into `context_get` |
| Generated API docs (OpenAPI dumps, autodoc) | do not import: a projection of code, not a source |
| Vendor / third-party license texts, submodule docs | do not import |
| `.txt` with data (logs, dumps, fixtures) | do not import: the extension matches, the meaning does not |

If `--dry-run` shows such a thing — add `--exclude` and run `--dry-run`
again until the list is clean. Importing "and cleaning up later" is an
anti-pattern: deleting a document after import pulls revisions and
links along.

## First plan

Import gives documents, but does not give **tracking**. A project is
considered onboarded only when there is a plan and it has tasks:

1. `plan_create(scope=...)` — scope in kebab-case, by the name of the
   work front (`web-mvp`, `stabilization-2026-07`), not by the repository
   name.
2. Plan sections — phases, not modules (`plan_section_create`).
3. First tasks — via `task_create`; format and mandatory fields see
   skill `task-standard`, decomposition — skill `plan-to-tasks`.

If the project already has a markdown-plan with tasks — parse it
manually into `task_create` calls; there is no automatic parser for
someone else's plan formats.

## "Project onboarded" criteria

All five — mandatory:

- [ ] `cod-doc project list` shows the project with a status other than
  `unknown`.
- [ ] `cod-doc doc drift -p <slug> --all` → `missing` == 0 and
  `edited_in_place` == 0. (`stale_export` in "files are the source" mode
  is **expected** and is not a defect.)
- [ ] `cod-doc audit -p <slug>` → 0 error-severity findings (warnings
  are allowed).
- [ ] There is ≥ 1 plan with ≥ 1 task; `cod-doc plan ready -p <slug>`
  returns a non-empty list.
- [ ] `cod-doc search "<any domain term>" -p <slug>` finds documents
  (the FTS index is alive).

Until at least one item is not done — the project is half-onboarded,
and the agent cycle (`agent_pick`) on it will give incomplete context.

## Anti-patterns

- **Import without `--dry-run`.** Drags archives and vendor docs;
  cleaning up later is more expensive than looking ahead.
- **`doc export` on someone else's repository without need.** ADO-010
  is closed: round-trip byte-identical, but the guard will still refuse
  to write over a hand edit or into someone else's checkout without
  `--force-write`. The default mode for pilots is "files are the source,
  the DB is the index": `import docs` on onboarding, `doc import <file>`
  after manual edits; export — only when the DB consciously becomes the
  source.
- **One common plan for the whole repository.** Plans are per work
  front; "do everything" does not decompose and gives no `plan ready`.
- **Onboarding for the sake of onboarding.** If the repository has 2
  markdown files and no tasks — cod-doc gives nothing beyond `grep`.
  Worth onboarding projects that have either ≥ 10 documents or a live
  backlog.

## Related

- [capabilities/project-bootstrap.md](../../../docs/system/capabilities/project-bootstrap.md) — the target state of the capability.
- [docs/adoption-playbook.md](../../../docs/adoption-playbook.md) — scenarios by project type.
- [docs/HANDBOOK.md §4](../../../docs/HANDBOOK.md) — Quick Start.
- skill `task-standard`, skill `plan-to-tasks` — what to do after import.
- skill `ground-truth-reconcile` — regular reconciliation after onboarding.
