# COD-DOC: integration with LLM and Copilot

> How to connect cod-doc to Cursor, VS Code Copilot, Claude Desktop,
> Claude Code, Codex and other LLM systems. The installer is
> `cod-doc connect install` — do not hand-copy JSON with a bare
> `cod-doc-mcp` command.

---

## Overview of the interfaces

cod-doc provides 4 access layers:

| Layer | For whom | When to use |
|------|----------|-------------------|
| CLI | Developer in the terminal | Manual work, scripts, CI/CD |
| TUI | Developer interactively | Initial setup, wizard |
| REST API | External systems | Dashboards, CI, web interfaces |
| **MCP** | **LLM clients** | **Copilot, Claude, agents** |

MCP (Model Context Protocol) — a standard protocol for connecting an LLM
to external tools. cod-doc implements an MCP server with **114 tools**
(the exact number is validated by the test `tests/test_mcp_integration_doc.py`),
grouped into 4 profiles.

The only MCP runtime is the `cod-doc-mcp` binary (`pyproject.toml` entry
point). Plugins ship skills, commands, and hooks — they do not spawn the
server for Cursor.

---

## Connect a host (the supported path)

Resolve the absolute binary, probe JSON-RPC `initialize`, and merge a
host config without clobbering other servers:

```bash
cod-doc connect install --client cursor
cod-doc connect install --client claude-code
cod-doc connect install --client vscode
cod-doc connect install --client claude-desktop
cod-doc connect install --client codex
cod-doc connect install --client all
cod-doc connect doctor
cod-doc connect which
```

Invariants:

- `command` is always an **absolute executable**. Forbidden: `${…}`
  interpolation, `./` relative paths, `bash -c` wrappers, and a bare
  `cod-doc-mcp` name (GUI apps have an empty PATH).
- IDE default profile is `standard` (110 CRUD). The server's own default
  `agent` profile is a 6-tool surface — only use it when the client asks.
- One live server per host. In Cursor the working namespace is
  `user-cod-doc`. The plugin does not spawn MCP; a red
  `plugin-cod-doc-cod-doc` row is a stale cache (update the plugin).

Target files (user-scope / gitignored):

| `--client` | Config |
|---|---|
| `cursor` | `~/.cursor/mcp.json` (`mcpServers.cod-doc`) |
| `claude-code` | `~/.claude.json` (`mcpServers.cod-doc`) |
| `vscode` | `<project>/.vscode/mcp.json` (`servers.cod-doc`) |
| `claude-desktop` | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| `codex` | `~/.codex/config.toml` (`[mcp_servers.cod-doc]`) |

`cod-doc mcp` still **runs** the server (stdio or streamable-http). It
does not write host configs.

---

## CLI ↔ MCP names

Agents that can only see one surface should not treat a missing name as a
missing feature. These are the same operations:

| CLI | MCP |
|---|---|
| `cod-doc plan show` | `plan_progress` (not `plan_show`) |
| `cod-doc plan create` | `plan_create` |
| `cod-doc adr new` | `adr_create` |
| `cod-doc adr export` | `adr_export` |
| `cod-doc doc import FILE` | `doc_import` |
| `cod-doc doc drift` | `doc_drift` / `doc_drift_all` |
| `cod-doc hash update` | `hash_update` |
| `cod-doc search` | `search` (not `tool_search`) |
| `cod-doc task create` | `task_create` (MCP is wider: `blocked_by`, `story_id`, `affects_files`) |

---

## Agent profile — 6-tool surface (cycle-5, default of the binary)

> The AI agent works task-centric, not CRUD-centric. The agent profile is
> a 6-tool surface, where each call returns a self-sufficient payload.
> One call replaces 5-10 round-trips.

| Tool | What it does |
|-----|------------|
| `agent_capabilities()` | L0 entry-point: server version, available skills, valid TaskStatus, default_project, recommended next-action. <4KB. |
| `agent_pick(project, agent_id, plan_scope?)` | Atomically: ready-set → checkout → assemble a **task card** = `{task, context{plan, story, related_docs, siblings, affected_files, recent_history}, navigation{applicable_skills (with BODIES), next_actions, success_criteria, legal_status_transitions}}`. Idempotent. |
| `agent_get(project, task_id, what, ref?)` | Opt-in deep fetch. `what ∈ {full_doc_body, related_task, story_full, plan_export}`. |
| `agent_report(project, task_id, kind, message, agent_id?, payload?)` | Dispatcher. `kind ∈ {progress, blocker, approval_request, needs_context}`. |
| `agent_complete(project, task_id, agent_id, commit_sha?, summary?)` | Guarded done + release lock in one transaction. |
| `agent_release(project, task_id, agent_id, reason?)` | Drop lock without done; status → todo. |

