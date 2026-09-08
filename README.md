<p align="center">
  <img src="https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/hero.png" alt="COD-DOC — Context Orchestrator for Documentation" width="100%">
</p>

<p align="center">
  <a href="https://pypi.org/project/cod-doc/"><img src="https://img.shields.io/pypi/v/cod-doc" alt="PyPI"></a>
  <a href="https://pypi.org/project/cod-doc/"><img src="https://img.shields.io/pypi/pyversions/cod-doc" alt="Python 3.13+"></a>
  <a href="https://github.com/Orange-hanter/cod-doc/actions/workflows/ci.yml"><img src="https://github.com/Orange-hanter/cod-doc/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

<p align="center">
  <b>Docs that drift from code are worse than no docs.<br>
  COD-DOC makes drift <i>detectable</i> — and gives both humans and LLM agents one shared, auditable interface to fix it.</b>
</p>

---

Docs, tasks, plans, stories, links and revisions live in a database
(`<project>/.cod-doc/state.db`). Markdown is a *projection* of that state, not
the source of truth — so a doc can never silently drift away from the task that
changed it.

## Features

- **Hash-verified docs.** Every markdown file carries a content hash of the DB
  state it was projected from. `cod-doc doc drift` tells you exactly which file
  was edited behind the system's back.
- **Every edit is a revision.** Full append-only history per document and per
  task, with revert — not a `git blame` on a 900-line file.
- **First-class links.** Documents, sections, tasks, ADRs and git commits
  reference each other as rows. Renames cascade instead of rotting.
- **Task engine with atomic checkout.** 7-state task lifecycle, dependency
  graph, plan sections, ready-queue — agents lock work instead of racing it.
- **Built for LLM agents.** A dedicated 6-tool MCP profile (`agent_pick`,
  `agent_report`, `agent_complete`, …) gives an agent everything it needs in
  one call — no 100-tool cold start.
- **Zero infrastructure.** Python 3.13+ and the SQLite that ships with it. No
  daemon, no indexer, no external database — the project DB is a single file.

## Quick start

```bash
pip install cod-doc
cod-doc project add . --name myproj   # register the project, create its DB
cod-doc import docs myproj            # ingest existing .md into the DB
cod-doc serve                         # REST API + web UI on http://localhost:8765
cod-doc-mcp --profile agent           # MCP over stdio for Claude Code / Desktop
```

## Why not just…

| | Markdown + Git | Wiki (Notion, Confluence) | COD-DOC |
|---|:---:|:---:|:---:|
| Detects doc↔code drift | ❌ | ❌ | ✅ content hashes |
| Queryable structure (tasks, deps, links) | ❌ grep only | ⚠️ proprietary | ✅ SQL + API |
| Native interface for LLM agents | ❌ | ⚠️ via API tokens | ✅ MCP, first-class |
| Per-entity history & revert | ⚠️ per-file git log | ✅ | ✅ append-only revisions |
| Lives in your repo, works offline | ✅ | ❌ SaaS | ✅ single SQLite file |
| Human & agent share one contract | ❌ | ❌ | ✅ same service layer |

## Four equal surfaces

| Surface | Entry point | For |
|---|---|---|
| CLI | `cod-doc` | day-to-day human work; `task`, `doc`, `plan`, `story`, `link`, `adr` groups |
| MCP | `cod-doc-mcp` | LLM agents; profiles `agent` (6 task-centric tools), `standard`, `full` (~100 CRUD tools) |
| REST + Web | `cod-doc serve` | dashboards, review, editing in the browser |
| TUI | `cod-doc tui` | legacy terminal UI |

New functionality lands in the service layer and must appear in *both* the CLI
and MCP — agent and human get an identical interface by construction.

## Screenshots

| Overview | Docs | Tasks |
|---|---|---|
| ![Overview](https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/02-overview.png) | ![Docs](https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/03-docs-list.png) | ![Tasks](https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/05-tasks.png) |

| Plans | Task detail | Revision history |
|---|---|---|
| ![Plans](https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/06-plans.png) | ![Task detail](https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/05b-task-detail.png) | ![Revisions](https://raw.githubusercontent.com/Orange-hanter/cod-doc/main/docs/assets/cod-doc/07-revisions.png) |

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

## Requirements

Python 3.13+ (3.13 tested in CI; 3.11/3.12 dropped 2026-09-07). SQLite ships with Python; no
external database, daemon or indexer is required. The project DB is a single
file under `.cod-doc/` and must live on a local disk — WAL mode does not work
on iCloud, NFS or SMB shares.

## License

MIT.
