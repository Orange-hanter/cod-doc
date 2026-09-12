---
type: migration-plan
scope: restate-to-cod-doc
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../DATA_MODEL.md
  - ../standards/task-plan.md
  - ../standards/frontmatter.md
---

# Migration — Restate (manual) → COD-DOC (managed)

> A step-by-step guide for transferring the current state of `~/Git/Restate` into the COD-DOC DB without losing history and without documentation downtime.

## 1. Initial state (as of 2026-04-19)

- `Docs/MASTER_DOCUMENTATION.md` — navigation + changelog.
- `Docs/AGENT_START.md` — agent entrypoint.
- `Docs/workspace-map.yaml` — machine-readable project map.
- `Docs/obsidian/` — the main vault (~70 files, hundreds of kilobytes of markdown in total).
- `Docs/obsidian/Modules/<Module>/` — module specs (split and inline).
- `Docs/obsidian/Modules/<Module>/<module>-task-plan.md` — execution plans.
- `Docs/obsidian/Modules/<Module>/tasks/section-*.md` — section files.
- `Docs/obsidian/Modules/<Module>/<module>-completed-tasks.md` — archives.
- `Docs/obsidian/User Stories.md` — a large file with all the stories.
- `Docs/standards/{frontmatter,task-plan,module-spec}.md` — standards.
- `tools/task-plan-audit.mjs` + family — validation.
- `tools/lightrag/` — RAG index.

## 2. Migration principles

1. **No downtime**: Obsidian users continue working in the vault until the "freeze-and-import" days are over.
2. **Frozen projection first**: after import, COD-DOC marks all markdown as "projection-of-record" and does not overwrite it until an explicit `cod-doc export`.
3. **Revisions from git**: the Restate commit history is played into the revision table (author=`human:<git-author>`, commit_sha=real).
4. **No heading magic**: if a task fails validation — it is imported with `status=draft` and a warning, not breaking the bulk load.

## 3. Stages

### Stage 0 — preparation

```bash
cd ~/Git/cod-doc
cod-doc project new --slug restate --root ~/Git/Restate --title "Restate"
cod-doc project use restate
```

Creates a `project` record, resolves `root_path`.

### Stage 1 — import standards

```bash
cod-doc import restate-standards
  # reads Docs/standards/*.md, creates document rows with type=standard
  # their frontmatter already mostly matches; light normalization
```

The goal — standard docs end up first, because they are the validation vocabulary for the rest.

### Stage 2 — import workspace-map

```bash
cod-doc import workspace-map --file Docs/workspace-map.yaml
  # creates module rows + module_code
  # entries become proto-modules
```

The `depends_on` fields are mapped to `module_dependency`. The `api_navigation` field — into the module's frontmatter (used later).

### Stage 3 — import module specs

```bash
cod-doc import docs \
  --glob "Docs/obsidian/Modules/**/*.md" \
  --type-infer \
  --fail-on-invalid=warn
```

Type inference:

- `<module>-task-plan.md` → `execution-plan`.
- `tasks/section-*.md` → `task-section`.
- `<module>-completed-tasks.md` → `execution-log`.
- Module folders → `module-spec` + subdocuments.
- The rest → `module-subdoc` if it lies in a module folder, otherwise `guide`.

The `implemented_in`, `depends_on`, `api_navigation` fields are taken from the frontmatter into the corresponding `module` / `document` fields.

### Stage 4 — import execution-plans and tasks

```bash
cod-doc import plans \
  --from-docs \
  --validate strict
```

- Parses the parent-plan + section-files.
- Writes `plan`, `plan_section`, `task`, `dependency`, `affected_file`.
- For each task writes a revision `author=human:<git-blame>`, `commit_sha=<hash>`, `at=<commit_date>`.
- Format violations are logged to `audit_log`, the task is imported with `status=draft`, marked `warning`.

### Stage 5 — import user stories