### Task lifecycle (canonical 3-step)

```text
1. agent_capabilities()              # who am I / what skills / what profile
2. agent_pick(project, agent_id)     # task + context + navigation card
   ↓ (do the work)
3a. agent_complete(...)              # success
3b. agent_report(kind='blocker',..)  # stuck
3c. agent_release(reason=...)        # refuse without done
```

### Launching under a chosen profile

```bash
cod-doc-mcp                              # agent (default cycle-5)
cod-doc-mcp --profile minimal            # 20 cold-start tools
cod-doc-mcp --profile standard           # 110 CRUD tools (without legacy)
cod-doc-mcp --profile full               # all 114 (including legacy)
COD_DOC_PROFILE=full cod-doc-mcp         # via env
```

Host installers pass `--profile standard` unless you override
`cod-doc connect install --profile …`.

### Migration guide (cycle-3/4 → cycle-5)

If your integration already calls `task_checkout` / `context_get` /
`task_complete` directly — it will keep working under `--profile standard`
or `--profile full`. There are no deprecations on the CRUD tools themselves.

For **new** agent integrations the agent profile is recommended:

| Cycle-3/4 pattern (6 calls) | Cycle-5 equivalent (3 calls) |
|---|---|
| `capabilities()` → `skill_list()` → `skill_get('orchestrator')` → `list_projects()` → ... | `agent_capabilities()` |
| `task_next_ready()` → `task_checkout()` → `context_get('task',id)` → `skill_get('task-standard')` | `agent_pick(project, agent_id)` |
| `task_complete()` → `task_release()` → `activity_emit()` | `agent_complete(project, task_id, agent_id)` |
| `task_set_blocker()` + `task_update_status(blocked)` + `activity_emit()` | `agent_report(kind='blocker', message=...)` |

---

## Option 1. Cursor

```bash
cod-doc connect install --client cursor
cod-doc connect doctor
```

Reload the window. Tools appear under the `user-cod-doc` namespace.
The plugin ships no MCP spawn; if a red `plugin-cod-doc-cod-doc` row
is still listed, the Claude plugin cache is older than 0.1.3 — run
`claude plugin update` and reload again.

The installer is idempotent: a second run updates `mcpServers.cod-doc`
and leaves every other server in `~/.cursor/mcp.json` untouched.

---

## Option 2. VS Code Copilot Chat

```bash
cod-doc connect install --client vscode
```

Writes `<project>/.vscode/mcp.json` with an absolute `command` (the
file is gitignored). After connecting, the entire MCP surface is
available in Copilot Chat (**114 tools** in the current release).
Example requests:

- "Show the status of the weather-cli project"
- "Which tasks are not closed?"
- "Are there stale links in MASTER.md?"
- "Add a task: write documentation for the auth module"
- "Update the hashes"
- "Find everything about error handling in the docs"
- "Run the agent for one iteration"

Copilot picks the right tools and calls them itself.

### Addition: copilot-instructions.md

For Copilot to work better, create `.github/copilot-instructions.md`:

```markdown
## Project documentation

This project is documented via cod-doc.
- Documentation navigator: MASTER.md (read it first)
- Structure: specs/ (requirements), arch/ (architecture), models/ (data), docs/ (misc)
- If you need to find something in the docs — use the MCP tool `search_docs`
- Before changing docs — check the hashes via `check_stale_refs`
```

This gives Copilot context about how the documentation is organized, even without MCP.

---

## Option 3. Claude Desktop

```bash
cod-doc connect install --client claude-desktop
```

Restart Claude Desktop. A 🔧 icon with the available tools will appear
in the interface.

The same MCP surface: docs, tasks, plans, stories, links, revisions, runs,
approvals, routines, activity, skills. Claude Desktop works well with
tools — you can have a dialogue about the documentation:

```
You: Show the list of projects
Claude: [calls list_projects] → You have 2 projects: weather-cli and proinstall...

You: What is the status of proinstall?
Claude: [calls get_project_status] → 9 documents, all hashes valid, 6 open tasks...

You: Show the contents of MASTER.md
Claude: [calls get_master] → ...
```

---

## Option 4. Claude Code (CLI)

```bash
cod-doc connect install --client claude-code
```

That writes an absolute `command` into `~/.claude.json`. The Claude
Code plugin no longer ships MCP (`.mcp.json` is an empty map). Use the
installer; do not add a plugin spawn for Cursor's Claude-plugin bridge.

```bash
claude "Show the documentation status of the proinstall project"
claude "Find everything about CSS variables in the docs"
claude "Add a task: update docs/overview.md after the refactoring"
```

---

## Option 5. Codex

```bash
cod-doc connect install --client codex
```

