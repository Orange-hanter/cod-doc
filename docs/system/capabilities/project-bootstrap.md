---
type: capability
scope: project-bootstrap
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../audit/2026-04-19-initial-audit.md
  - ../migration/from-restate.md
---

# Capability — Project Bootstrap

> What happens on `cod-doc project new`: DB records, skeleton documents, agents, config.

## 1. Command

```bash
cod-doc project new \
  --slug <slug> \
  --root <path> \
  --title <title> \
  [--profile embedded|server] \
  [--from-template restate|generic|empty]
```

## 2. What gets created

### 2.1 The `project` record
- `slug`, `title`, `root_path`, `created`, default `config_json`.

### 2.2 Default documents

| doc_key | type | sensitivity |
|---------|------|-------------|
| `master` | `vision` | internal |
| `architecture` | `architecture` | internal |
| `documentation-graph` | `guide` (auto-generated) | internal |
| `navigation` | `guide` | internal |
| `standards/frontmatter` | `standard` (clone from system) | internal |
| `standards/task-plan` | `standard` (clone from system) | internal |
| `standards/document-link` | `standard` (clone from system) | internal |

Templates live in `cod_doc/templates/projects/<template>/`. This is the analogue of the Restate stack "out of the box".

### 2.3 Default agents

Imported from the system catalog ([agents-and-skills.md §2](agents-and-skills.md)): `task-steward`, `docs-reviewer`, `link-verifier`, `migrator`, `release-manager`.

### 2.4 MCP registration

The CLI asks: `Register MCP for Claude Code? [Y/n]`. If yes — writes the config to `~/.claude/mcp.json` or the project's `.mcp.json`.

### 2.5 Hooks

Optional (`--with-hooks`):
- git pre-commit: `cod-doc audit --strict --staged`
- git post-commit: `cod-doc task sync_from_diff`

## 3. Profile

| Parameter | embedded | server |
|----------|----------|--------|
| DB | `.cod-doc/state.db` (SQLite) | `COD_DOC_DB_URL` (Postgres) |
| REST API | off | on |
| Embeddings | sqlite-vss | pgvector |
| Auth | local user | token-based |

## 4. Idempotency

Re-running `project new --slug <existing>` — error. To recreate: `cod-doc project drop <slug> --confirm`. Drop does not delete the markdown projection (only the DB record); `--purge` deletes everything.

## 5. Importing an existing project

`cod-doc project new --from-existing <root>`:
- scans `<root>` for markdown with frontmatter;
- does not create default documents (uses the existing ones);
- runs the analogue of [migration/from-restate.md](../migration/from-restate.md) stages 3-7.

## 6. Audit after bootstrap

The final step of the command is `cod-doc audit --strict`. The project is not considered ready until it returns 0 errors.
