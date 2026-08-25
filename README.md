# COD-DOC

**Context Orchestrator for Documentation** — an MCP server and autonomous agent
that keeps project documentation honest.

Docs, tasks, plans, stories, links and revisions live in a database
(`<project>/.cod-doc/state.db`). Markdown is a *projection* of that state, not
the source of truth — so a doc can never silently drift away from the task that
changed it.

## Why a database instead of just markdown

- **Every edit is a revision.** Full history per document and per task, with
  revert — not a `git blame` on a 900-line file.
- **Drift is detectable.** Each projection carries a content hash, so
  `cod-doc doc drift` tells you exactly which file was edited in place behind
  the system's back.
- **Humans and agents share one interface.** Anything in the service layer is
  exposed through both the CLI and MCP — an agent has no private back door and
  no missing capability.
- **Links are first-class.** Documents, sections, tasks, ADRs and git commits
  reference each other as rows, so renames cascade instead of rotting.

![COD-DOC web UI — project overview](https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/02-overview.png)

## Quick start

```bash
pip install cod-doc
cod-doc project add . --name myproj   # register the project, create its DB
cod-doc import docs myproj            # ingest existing .md into the DB
cod-doc serve                         # REST API + web UI on http://localhost:8765
cod-doc-mcp --profile agent           # MCP over stdio for Claude Code / Desktop
```

## Four equal surfaces

| Surface | Entry point | For |
|---|---|---|
| CLI | `cod-doc` | day-to-day human work; `task`, `doc`, `plan`, `story`, `link`, `adr` groups |
| MCP | `cod-doc-mcp` | LLM agents; profiles `agent` (6 task-centric tools), `standard`, `full` (~100 CRUD tools) |
| REST + Web | `cod-doc serve` | dashboards, review, editing in the browser |
| TUI | `cod-doc tui` | legacy terminal UI |

New functionality lands in the service layer and must appear in *both* the CLI
and MCP — agent and human get an identical interface by construction.

## Requirements

Python 3.11+ (3.11 / 3.12 / 3.13 tested in CI). SQLite ships with Python; no
external database, daemon or indexer is required. The project DB is a single
file under `.cod-doc/` and must live on a local disk — WAL mode does not work
on iCloud, NFS or SMB shares.

## Documentation

| Document | What it is |
|---|---|
| [`docs/cod-doc-guide.md`](docs/cod-doc-guide.md) | tutorial: document a project from scratch, ~30 min |
| [`docs/HANDBOOK.md`](docs/HANDBOOK.md) | reference: install, CLI, web UI tour, config, troubleshooting |
| [`docs/adoption-playbook.md`](docs/adoption-playbook.md) | adopting COD-DOC on an existing repo with accumulated markdown |
| [`docs/mcp-integration.md`](docs/mcp-integration.md) | wiring the MCP server into Claude Code, Claude Desktop, VS Code |
| [`AGENTS.md`](AGENTS.md) | contributing: DB workflow, Definition of Done, PR requirements |
| [`MASTER.md`](MASTER.md) | the project's own map, maintained by COD-DOC itself |

The repository is documented in Russian; this README is the English entry point.

## License

MIT.