Upserts `[mcp_servers.cod-doc]` in `~/.codex/config.toml` and leaves
the rest of the file intact.

---

## Option 6. Streamable HTTP (for remote / cloud agents)

stdio with an absolute `command` is the default on the user's machine.
HTTP is an option when the client cannot spawn a local process:

```bash
cod-doc mcp --transport streamable-http --host 127.0.0.1 --port 8001
# endpoint: http://127.0.0.1:8001/mcp
```

Connecting in any MCP client:
```json
{
  "mcpServers": {
    "cod-doc": {
      "url": "http://127.0.0.1:8001/mcp"
    }
  }
}
```

When to use:
- Server on one machine, client on another
- Docker / remote development
- Multiple clients to one server

---

## Option 7. REST API (without MCP)

For systems that do not support MCP:

```bash
cod-doc serve  # → http://localhost:8765
```

Available endpoints:
```
GET  /api/config
GET  /api/projects
POST /api/projects
GET  /api/projects/{name}/status
GET  /api/projects/{name}/tasks
POST /api/projects/{name}/tasks
WS   /ws/projects/{name}/run     # launch the agent via WebSocket
```

### Example: GitHub Actions

```yaml
- name: Check documentation freshness
  run: |
    cod-doc serve &
    sleep 2
    STATUS=$(curl -s http://localhost:8765/api/projects/myproject/status)
    STALE=$(echo $STATUS | jq '.stale_refs')
    if [ "$STALE" -gt 0 ]; then
      echo "::warning::Documentation has $STALE stale references"
    fi
```

---

## Option 8. MASTER.md only (without a server)

Even without a running MCP server, MASTER.md is useful for an LLM:

1. **Copilot instructions** → specify "read MASTER.md first"
2. **Context window** → copy MASTER.md into a chat with any LLM
3. **@workspace in Copilot** → Copilot will find MASTER.md via file search

MASTER.md v0.2 is designed as two-layer:
- The top part — tables, diagrams, a checklist (clear to a human)
- The `<details>` block — metadata, hashes, protocols (clear to an LLM)

An LLM can parse MASTER.md and build a project map even without MCP.

---

## Catalog of MCP tools

> The source of truth is the `tools/list` of an MCP client and `skill_list` for guides.
> This table is a navigator of "what is in which family" for the current release.
> The numbers are checked against the real catalog via
> [`tests/test_mcp_integration_doc.py`](../tests/test_mcp_integration_doc.py).

