# Guide: project documentation from scratch via COD-DOC

> A step-by-step guide on a real example.
> Time: ~30 minutes. Result: a fully documented project.

---

## What you need

- Python 3.11+ with `cod-doc` installed
- Any project with source code (we will create a demo project)
- A terminal

## Step 0. Install cod-doc

```bash
pip install cod-doc
# or if the repository is cloned:
cd /path/to/cod-doc && pip install -e .
```

Check:
```bash
cod-doc --help
```

---

## Step 0a. Connecting the MCP server (for Claude Code / Claude Desktop)

```bash
# In the root of the cod-doc repository:
cp .mcp.json.example .mcp.json
# Open .mcp.json and leave ONE of the two configurations (docker or native).
# Restart the MCP client.
```

`.mcp.json` is already in `.gitignore` — each developer keeps their own copy. The ready template contains two launch options:

- **`cod-doc-docker`** — connects to a running `docker compose up` container (recommended; projects are mounted via `docker-compose.yml`).
- **`cod-doc-native`** — uses the `cod-doc-mcp` CLI script (requires `pip install -e .`).

After connecting, the client gets access to all `mcp__cod-doc__*` tools (`task.list`, `task.summary`, `context.get`, `doc.create`, …).

---

## Step 1. Create a demo project

For this guide, we will create a simple Python CLI — a weather forecast.

```bash
mkdir ~/weather-cli && cd ~/weather-cli
git init
```

Create the structure:
```
weather-cli/
├── weather/
│   ├── __init__.py
│   ├── cli.py          # click-based CLI
│   ├── api.py          # HTTP client to weather API
│   └── formatter.py    # output formatting
├── tests/
│   └── test_api.py
├── pyproject.toml
└── README.md
```

> Substitute your own project here — the steps are the same for any stack.

---

## Step 2. Register the project in cod-doc

### Option A: via CLI (interactive wizard)

```bash
cod-doc wizard
```

The wizard will ask:
1. **OpenAI API key** — needed for the autonomous agent (can be skipped for manual mode)
2. **Project name** — `weather-cli`
3. **Path** — `/Users/you/weather-cli`
4. **MASTER.md** — the navigator file name (default `MASTER.md`)

### Option B: via MCP (programmatically)

```bash
# Start the MCP server
cod-doc mcp --transport stdio
```

Or via Python:
```python
import asyncio
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="cod-doc-mcp", args=["--transport", "stdio"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Register the project
            result = await session.call_tool("add_project", {
                "name": "weather-cli",
                "path": "/Users/you/weather-cli",
            })
            print(result.content[0].text)

asyncio.run(main())
```

### Option C: via REST API

```bash
cod-doc serve  # starts FastAPI on :8765

curl -X POST http://localhost:8765/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "weather-cli", "path": "/Users/you/weather-cli"}'
```

### What happened

cod-doc created in the project:
```
weather-cli/
├── .cod-doc/           # ← service directory
│   ├── tasks.yaml      # task queue
│   └── state.yaml      # agent state
├── MASTER.md           # ← documentation navigator
└── ... (your code)
```

`.cod-doc/` is added to `.gitignore`.

---

## Step 3. Explore the generated MASTER.md

Open `MASTER.md` — this is the main project navigator. The new v0.2 format:

- **Top part** — for humans: overview, section table, checklist, log
- **Bottom part** (`<details>`) — for the LLM: metadata, hashes, links, protocols

> Key principle: MASTER.md is the single entry point.
> The agent (and a human) reads it first, then goes to the needed files.

---

## Step 4. Create the documentation structure

cod-doc uses 4 directories:

| Directory | Purpose | Examples |
|------------|-----------|---------|
| `specs/` | What the product should do | Requirements, user stories, API contracts |
| `arch/` | How the code is organized | Architecture, components, data flow |
| `models/` | Data structures | Models, DTO, DB schemas |
| `docs/` | Everything else | Overview, operations, FAQ, onboarding |

```bash
mkdir -p specs arch models docs
```

---

## Step 5. Create the first document (manual mode)

Create `docs/overview.md`:

```markdown
# Weather CLI

## Purpose
A CLI utility to get a weather forecast from the terminal.

## Features
- Current weather by city
- Forecast for N days
- Output formats: table, JSON, compact

## Stack
- Python 3.11+
- Click (CLI framework)
- httpx (HTTP client)
- OpenWeatherMap API
```

### Generate a reference for MASTER.md

**Via CLI:**
```bash
cod-doc hash calc docs/overview.md
# → sha:a1b2c3d4e5f6  docs/overview.md
```

**Via MCP:**
```python
result = await session.call_tool("generate_ref", {
    "project_name": "weather-cli",
    "file_path": "docs/overview.md",
})
# → 📁 /docs/overview.md | 🗃️ doc:docs_overview_md | 🔑 sha:a1b2c3d4e5f6
```

### Add to MASTER.md

In the section table:
```markdown
| Overview | [docs/overview.md](docs/overview.md) | Purpose, stack, features | 🟢 |
```

In the LLM metadata section (`<details>`):
```markdown
#### Overview
- **Reference:** `📁 /docs/overview.md | 🗃️ doc:docs_overview_md | 🔑 sha:a1b2c3d4e5f6`
- **Status:** `🟢 VERIFIED`
```

---

## Step 6. Bulk creation via tasks

Instead of creating each file manually — set tasks for the agent.

