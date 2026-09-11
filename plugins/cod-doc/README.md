# cod-doc — plugin for Claude Code and Cursor

Installs into the session the skills, commands, hooks, and scout agent needed
to work with a project whose documentation and tasks live in the cod-doc DB
(`<project>/.cod-doc/state.db`), with markdown as a projection.

The plugin is **project-agnostic**: the project slug and the path to the
CLI binary are resolved on the fly (`scripts/cod-doc-env.sh`).

**The plugin is not the MCP server.** The only MCP runtime is the
`cod-doc-mcp` binary. Host configs are written by
`cod-doc connect install` with an absolute `command`. Cursor must not
spawn MCP through the Claude-plugin bridge.

Hosts:

- **Claude Code** — skills / commands / hooks (`.claude-plugin/plugin.json`).
  `.mcp.json` ships with an empty server map. MCP is
  `cod-doc connect install --client claude-code`.
- **Cursor** — skills / commands / hooks
  (`plugin.json`, `.cursor-plugin/plugin.json`). MCP is exclusively
  `~/.cursor/mcp.json` via `cod-doc connect install --client cursor`.
  Root `mcp.json` also ships empty, so neither the Agent Plugins path
  nor the Claude-plugin bridge has a stdio server to spawn.

A leftover red `plugin-cod-doc-cod-doc` row means an old cache (0.1.2
or earlier) is still being read. Update the plugin (`claude plugin update`)
and reload the window. Working tools appear as `user-cod-doc`.

## What is inside

| Part | What it gives |
|---|---|
| MCP (via `cod-doc connect install`) | tools `task_*` / `doc_*` / `plan_*` / `adr_*` / `link_*`; the `standard` profile (110 tools), changed via `COD_DOC_PROFILE` or `--profile` |
| `/cod-doc:status` | a project snapshot: progress, queue, stuck checkouts, drift |
| `/cod-doc:task` | the task protocol: checkout → work → complete with a sha |
| `/cod-doc:drift` | check and (with `--fix`) repair `edited_in_place` |
| `/cod-doc:setup` | connecting a new repository to cod-doc |
| skill `task-flow` | the law of working with tasks; picked up by context |
| skill `doc-sync` | markdown ↔ DB, the hash registry, drift semantics |
| agent `cod-doc-scout` | read-only recon on the DB without dumping documents into the context |
| hook PostToolUse | after editing a **tracked** `.md` reminds about `doc import` |
| hook SessionStart | a summary of unclosed tasks — **disabled by default** |

## Installation

1. Install the Python package so `cod-doc-mcp` exists
   (`pip install -e '.[dev]'` in this checkout, or a uv/pipx tool install).
2. Wire the host:

```bash
cod-doc connect install --client cursor          # ~/.cursor/mcp.json
cod-doc connect install --client claude-code     # ~/.claude.json
cod-doc connect install --client all             # every supported host
cod-doc connect doctor
```

Claude Code plugin (skills/commands/hooks; MCP via `connect install`):

```bash
claude plugin marketplace add /Users/dakh/Git/_my/cod-doc   # or the repository URL
claude plugin install cod-doc@cod-doc
```

Cursor (local, from this checkout) — skills only; MCP is the user config
from step 2:

```bash
mkdir -p ~/.cursor/plugins/local
ln -sfn /Users/dakh/Git/_my/cod-doc/plugins/cod-doc ~/.cursor/plugins/local/cod-doc
```

Then reload the window (Developer: Reload Window) and confirm `cod-doc`
under Customize → Plugins.

Check: `claude plugin list`, `/cod-doc:status` in the connected project,
and in Cursor the `user-cod-doc` namespace listing `task_*` tools.

## Requirements

- `cod-doc` is installed: the binary is looked up as `$COD_DOC_BIN` →
  `<project>/.venv/bin/cod-doc` → `~/.local/bin/cod-doc` → `cod-doc` in PATH;
  the MCP server — `$COD_DOC_MCP_BIN` → project `.venv` → `~/.local/bin` →
  uv tools → Homebrew / `/usr/local/bin`.
- The project has `.cod-doc/state.db` (otherwise the hooks are silent, and
  the commands will suggest `/cod-doc:setup`).
- `sqlite3` in PATH — the hooks use it to resolve the slug without spinning
  up Python.

## Environment variables

| Variable | Meaning |
|---|---|
| `COD_DOC_PROJECT` | force the project slug (otherwise — by `root_path` in the DB) |
| `COD_DOC_BIN`, `COD_DOC_MCP_BIN` | explicit paths to the binaries |
| `COD_DOC_PROFILE` | the MCP profile: `agent` (6) / `minimal` (20) / `standard` (110) / `full` (114) |
| `COD_DOC_SESSION_BRIEF=1` | enables the summary on session start (disabled by default) |

## Relation to the `.claude/` of the cod-doc repository itself

The cod-doc repository keeps its own `.claude/skills/{task-flow,doc-sync}`,
`.claude/commands/gate.md` and `.claude/hooks/md-drift-reminder.sh` —
tied to itself (slug `cod-doc`, `.venv/bin/…`, the plan
`adoption-2026-08`). The plugin skills are generalized copies of the same rules
and arrive under the `cod-doc:` prefix, so there is no name conflict; when
both are active at the same time the drift reminder will arrive twice. Removing
the repo-local versions is a separate decision, the plugin does not require it.

The `/gate` command intentionally did not move into the plugin: it runs
`ruff`/`mypy`/`pytest` of cod-doc itself and is meaningless in someone else's
project.