```bash
cod-doc import stories --file Docs/obsidian/User\ Stories.md
```

Splits one large file by the pattern `## US-<NNN> — <title>`. Acceptance criteria are parsed from `- [ ]` / `- [x]`. `implemented_by` links — if the history explicitly specifies task-IDs.

### Stage 6 — resolve links

```bash
cod-doc link reindex --project restate
cod-doc link verify --project restate
```

- Iterates over all section bodies.
- Extracts wiki-links and markdown-relative-links.
- Resolves against the DB.
- Sets `resolved` / `broken_reason`.

Expected: several dozen broken links to already deleted legacy files. They are marked and go into the backlog of `type=docs` tasks.

### Stage 7 — import revisions from git

```bash
cod-doc import git-history --project restate --since 2026-01-01
```

- `git log` over document files.
- Each commit on a document → a revision.
- `diff` = `git show` for the file.
- `author` from `user.email` → `human:<email-slug>`.
- `commit_sha` is real.

This is a long stage (may take an hour or two for the full history). Can be limited to a period (`--since`).

### Stage 8 — validation

```bash
cod-doc audit --project restate --strict
```

Equivalent of `node tools/task-plan-audit.mjs --strict` + `--linkable` + `--stale` + `--orphans`. Expected result: several warnings, 0 errors. If errors — they are described in the report, can be fixed targeted.

### Stage 9 — frozen projection

```bash
cod-doc projection freeze --project restate
```

All documents get `projection_hash = hash(current disk content)`. Manual markdown edits are now detected: any hash mismatch → `cod-doc doc diff <key>` shows what changed, `cod-doc doc accept <key>` confirms in the DB.

### Stage 10 — enable the MCP stack

```bash
cod-doc mcp install --client claude-code
cod-doc mcp install --client vscode-copilot
```

Registers the cod-doc MCP server for agents. From this point, agents work via `task.*`, `doc.*`, `context.*` tools, not via the terminal.

## 4. Reverse transition (rollback)

While the frozen projection is not unfrozen (`cod-doc export` not run in bulk), rolling back to manual mode is simply:

```bash
cod-doc project pause restate
# COD-DOC does not touch Restate files
# the author continues in Obsidian as before
```

The DB state is preserved. Can be resumed at any time.

## 5. Coexistence with Restate tooling

- `tools/task-plan-audit.mjs` can be left running — it reads the same markdown projection.
- `tools/task-plan/mcp-server.mjs` can be turned off in favor of cod-doc MCP, when all agents have migrated.
- `tools/lightrag/` is kept for the transition period; cod-doc embeddings are enabled alongside, then Restate-RAG is turned off.

## 6. Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| A format violation in legacy sections breaks import | Import is non-blocking, warnings — not errors; specific tasks with draft-status can be fixed afterward |
| Links to Obsidian aliases are lost | `link.raw` is preserved, the alias is parsed separately, restored in the projection |
| Embeddings consume memory in the embedded profile | The pg profile is recommended for Restate-scale |
| The author is not used to working via CLI | The TUI dashboard + `wizard doc new/task new` cover 80% of scenarios; Obsidian is available as a read-only view |
| Revisions bloat the DB | a compact job (`revision compact --older-than 365d`) + snapshot |

## 7. Migration success control

Two weeks after stage 10:

- ✅ `cod-doc audit --strict` on Restate — 0 errors.
- ✅ Any task-create completes in ≤ 1 sec, without manual markdown editing.
- ✅ `link verify` — 0 regressions (only the known backlog of broken ones at import time).
- ✅ The agent completes the cycle "understand → create a plan → close a task" with ≤ 30% of today's manual intervention.

## 8. After migration

- Only COD-DOC is maintained; markdown is generated.
- Once a week — `cod-doc audit --strict` in CI.
- Once a month — `cod-doc export-changelog` for the public CHANGELOG.
- Obsidian can be used as a read-only pane / viewer, if someone prefers it.