### Via CLI:
```bash
cod-doc task add weather-cli "Describe the CLI architecture" --priority 1
cod-doc task add weather-cli "Describe the API client" --priority 2
cod-doc task add weather-cli "Describe the WeatherData model" --priority 3
```

### Via MCP:
```python
for title, prio in [
    ("Describe the CLI architecture", 1),
    ("Describe the API client weather/api.py", 2),
    ("Describe the WeatherData model", 3),
    ("Describe the output formatting", 4),
    ("Write an operations runbook", 5),
]:
    await session.call_tool("add_task", {
        "project_name": "weather-cli",
        "title": title,
        "priority": prio,
    })
```

### Look at the queue:
```python
result = await session.call_tool("list_tasks", {
    "project_name": "weather-cli",
    "status": "pending",
})
```

---

## Step 7. Autonomous agent (if you have an API key)

```bash
cod-doc agent weather-cli --autonomous
```

The agent:
1. Reads MASTER.md
2. Takes the next task from the queue
3. Reads the source code via tools
4. Creates/updates the documentation file
5. Recomputes hashes
6. Updates MASTER.md
7. Repeats for the next task

### Via MCP:
```python
events = await session.call_tool("run_agent_once", {
    "project_name": "weather-cli",
    "autonomous": True,
})
```

> Without an API key — create documents manually (step 5) or via Copilot (step 10).

---

## Step 8. Integrity check

### Check hashes:
```bash
cod-doc hash update MASTER.md
```

### Via MCP:
```python
# Find stale references
result = await session.call_tool("check_stale_refs", {
    "project_name": "weather-cli",
})
# → {"summary": {"total": 5, "valid": 4, "stale": 1, "broken": 0}}

# Update hashes
result = await session.call_tool("update_master_hashes", {
    "project_name": "weather-cli",
})
# → {"updated": 1, "warnings": []}
```

**Statuses:**
- `VALID` — the file has not changed, the hash matches
- `STALE` — the file has changed, the hash is outdated → needs updating
- `BROKEN` — the file was deleted → remove from MASTER.md

---

## Step 9. Semantic search

cod-doc indexes documents in ChromaDB for meaning-based search.

### Indexing:
```python
await session.call_tool("reindex", {"project_name": "weather-cli"})
# → {"indexed": 5, "errors": []}
```

### Search:
```python
await session.call_tool("search_docs", {
    "project_name": "weather-cli",
    "query": "how API errors are handled",
    "n_results": 3,
})
# → [{"path": "arch/api-client.md", "score": 0.87, "snippet": "..."}]
```

---

## Step 10. Integration with Copilot / LLM

The full section was planned as `docs/llm-integration.md`; for now the canonical
material lives in the system capability/RFC docs.

In short:

### VS Code + Copilot Chat
1. Add the MCP server to `.vscode/mcp.json`
2. Copilot gets access to 23 cod-doc tools
3. You can ask: "show the project status", "which tasks are not closed", "update hashes"

### Claude Desktop / any MCP client
```json
{
  "mcpServers": {
    "cod-doc": {
      "command": "cod-doc-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

### REST API for any systems
```bash
cod-doc serve  # → http://localhost:8765/api/...
```

---

## Step 11. Day-to-day work

### Code changed → update the docs:
```
1. cod-doc hash update MASTER.md        # find what is stale
2. Update the affected files
3. cod-doc hash update MASTER.md        # record the new hashes
4. git commit
```

### Add a new module:
```
1. Create a file in the right directory (specs/, arch/, models/, docs/)
2. cod-doc hash calc path/to/file.md    # get the hash
3. Add a row to MASTER.md (section table + LLM section)
4. git commit
```

### Documentation review:
```python
# Check coverage
status = await session.call_tool("get_project_status", {"project_name": "weather-cli"})

# Find issues
stale = await session.call_tool("check_stale_refs", {"project_name": "weather-cli"})
```

---

## Summary: what we used

| Feature | CLI | MCP | REST |
|-------------|-----|-----|------|
| Register a project | `cod-doc wizard` | `add_project` | `POST /api/projects` |
| Create tasks | `cod-doc task add` | `add_task` | `POST /api/projects/{name}/tasks` |
| View tasks | `cod-doc task list` | `list_tasks` | `GET /api/projects/{name}/tasks` |
| Compute hashes | `cod-doc hash calc` | `hash_file` | — |
| Update hashes | `cod-doc hash update` | `update_master_hashes` | — |
| Integrity check | — | `check_stale_refs` | — |
| Generate a reference | — | `generate_ref` | — |
| Read a file | — | `read_file` | — |
| List files | — | `list_files` | — |
| Semantic search | — | `search_docs` | — |
| Indexing | — | `reindex` | — |
| Run the agent | `cod-doc agent` | `run_agent_once` | `WS /ws/projects/{name}/run` |
| Configuration | `cod-doc wizard` | `check_config` | `GET /api/config` |

### MCP-only tools (23):
`list_projects` · `get_project_status` · `add_project` · `remove_project` ·
`list_tasks` · `add_task` · `update_task` · `next_pending_task` ·
`get_master` · `update_master_hashes` · `check_stale_refs` · `generate_ref` ·
`read_context` · `read_file` · `list_files` ·
`hash_file` · `verify_hash` ·
`search_docs` · `reindex` ·
`run_agent_once` · `get_agent_context` · `clear_agent_context` ·
`check_config`