| Family | Count | Purpose | Key tools |
|-----------|-------:|------------|---------------|
| **doc.\*** | 11 | DB-backed documents | `doc_list`, `doc_body`, `doc_create`, `doc_rename`, `doc_export`, `doc_import`, `doc_drift`, `doc_drift_all`, `doc_get`, `doc_accept`, `doc_backfill_projection` |
| **task.\*** | 17 | DB-backed tasks (lifecycle) | `task_create`, `task_create_many`, `task_get`, `task_list`, `task_next_ready`, `task_update_status`, `task_update`, `task_complete`, `task_set_blocker`, `task_find_duplicate`, `task_log_progress`, … |
| **task_doc.\*** | 5 | Artifacts linked to a task | `task_doc_put`, `task_doc_get`, `task_doc_list`, `task_doc_revisions`, `task_doc_revert` |
| **task_checkout / task_release** | 2 | Atomic task checkout (PCA-200) | `task_checkout`, `task_release` |
| **plan.\*** | 11 | Execution plans and dependency graphs | `plan_create`, `plan_freeze`, `plan_section_create`, `plan_sections_list`, `plan_ready`, `plan_progress`, `plan_critical_path`, `plan_forward_chain`, `plan_reverse_chain`, `plan_audit`, `plan_export` |
| **story.\*** | 7 | User stories + acceptance criteria | `story_create`, `story_list`, `story_get`, `story_link`, `story_add_criterion`, `story_update_status`, `story_coverage` |
| **link.\*** | 4 | Hybrid links between documents | `link_list`, `link_sync`, `link_verify`, `link_suggest_for_section` |
| **revision.\*** | 3 | Entity change history | `revision_list`, `revision_get`, `revision_revert` |
| **run.\*** | 1 | Inspection of the built-in orchestrator runs (ADR-012) | `run_get` |
| **approval.\*** | 5 | Human-in-the-loop approvals | `approval_request`, `approval_list`, `approval_get`, `approval_resolve`, `approval_cancel` |
| **activity.\*** | 1 | Unified audit timeline | `activity_list` |
| **routine.\*** | 7 | Cron-style health checks | `routine_create`, `routine_list`, `routine_get`, `routine_update_status`, `routine_delete`, `routine_run_now`, `routine_history` |
| **skill.\*** | 2 | Catalog of skill-instructions for the agent | `skill_list`, `skill_get` |
| **agent.\* (cycle-5)** | 6 | Task-centric surface for AI agents: pick → work → complete in 3 calls | `agent_capabilities`, `agent_pick`, `agent_get`, `agent_report`, `agent_complete`, `agent_release` |
| **adr.\* (ADR-002)** | 10 | Architecture Decision Records: CRUD + supersede DAG + task links + Mermaid diagrams + deprecate + export | `adr_create`, `adr_get`, `adr_list`, `adr_update`, `adr_add_diagram`, `adr_supersede`, `adr_deprecate`, `adr_link_task`, `adr_graph`, `adr_export` |
| **context / capabilities / session** | 9 | Admin: snowball context assembly, L0 bootstrap, tool discovery + per-tool describe, change-log, safe-call envelope, workspace defaults | `context_get`, `capabilities`, `tool_search`, `tool_describe`, `tools_diff`, `tool_call_safe`, `set_default_project`, `get_default_project`, `clear_default_project` |
| **check_config** | 1 | Server self-diagnostics | `check_config` |
| **Legacy (YAML agent)** | 3 | Remainder of the legacy surface after STB-002 (2026-06-08): resume-entry + context-helpers. YAML CRUD (projects/tasks/MASTER/search + hash/verify) is removed — the DB is the source of truth. | `run_agent_once`, `get_agent_context`, `clear_agent_context` |
| **finding.\* (RFC 22)** | 4 | External findings (ai-review / ZAIrgRush / routines): triage and promotion to tasks. Only the standard/full profiles | `finding_list`, `finding_get`, `finding_promote`, `finding_dismiss` |
| **ctx.\* (RFC 22)** | 3 | Context for external consumers: `ctx_docs` = `doc_list`, `ctx_drift` = `doc_drift_all` (SYM-006D), `ctx_drift_gate` — a deterministic documentation gate by PR files with an idempotent PR comment (SYM-010). Only the standard/full profiles | `ctx_docs`, `ctx_drift`, `ctx_drift_gate` |
| **search.\*** | 1 | FTS5 corpus | `search` |
| **hash.\*** | 1 | MASTER hybrid-ref registry | `hash_update` |
| **TOTAL** | **114** | | |

The legacy family duplicates part of the DB surface (for example `add_task` ↔
`task_create`, `list_tasks` ↔ `task_list`) and is marked `DEPRECATED` in
the docstring of the corresponding tools. For new integrations — ignore legacy
and rely on the DB surface; `--profile standard` hides legacy completely.

## MCP Resources

| URI | Type | Description |
|-----|-----|---------|
| `cod-doc://config` | Static | Current configuration |
| `cod-doc://projects` | Static | List of projects |
| `cod-doc://project/{name}/master` | Template | The project's MASTER.md |
| `cod-doc://project/{name}/tasks` | Template | The project's tasks |

## MCP Prompts

| Prompt | Purpose | Parameters |
|--------|-----------|-----------|
| `doc_review` | Documentation review | `project_name`, `focus?` |
| `doc_plan` | Documentation plan | `project_name` |
| `onboard_project` | Onboarding a new project | `project_name` |

---

## Comparison of the options

| Criterion | MCP (stdio) | MCP (HTTP) | REST API | MASTER.md only |
|----------|-------------|-----------|----------|-------------------|
| Setup | `connect install` | Medium | Simple | None |
| Copilot Chat | ✅ | ✅ | ❌ | Partial |
| Claude Desktop | ✅ | ✅ | ❌ | Via copy-paste |
| CI/CD | ❌ | ✅ | ✅ | ❌ |
| Number of tools | 114 | 114 | ~8 | 0 |
| Semantic search | ✅ | ✅ | ❌ | ❌ |
| Agent launch | ✅ | ✅ | ✅ (WS) | ❌ |

---

## Recommendations

**For a single developer:** `cod-doc connect install --client cursor` (or
`vscode`) + Copilot / Cursor agent. Absolute stdio, no extra daemon.

**For a team:** MCP (HTTP) + copilot-instructions.md when clients cannot
spawn a local binary. A server on a shared machine, everyone connects
from their own IDE.

**For CI/CD:** REST API. Checking documentation freshness, automatically creating tasks when stale refs are detected.

**For a quick start:** MASTER.md only + copilot-instructions.md. Zero setup, Copilot finds MASTER.md via @workspace.

## How I suggest learning next

A good next learning cycle:

1. I show you a live smoke test with an MCP client.
2. Then together we add one more tool.
3. Then you yourself formulate what is missing from the documentation workflow.
4. After that we decide whether to keep the native MCP server or build a bridge over REST.
