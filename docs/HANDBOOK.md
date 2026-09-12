# COD-DOC — Handbook

> Context Orchestrator for Documentation. A single document covering the whole
> product: what it is, how to install, how to use it via Web/CLI/MCP.
> Screenshots are real, taken by a Playwright run against a seed project.

**Status:** Web UI — 13/14 endpoints shipped (~93 %), 137 e2e tests green;
CLI — all main surfaces (`task`/`plan`/`doc`/`story`/`link`/`revision`/`project`/`agent`/`audit`/`hash`/`tui`);
MCP server for integration with Claude/Cursor; AI agent with an autonomous daemon mode;
ChromaDB vector index for semantic search; Docker-stack production-ready.

---

## Table of contents

1. [What is COD-DOC](#1-what-is-cod-doc)
2. [Architecture at a glance](#2-architecture-at-a-glance)
3. [Installation](#3-installation)
4. [Quick Start (5 minutes)](#4-quick-start)
5. [Web UI tour](#5-web-ui-tour) — 8 screenshots
6. [CLI reference](#6-cli-reference)
7. [Configuration](#7-configuration)
8. [MCP integration](#8-mcp-integration)
9. [AI agent (autonomous mode)](#9-ai-agent-autonomous-mode)
10. [Vector database (ChromaDB)](#10-vector-database-chromadb)
11. [Typical workflows](#11-typical-workflows)
12. [Troubleshooting](#12-troubleshooting)
13. [Testing and development](#13-testing-and-development)

---

## 1. What is COD-DOC

**COD-DOC** = Context Orchestrator for Documentation. It is an autonomous agent
that maintains project documentation for you:

- Stores the entire knowledge graph (documents, sections, tasks, plans,
  revisions, user stories, links) in SQLite and projects it onto markdown.
- Writes a revision on every change — git-style history without manually
  maintaining changelogs.
- Auto-linking of `[[doc:KEY#anchor]]` references with cascading renames.
- Frontmatter validation (FM-001..FM-007), task-plan structural validation
  (TP-001..TP-011), sensitivity scanning (SD-001..SD-002).
- Three surfaces with the same capability set: **Web UI**, **CLI**, **MCP** (Claude/Cursor).
- Daemon mode: a background agent watches MASTER.md and auto-generates tasks.

**Not a goal:** not a SPA, not a design system, not a replacement for Obsidian.
Web UI — server-rendered Jinja + targeted HTMX fragments, CSS ~370 LOC.

---

## 2. Architecture at a glance

```
                ┌──────────────────────────────────────────────────────┐
                │                  Presentation                         │
                │  ┌─────────┐    ┌────────┐    ┌─────┐    ┌────────┐  │
                │  │ Web UI  │    │  CLI   │    │ TUI │    │  MCP   │  │
                │  │ Jinja+  │    │  click │    │textl│    │ server │  │
                │  │  HTMX   │    │        │    │     │    │        │  │
                │  └────┬────┘    └────┬───┘    └──┬──┘    └───┬────┘  │
                └───────┼──────────────┼───────────┼───────────┼───────┘
                        │              │           │           │
                ┌───────┴──────────────┴───────────┴───────────┴───────┐
                │                    Services                           │
                │   doc · task · plan · story · link · revision         │
                │   projection · sensitivity · validation               │
                └──────────────────────┬───────────────────────────────┘
                                       │
                ┌──────────────────────┴───────────────────────────────┐
                │                    Domain                             │
                │   entities (Plan, Task, Document, Section, ...)       │
                │   pure dataclasses, no DB awareness                   │
                └──────────────────────┬───────────────────────────────┘
                                       │
                ┌──────────────────────┴───────────────────────────────┐
                │             Infra (DB + Repositories)                 │
                │   SQLAlchemy 2.0 · Alembic · embedded SQLite          │
                │   .cod-doc/state.db (per project)                     │
                └──────────────────────────────────────────────────────┘
```

**Layers:** Presentation never imports Infra directly — only through
Services + DI helpers (`cod_doc.api.deps.get_project_db`). The architectural
rule is enforced by the AST test `tests/api/test_web_layer_imports.py`.

**Main entities:**
- `Project` — a project (repo + `.cod-doc/state.db`).
- `Plan` → `PlanSection` → `Task` — the plan hierarchy. A Task with dependencies
  (`Dependency.kind = blocks`).
- `Document` → `Section` — structured markdown. Each section is
  a separate entity with its own revisions.
- `Revision` — every change of an entity (TASK/SECTION/DOCUMENT/PLAN).
- `UserStory` — a user story with acceptance criteria.
- `Link` — `[[doc:KEY#anchor]]` references, parsed + tracked.

---

## 3. Installation

### 3.1. Docker (recommended for production)

`docker-compose.yml` is already in the repo. The stack: one `cod-doc` container, a healthcheck,
port 8765.

```bash
git clone https://github.com/<org>/cod-doc.git
cd cod-doc

# Put your project under /projects/ via a volume mount.
# Open docker-compose.yml and uncomment / add:
#   volumes:
#     - cod_doc_data:/data/cod-doc
#     - /path/to/your/project:/projects/my-project:rw

docker compose up -d cod-doc

# Check:
curl http://localhost:8765/api/health
# {"status":"ok","configured":false,"projects":0}
```

For a DB-backed project there is a compact health summary for automations and
dashboards:

```bash
curl http://localhost:8765/api/projects/my-project/health
```

The response combines the current DB↔markdown drift, unresolved links, and the last
`doc_drift` routine run. If `.cod-doc/state.db` is not yet created, the endpoint
returns `status: "uninitialized"` without a 500.

The API key for the LLM is passed via env (see §7):
```bash
COD_DOC_API_KEY=sk-or-v1-... docker compose up -d cod-doc
```

### 3.2. Locally (dev)

```bash
git clone https://github.com/<org>/cod-doc.git
cd cod-doc

python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# alembic — the built-in admin for embedded SQLite migrations:
alembic upgrade head  # against the current working directory

cod-doc --help
cod-doc serve  # → http://127.0.0.1:8765
```

Migrations are applied automatically on the first access to a project via CLI;
an explicit `alembic upgrade head` is only needed if you create `state.db` by hand.

`alembic upgrade head` only knows about the DB in the current working directory. If
the project DB is moved outside the repository (`db_url` in the registry — hub mode), migrate
via the registry — the command resolves `db_url` wherever it is defined:

```bash
cod-doc project migrate my-app   # one registry entry
cod-doc project migrate --all    # all; exactly what the container does on startup
```

---

## 4. Quick Start

### 4.1. Register a project

```bash
# 1. Create or open your project
cd ~/code/my-app

# 2. Register it in COD-DOC
cod-doc project add . --name my-app

# 3. Initialize .cod-doc/ (creates state.db + MASTER.md)
cod-doc project init my-app

# 4. Check
cod-doc project list
# my-app    /Users/.../my-app    (enabled)
```

### 4.2. Create the first plan and task

```bash
# A plan in this project
cod-doc plan create -p my-app --scope onboarding --section A:Setup
cod-doc plan show onboarding -p my-app

# A task (minimal example)
cod-doc task create -p my-app --plan onboarding --section A \
  --title "Implement user login" \
  --type feature --priority high --prefix MYP
# → MYP-001 created

cod-doc task list -p my-app
```

### 4.3. Open the Web UI

```bash
cod-doc serve  # or docker compose up -d cod-doc
```

Open <http://localhost:8765> — you will see your project, click it to get to the
dashboard, see the plan, tasks, documents, revisions.

---

## 5. Web UI tour

All pages are server-rendered, without a JS bundler. HTMX is plugged in only
for inline editing (task status, section body). Without JS everything also
works — `<form method="post">` + 303 redirect.

### 5.1. Project list — `GET /`

![Projects list](assets/cod-doc/01-index.png)

- A table of all registered projects.
- Stats — DB-aggregated (total / done / in_progress).
- Pagination: `?limit=20&offset=0`, supports the "N projects" scale.
- Static-asset versioning: `/static/app.css?v=<mtime-hex>` — a cache-bust on upgrade.

### 5.1b. Initialize DB — empty project bootstrap

![Empty DB banner](assets/cod-doc/02b-init-banner.png)

If a project is registered in `~/.cod-doc/config.yaml` but `state.db` is not there yet
(a new project or recreated after `rm -rf .cod-doc`), the overview shows a banner
"**Project database is not initialized**" with an **Initialize DB** button.
Under the hood `POST /p/{slug}/init` via `project_service.init_project`:

1. `Project(entry).init()` — creates `.cod-doc/`, `tasks.yaml`, `state.yaml`,
   `MASTER.md` (if missing — without overwriting).
2. `alembic.command.upgrade(cfg, "head")` — programmatically, without a shell-out;
   migrations live inside the package (`cod_doc/infra/migrations/versions/*.py`).
3. A `ProjectModel` with `slug=<entry.name>` is created — without it
   `try_open_project_db` cannot find the project in the DB.
4. Engine cache invalidated → the next request sees the fresh schema.

Idempotent: a repeated click → an "info" flash "DB is already initialized".
The hint under the button gives the CLI equivalent: `cod-doc project init <slug>`.

### 5.2. Project dashboard — `GET /p/{slug}`

![Project overview](assets/cod-doc/02-overview.png)

7 KPI cards + 3 aggregates + MASTER.md render:

- **Status / Tasks total / Done / In progress / Pending / Failed / Last run** —
  aggregates from the DB (in sync with the Plan-progress block below). If the DB is not there yet —
  fallback to legacy YAML (`.cod-doc/tasks.yaml`).
- **Ready to start** — top-5 tasks ready to start, via
  `plan_service.ready` (filtered by unfulfilled blocks-deps). The HTMX `✓` button
  completes a task in one click. Each row is clickable → task details.
- **Plan progress** — a mini-table of plans with a progress bar, scope-link → plan details.
- **Recent revisions** — top-5 latest revisions (newest first).
- **MASTER.md preview** — now rendered via mini-markdown (headings,
  blockquote, fenced code, lists, inline). The "View raw" button at the top → `?raw=1`.
  First 80 lines; "Open full" → the full document under `/docs/{master_md}`.

### 5.3. Document list — `GET /p/{slug}/docs`

![Docs list](assets/cod-doc/03-docs-list.png)

A table with doc_key / title / type / status / owner / last_updated. Each
row is clickable. If `.cod-doc/state.db` is missing — a graceful warning
instead of a 500.

#### Import markdown

![Import markdown form](assets/cod-doc/03b-import-form.png)

Above the table — a collapsible toolbar **📥 Import markdown**. Uploads
an existing `.md` file to the DB via `POST /p/{slug}/docs/import`
(multipart/form-data): `doc_key` (required), `type` (dropdown by
DocumentType), `file` (.md, UTF-8). The parser:

- **Frontmatter** between `---\n…\n---\n` (YAML) → document fields: `title`,
  `type`, `status`, `owner`, `sensitivity`. Any missing ones — fallback
  to the form or the default (`module-spec`/`draft`/`internal`).
- **H1-line** right after the frontmatter → fallback `title`, the line itself
  is discarded (the document has a separate title field).
- Everything up to the first `## ` → `Document.preamble`.
- Each `## heading` → a new `Section` (anchor — slugified heading,
  duplicates get `-2`, `-3`…). Headings inside fenced code (` ``` `) are NOT
  considered section breaks.

CLI equivalent: `cod-doc doc import` (the same service layer,
`projection_service.import_document` for re-syncing existing docs).

### 5.4. Document — `GET /p/{slug}/docs/{doc_key:path}`

![Doc detail](assets/cod-doc/04-doc-show.png)

- The slug component in the URL — supports nested paths (`modules/M1/spec`).
- Sidebar with anchor-nav. Click → smooth scroll to the section.
- Mini-markdown renderer (~110 LOC, no new deps): paragraphs,
  fenced code, bullet lists, inline `code`/**bold**/*italic*/[link](url).
  HTML-escapes all input.
- ✎ button next to each section → swap into a `<textarea>` form with hidden
  `expected_parent_revision_id` (optimistic concurrency).
- `?raw=1` → fallback to `<pre>` mode.

**Inline section editing (HTMX flow):**

```
[GET /docs/foo]
  ↓ click ✎
[GET /docs/foo/sections/data-model/edit]   → swap to textarea
  ↓ edit + Save
[POST /docs/foo/sections/data-model]       → patch_section + revision
  ↓ swap-back to view fragment
[GET /docs/foo]                             → updated section visible
```

If another author updated the section between `edit` and `Save` — `RevisionConflictError`
→ `ConflictWebError(409)` → an alert-warning out-of-band via `hx-swap-oob`.

### 5.5. Tasks — `GET /p/{slug}/tasks`

![Tasks](assets/cod-doc/05-tasks.png)

- Table: ID / Title / Type / Status / Priority / Plan / Section.
- Status badge color-coded; priority text-coded.
- Filter: `?status=pending` (dropdown autosubmit, works as a form without JS).
- HTMX inline status update: change `<select>` → `POST .../status` →
  swap row. Conflict / validation error → OOB alert.
- ID and Title are clickable — open the task detail page (see §5.5b).
- The ✓ button from the "Ready to start" block also persists here.

### 5.5b. Task detail — `GET /p/{slug}/tasks/{task_id}`

![Task detail](assets/cod-doc/05b-task-detail.png)

Composition: **hero · actions · 2-column body · history**. Each zone is visually
distinct; colors and shapes carry meaning.

- **Hero (top stripe)** — a large `<id>` chip + status-badge on one row,
  below them the title h1. **Border-left** is colored by priority
  (`critical` red, `high` orange, `medium` blue, `low` muted).
  **Background gradient** is softly tinted by status
  (pending — amber, in-progress — sky, done — green, failed — red,
  blocked — orange). On the right in the hero — meta-chips: priority, type, plan link
  (as a clickable pill), commit hash (when present).
- **Actions stripe** — a separate strip: Set status `<select>` (HTMX) +
  ✓ Mark done (green button, hidden in the done status).
- **2-column body:**
  - **Left (2/3 width)** — Description and Acceptance criteria as cards.
    Each card has a header with a `✎ Edit` button for **inline editing**
    (HTMX swap → textarea → Save/Cancel; see below). Markdown is rendered via
    the mini-renderer; the empty state is an italic-hint inviting to click Edit.
  - **Right (1/3 width)** — two compact dep-cards:
    - **Blocked by** — what must complete BEFORE (depth-chip, status-badge,
      task-link with id + truncated title).
    - **Unblocks** — what depends on this (same structure).
- **Revision history** — a full-width card at the bottom. A table + count-chip in
  the header + a link to the full `/revisions?entity_kind=task`. If there are no revisions —
  an italic-hint without visual noise.

#### Inline editing (Description / Acceptance)

![Task detail — edit mode](assets/cod-doc/05c-task-detail-edit.png)

`✎ Edit` → HTMX swap of the card into edit-mode:

- The card gets a soft blue tint, the header changes to `— editing`.
- A textarea with a monospace font, autofocus, the placeholder hints the syntax
  (`**bold**`, `\`code\``, lists, `> quotes`).
- **Save** (primary) → POST `.../fields/{description|acceptance}` with
  an optimistic-concurrency-friendly handler; on success swap back to the
  view-fragment with the already-rendered markdown.
- **Cancel** (secondary) → GET `.../fields/{field}/view` → swap without writing.
- Changes are written as a TASK revision (`op: description`/`acceptance` +
  `old_len`/`new_len`/`old_preview`/`new_preview`). Without HTMX (form-post) →
  303 to the detail page, the history will update on reload.

### 5.6. Plans — `GET /p/{slug}/plans` + `GET /p/{slug}/plans/{plan_id}`

![Plan detail](assets/cod-doc/06-plans.png)

A list of plans with progress → the plan detail page:
- **Header** — scope / principle / status / done/total/percent.
- **Section progress** — a table of sections with a progress bar.
- **Next batch (ready to start)** — top-7 ready, the same `✓` flow as on the dashboard.
- **Dependency graph** — `<pre>` with mermaid-syntax (interactive rendering
  deferred until vendoring `mermaid.min.js` — for now copy-paste into mermaid.live).
- **Raw export** — `<details>` with markdown projections (`progress_overview`,
  `next_batch`).

Cross-project guard: `/p/B/plans/<id-from-A>` → 404, no leak.

### 5.7. Revisions — `GET /p/{slug}/revisions`

![Revisions](assets/cod-doc/07-revisions.png)

- Newest-first project-wide log.
- Filter `?entity_kind=task|document|section|plan&entity_id=42`.
- 6 columns: revision_id / entity#id / author / at / reason / diff first line.
- The diff is shown as a preview (the first 200 chars of the first line).

### 5.8. Settings — `GET /settings` / `POST /settings`

![Settings](assets/cod-doc/08-settings.png)

- API key — masked (`…XXXX`), never in plaintext.
- Basic LLM fields (base_url, model, max_tokens).
- Embedder fields (ADO-071): backend, a separate key, base_url, model,
  dimensions, batch size — configured independently of the LLM (§10.4).
- Agent parameters (max_iterations, daemon interval, auto_commit checkbox).
- **Secret-field UX:** an empty api_key value = "leave as is";
  an explicit hyphen `-` = delete; a non-empty string = replace. The same rule
  applies to `embedding_api_key`.
- POST → 303 to `/settings`, the form works without JS.

---

## 6. CLI reference

All commands go through `cod-doc <group> <action>`. Examples are at the gist level,
the full help: `cod-doc --help`, `cod-doc <group> --help`.

### project

```bash
cod-doc project add /path/to/repo --name my-app   # register
cod-doc project init my-app                  # create .cod-doc/state.db + MASTER.md
cod-doc project list                          # all projects
cod-doc project status my-app                # details
cod-doc project remove my-app                # from the registry (does not touch the repo)
```

### task

```bash
cod-doc task create my-app --plan-id 1 --section-id 1 \
    --title "Implement login" --type feature --priority high
cod-doc task list my-app                     # all
cod-doc task list my-app --status pending    # filter
cod-doc task show my-app MYP-001
cod-doc task status my-app MYP-001 in-progress
cod-doc task complete my-app MYP-001
```

### plan

```bash
cod-doc plan create -p my-app --scope my-plan --section A:Core
cod-doc plan section-create my-plan -p my-app --letter B --title "Next"
cod-doc plan sections my-plan -p my-app --json
cod-doc plan show my-plan -p my-app
cod-doc plan ready my-plan -p my-app          # top-N ready tasks
cod-doc plan audit my-app <plan_id>          # cycles, done-with-unfinished-blocks
cod-doc plan critical-path my-app <plan_id>  # longest sequential chain
cod-doc plan forward my-app <task_id>        # what must complete BEFORE
cod-doc plan reverse my-app <task_id>        # what this task UNBLOCKS
cod-doc plan export my-app <plan_id>         # markdown projections
```

### doc

```bash
cod-doc doc create my-app --doc-key modules/auth/spec \
    --type module-spec --title "Auth Module Spec"
cod-doc doc list my-app
cod-doc doc show my-app modules/auth/spec
cod-doc doc body my-app modules/auth/spec    # full rendered markdown
cod-doc doc rename my-app modules/auth/spec modules/identity/spec
cod-doc doc export my-app modules/auth/spec  # → projection .md file
cod-doc doc drift modules/auth/spec -p my-app # check one DB↔markdown projection
cod-doc doc drift --all -p my-app             # project-wide drift monitor
cod-doc doc import my-app path/to/file.md     # import existing markdown
```

### story (user-stories)

```bash
cod-doc story create my-app --as-a "registered user" \
    --i-want "to reset my password" --so-that "I regain access"
cod-doc story list my-app
cod-doc story show my-app US-001
cod-doc story add-criterion my-app US-001 "Email with reset link is sent within 60s"
cod-doc story coverage my-app US-001         # related tasks/docs
cod-doc story link my-app US-001 --task MYP-001
cod-doc story status my-app US-001 accepted
```

### link

```bash
cod-doc link list my-app                     # all links
cod-doc link verify my-app                   # broken refs?
cod-doc link sync my-app                     # rescan + reparse
```

### revision

```bash
cod-doc revision list my-app --kind task --id 42
cod-doc revision show my-app <revision_id>
cod-doc revision revert my-app <revision_id>  # WHERE supported
```

### audit

```bash
cod-doc audit my-app                         # frontmatter + task-plan + sensitivity
cod-doc audit my-app --strict                # advisory issues also fail the exit-code
```

### embed (embedding provider, ADO-071)

```bash
cod-doc embed status                         # resolve key/endpoint + index state, no network
cod-doc embed status --json                  # same machine-readable; exit 1 if config is broken
cod-doc embed probe                          # one live call: dimension, latency, usage.cost
cod-doc embed models                         # provider's model catalog (OpenRouter has a separate one)
cod-doc embed reset --yes [--force]          # delete the collection after changing model/dimension
```

### serve / mcp / agent / tui / hash

```bash
cod-doc serve [--host 127.0.0.1] [--port 8765] [--reload]   # loopback by default (SYM-003); 0.0.0.0 — via --host or COD_DOC_BIND
cod-doc mcp                                  # MCP server (stdio)
cod-doc agent run my-app                     # interactive agent loop
cod-doc tui                                  # textual-based TUI
cod-doc hash calc my-app modules/foo         # content hash
cod-doc hash update my-app modules/foo
```

---

## 7. Configuration

### 7.1. `~/.cod-doc/config.yaml`

Created automatically. Fields:

```yaml
api_key: sk-or-v1-XXXXXXXX                   # OpenRouter
base_url: https://openrouter.ai/api/v1
model: anthropic/claude-sonnet-4-6
max_tokens: 8192

auto_commit: false                            # auto-git after task done
max_iterations: 50
agent_interval: 60                            # daemon poll seconds

chroma_path: ~/.cod-doc/chroma
embedding_backend: openai                     # openai | openrouter | local | mock (§10.4)
embedding_api_key: ''                         # dedicated embedder key; empty = as before
embedding_base_url: ''                        # dedicated endpoint; empty = base_url
embedding_model: openai/text-embedding-ada-002
embedding_dimensions: null                    # Matryoshka truncation; null = native
embedding_batch_size: 128

api_host: 127.0.0.1                           # loopback by default (SYM-003); POST /settings — only from loopback
api_port: 8765

projects:
  - name: my-app
    path: /Users/me/code/my-app
    enabled: true
    auto_commit: false
    master_md: MASTER.md
```

### 7.2. Environment variables

All fields can be overridden via `COD_DOC_*`:

```bash
COD_DOC_HOME=/data/cod-doc                   # alternative ~/.cod-doc
COD_DOC_API_KEY=sk-or-v1-...
COD_DOC_MODEL=anthropic/claude-sonnet-4-6
COD_DOC_BASE_URL=https://openrouter.ai/api/v1
COD_DOC_AUTO_COMMIT=true
COD_DOC_AGENT_INTERVAL=120
COD_DOC_EMBEDDING_BACKEND=openrouter          # embedder configured independently of the LLM
COD_DOC_EMBEDDING_API_KEY=sk-or-v1-...        # not inherited from COD_DOC_API_KEY
COD_DOC_EMBEDDING_BASE_URL=https://openrouter.ai/api/v1
COD_DOC_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
COD_DOC_EMBEDDING_DIMENSIONS=2048
COD_DOC_EMBEDDING_BATCH_SIZE=128
COD_DOC_API_HOST=127.0.0.1
COD_DOC_API_PORT=9000
COD_DOC_DB_URL=postgresql://user:pass@host/db   # server mode (otherwise embedded sqlite)
LOG_LEVEL=INFO
LOG_FORMAT=text                              # or json
```

The Web UI `/settings` works with the same `Config.save()` — changes go
into `~/.cod-doc/config.yaml` and are picked up on the next lifespan start.

---

## 8. MCP integration

See the separate guide: [docs/mcp-integration.md](mcp-integration.md).

In short: `cod-doc mcp` starts the MCP server over stdio; the tools cover
the same service layer as CLI/Web. Connecting in Claude Desktop:

```json
{
  "mcpServers": {
    "cod-doc": {
      "command": "cod-doc",
      "args": ["mcp"]
    }
  }
}
```

After a restart Claude will see the tools `task_create`, `doc_show`,
`plan_ready`, etc.

---

## 9. AI agent (autonomous mode)

`cod_doc.agent.orchestrator.Orchestrator` — the built-in LLM agent that
reads MASTER.md, builds a task queue, and executes them via the
"LLM → tool → result" loop. Uses any OpenAI-compatible endpoint
(OpenRouter by default).

### 9.1. Snowball Protocol — context levels

The agent works by the principle of minimal context:

| Level | What is loaded | When |
|---------|----------------|-------|
| **L0** | Only MASTER.md (first 4 000 chars) | Always — the starting point |
| **L1** | L0 + one target file (via `get_context`) | When working with a specific document |
| **L2** | L1 + dependencies | Only when explicitly needed |

MASTER.md must contain hybrid references in the format:

```
📁 /path/to/file.ext | 🗃️ doc:sanitized_path | 🔑 sha:12hexchars
```

Document statuses the agent tracks:

- 🟢 **VERIFIED** — the hash matches the file
- 🟡 **DRAFT** — not yet verified
- 🔴 **STALE** — the hash is outdated (the file was changed outside the agent)
- 🔴 **BROKEN** — the file is not found

### 9.2. Agent tools (15)

**File operations:**

| Tool | What it does |
|-----------|-----------|
| `read_file(path, page)` | Reads a file page by page (200 lines / page) |
| `write_file(path, content)` | Creates / overwrites a file |
| `list_files(directory, pattern)` | Glob-listing of a directory |
| `calc_hash(path)` | SHA-256 (12 chars) — for hybrid refs |
| `get_context(ref, depth)` | Loads a document by hybrid ref, validates the hash |
| `update_master_hashes()` | Recomputes all hashes in MASTER.md |
| `make_ref(path)` | Generates a hybrid reference for a new file |

**Task queue:**

| Tool | What it does |
|-----------|-----------|
| `create_task(title, description, priority, context_refs)` | Add a task to the queue |
| `complete_task(task_id, result)` | Close a task with a result |
| `fail_task(task_id, reason)` | Fail a task with a reason |

**Git and search:**

| Tool | What it does |
|-----------|-----------|
| `git_commit(message, files, branch)` | Make a commit (optionally into a new branch) |
| `search_documents(query, n_results)` | Semantic search via ChromaDB |
| `reindex_project()` | Rebuild the vector index |
| `get_project_status()` | Stats + `next_actions` from MASTER.md |
| `ask_human(question, context)` | A blocking question to the operator |

### 9.3. CLI — launching the agent

```bash
# Autonomous mode: the agent generates tasks from MASTER.md and executes them
cod-doc agent run my-app

# Forced task: execute a specific title, then process the queue
cod-doc agent run my-app --task "Update the Data Model section in payments/spec"

# Only the next task from the queue without auto-generation
cod-doc agent run my-app --no-autonomous
```

The console output is streaming (emoji indicators by event type):

```
🤔 thinking  — the agent is reasoning
🔧 tool_call — a tool call
📋 result    — the tool result
💬 message   — the final answer
✅ done      — the task is complete
❌ error     — an error / failure
⛔ blocked   — the agent is waiting for a human response
```

If the agent cannot continue without human input, it calls
`ask_human` → the CLI blocks on `input()`. After the input the agent continues.

### 9.4. Daemon mode

The daemon watches all enabled projects in an infinite loop. In production
it starts automatically when the API server starts (`cod-doc serve`).

```bash
# Start the server — the daemon starts automatically in the background
cod-doc serve

# Look at active tasks
cod-doc task list my-app --status pending

# Configure the poll interval (seconds)
# In config.yaml:
agent_interval: 60    # default
# Or via env:
COD_DOC_AGENT_INTERVAL=120 cod-doc serve
```

Security limits:
- `max_iterations: 50` — max steps per task (protection against infinite loops)
- `auto_commit: false` — the agent does not git-commit without explicit permission

### 9.5. Agent via MCP

```python
# From Claude Desktop / Cursor:
# One agent run (auto-generating tasks from MASTER.md)
run_agent_once(project_name="my-app", autonomous=True)

# Run the next task from the queue
run_agent_once(project_name="my-app", autonomous=False)

# Look at the agent's conversation history
get_agent_context(project_name="my-app")

# Clear the history (reset context)
clear_agent_context(project_name="my-app")
```

---

## 10. Vector database (ChromaDB)

COD-DOC uses ChromaDB as a persistent vector index for
semantic search over project documentation. The index is stored in
`.cod-doc/chroma/` inside the project directory (separately for each project).

### 10.1. What is indexed

Files from four project directories are indexed:

```
specs/      arch/       models/     docs/
```

Formats: `.md`, `.yaml`, `.yml`, `.json`, `.txt`.

Each file is split into chunks of **8 000 characters**. For each chunk
the following metadata is stored:

```
path     — the relative file path
hash     — SHA-256 (12 chars) at indexing time
project  — the absolute path to the project root
```

### 10.2. How to run indexing

```bash
# Via CLI — an explicit request for the agent to reindex
cod-doc agent run my-app --task "reindex project docs"

# Via MCP
reindex(project_name="my-app")
# → {"indexed": 42, "errors": []}

# The agent does this automatically via the reindex_project() tool
# after writing new files via write_file()
```

The first indexing can take a few seconds — embeddings are generated
at an external endpoint (OpenRouter or OpenAI-compatible).

### 10.3. Semantic search

```bash
# Via MCP
search_docs(
    project_name="my-app",
    query="how user authentication works",
    n_results=5
)
# → [{"path": "specs/auth.md", "score": 0.87, "snippet": "...", "hash": "abc123"}, ...]
```

The agent calls `search_documents` on its own when it needs to find
related documents before executing a task.

`score` — cosine similarity (0..1, the higher the more relevant).

### 10.4. Embedding configuration (ADO-071)

The embedding provider is **independent of the LLM provider**: it has its own backend,
key, endpoint, model, and dimension. This is how it should be — not every
chat provider even has `/embeddings` (Ollama Cloud does not: measured,
404 on any model), and earlier switching the LLM silently broke semantic search.

| backend | What it is | Key | Features |
|---|---|---|---|
| `openai` (default) | Any OpenAI-compatible `/embeddings` | `embedding_api_key`, and if empty — the shared `api_key` | Backward compatibility: behaves as before ADO-071 |
| `openrouter` | OpenRouter | **only** `embedding_api_key` | `dimensions` for any models, `usage.cost`, its own model catalog |
| `local` | sentence-transformers on CPU | not needed | Requires `pip install 'cod-doc[embeddings-local]'` |
| `mock` | Deterministic | not needed | For tests |

**The OpenRouter key is not inherited from the LLM key** — these are different keys, and
silent inheritance would give a 401 indistinguishable from a cod-doc bug.

```yaml
# ~/.cod-doc/config.yaml — working example: chat in Ollama Cloud, embeddings in OpenRouter
base_url: https://ollama.com/v1          # LLM
model: qwen3.5:397b                      # LLM
chroma_path: ~/.cod-doc/chroma

embedding_backend: openrouter
embedding_api_key: sk-or-v1-…            # separate key
embedding_model: qwen/qwen3-embedding-8b
embedding_dimensions: 2048               # Matryoshka truncation on the provider side
embedding_batch_size: 128
```

Or via env: `COD_DOC_EMBEDDING_BACKEND`, `COD_DOC_EMBEDDING_API_KEY`,
`COD_DOC_EMBEDDING_BASE_URL`, `COD_DOC_EMBEDDING_MODEL`,
`COD_DOC_EMBEDDING_DIMENSIONS`, `COD_DOC_EMBEDDING_BATCH_SIZE`.

**`embedding_dimensions`** — truncating the vector on the provider side. For
`qwen/qwen3-embedding-8b` the native 4096 vs 2048 are measured as lossless, and
the index is half the size. The stock chroma-EF can only pass this parameter
to `text-embedding-3-*` models, so for the rest cod-doc uses its own adapter.

**Collection signature.** On creation, the metadata stores
`cod_doc:embedding = <backend>:<model>@<dimensions|native>`. If the config
diverges with a non-empty index, cod-doc fails with a clear error instead of
garbage output: vectors of different models are incomparable. The only correct action is
`cod-doc embed reset --yes` and reindexing.

**Your own adapter.** A provider can be added without touching the core: implement the
`cod_doc.core.embeddings.base.EmbeddingAdapter` protocol (with a classmethod
`from_settings`) and register it in `~/.cod-doc/embeddings.json`:

```json
[{"name": "my-embed", "module": "my_pkg.adapter", "class": "MyEmbeddingAdapter"}]
```

Diagnostics:

```bash
cod-doc embed status        # resolve key/endpoint, index state — no network
cod-doc embed probe         # one live call: dimension, latency, cost
cod-doc embed models        # provider's model catalog (OpenRouter has a separate one)
cod-doc embed reset --yes   # delete the collection after changing the model
```

### 10.5. Troubleshooting the index

| Symptom | Cause | Solution |
|---------|---------|---------|
| `search_docs` returns an empty list | The index is not created | Run `reindex` |
| Stale results (old content) | Files changed after the last indexing | Run `reindex` |
| `ChromaDB connection error` | The `chroma/` directory is corrupted | `rm -rf .cod-doc/chroma && reindex` |
| Slow indexing | Many files or a slow embedding endpoint | Decrease `embedding_batch_size` or change the model |
| `404 … no route /embeddings` | The LLM provider does not serve embeddings (Ollama Cloud) | `embedding_backend: openrouter` + `embedding_api_key` |
| `401` with `backend=openrouter` | The embedder key is not set: it is **not** inherited from `api_key` | Fill in `embedding_api_key` |
| `dimension of N, got M` | The model/dimension changed, the index was built with another | `cod-doc embed reset --yes`, then reindex |
| Search silently empty, no errors | Fail-open: consumers swallow the embedder error | `cod-doc embed status` and `cod-doc embed probe` |

---

## 11. Typical workflows

### 9.1. Document a new module

```bash
# 1. Create a stub document
cod-doc doc create my-app \
    --doc-key modules/payments/spec \
    --type module-spec \
    --title "Payments Module Spec" \
    --owner "human:dakh"

# 2. Open in Web → /p/my-app/docs/modules/payments/spec
#    Click ✎ next to the "Data Model" section → edit → Save

# 3. From CLI: add a section programmatically
.venv/bin/python <<'PY'
from cod_doc.config import Config
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.services import doc_service as docs

cfg = Config.load()
entry = cfg.get_project("my-app")
engine = make_engine(f"sqlite:///{entry.cod_doc_dir}/state.db")
factory = make_session_factory(engine)
with transactional(factory) as s:
    # ... lookup proj_id and doc_id, then:
    docs.add_section(s, document_id=DOC_ID, anchor="api",
                     heading="API", level=2, position=1,
                     body="POST /payments/intent\nPOST /payments/attempt/{id}",
                     author="human:dakh")
PY
```

### 9.2. Manage task progress

```bash
# Via CLI
cod-doc task status my-app PAY-001 in-progress
# ... work ...
cod-doc task complete my-app PAY-001

# Via Web — press ✓ in the Ready block on the dashboard or go to /tasks
# and change the status via the select.

# Via MCP — Claude calls task_status via its own tooling.
```

### 9.3. Roll back a change

```bash
cod-doc revision list my-app --kind section --id 42
# 01HXXX...   2026-05-02 16:55  human:web  : web inline section patch
# 01HYYY...   2026-05-02 16:30  human:dakh : add_section
# ...

cod-doc revision revert my-app 01HXXX...
# Restores the section body to the state before this revision
# (supported for SECTION; for TASK — set status, for DOC — TBD).
```

### 9.4. Run an audit before merge

```bash
cod-doc audit my-app --strict
# Frontmatter:  FM-002 (3) FM-003 (1) FM-004 (12) ...
# Task-plan:    TP-001 (0) TP-002 (1) TP-003 (0) ...
# Sensitivity:  SD-001 high-conf-leak: 0
# Exit code:    1   ← due to advisory FM-004 in strict mode
```

---

## 12. Troubleshooting

### The container 500s on the project page

`TemplateNotFound: '_layout/project_tabs.html'` or similar —
means the installed package lags behind the sources. Rebuild:
```bash
docker compose build cod-doc && docker compose up -d cod-doc
```

`pyproject.toml` `package-data` globs must cover all folders in
`templates/web/` and `static/` (see the fix `a0861cb`). A regression AST test
keeps the web layer clean, but a new template-subdir = a new glob.

### KPI cards on the dashboard show 0, but Plan progress — real numbers

Should not happen anymore — closed by the fix `e8512e6`. If it reproduces:
- Most likely, `.cod-doc/tasks.yaml` is empty (legacy YAML) and the container
  runs an old version. Rebuild.

### "No revisions yet." with existing tasks

This is a **data state**, not a bug. 31 tasks in the DB were created directly
(SQL/fixture), bypassing `task_service.create` (which writes a revision).
All new operations via the service layer automatically fill the log.

To start from a clean slate:
```bash
rm -rf .cod-doc
cod-doc project init my-app  # recreate
```

### MASTER.md shows test garbage

```bash
rm /path/to/repo/MASTER.md
cod-doc project init my-app  # init will not overwrite an existing one
```

`Project.init()` intentionally does not overwrite MASTER.md — a defensive default
(so it does not wipe your real navigator). Delete it manually.

### `Internal Server Error` without TemplateNotFound

`docker logs cod-doc --tail 100` or `journalctl -u cod-doc-serve -e` for
systemd. The stack trace will show the specific cause. The most common:
- Schema mismatch — old `state.db`, run `alembic upgrade head`.
- The slug in config.yaml does not match `project.slug` in the DB — recreate
  via `cod-doc project init`.

### HTMX does not work (forms reload the whole page)

- Check that `/static/htmx.min.js` is served 200, not 404.
  ```bash
  curl -I http://localhost:8765/static/htmx.min.js
  ```
  404 = the `package-data` glob does not cover `static/*.js` → rebuild.
- The `<noscript>` fallback also works — `<form method="post">` sends a normal
  POST, the handler returns a 303-redirect. The UI always graceful-degrades.

### `database is locked` / DB on a network drive

Starting with SYM-002 the file SQLite opens in WAL mode
(`journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`) — the writer
no longer blocks readers, and a competing write waits up to 5 seconds instead
of failing instantly. Practical consequences:

- Next to `state.db` appear `state.db-wal` and `state.db-shm`. If you copy the DB
  manually — copy all three files or first run
  `PRAGMA wal_checkpoint(TRUNCATE)`, otherwise you lose the tail of entries.
- **WAL requires normal file locks and only works on a local disk.** iCloud Drive,
  Dropbox, NFS, and SMB shares break WAL — keep `.cod-doc/` on a local filesystem.
- Still seeing `database is locked` — means there are more than two writers or
  a transaction hangs for more than 5 seconds: look for a long operation, do not crank the timeout.
- An in-memory DB (`sqlite://`) does not get the WAL pragma — it has no journal.

---

## 13. Testing and development

### 11.1. pytest

```bash
.venv/bin/pytest tests/ -q             # full suite (~512 tests, ~110s)
.venv/bin/pytest tests/api/ -q         # web only (~137, ~30s)
.venv/bin/pytest tests/services/ -q    # service layer
.venv/bin/pytest tests/infra/ -q       # repositories + migrations
```

CI block (locked since COD-024a):
- pytest matrix `3.11 / 3.12 / 3.13` — blocking
- ruff — blocking
- mypy strict — blocking

### 11.2. Lint / types

```bash
.venv/bin/ruff check cod_doc tests
.venv/bin/ruff format cod_doc tests
.venv/bin/mypy cod_doc
```

### 11.3. Playwright e2e (optional)

Not part of CI; the runner lives in `.cod-doc-playwright-*.py` (untracked).
A run:

```bash
# 1. Seed sandbox (creates /tmp/cod-doc-playwright-sandbox/)
.venv/bin/python .cod-doc-playwright-seed.py

# 2. Start the server on the seed
COD_DOC_HOME=/tmp/cod-doc-playwright-sandbox/home \
    .venv/bin/uvicorn cod_doc.api.server:app --port 8765

# 3. Run the scenario
.venv/bin/python .cod-doc-playwright-run.py
# → Playwright e2e summary: 23 passed, 0 failed

# 4. Screenshots for the docs
.venv/bin/python .cod-doc-playwright-screenshots.py
# → /tmp/cod-doc-playwright-shots/{01..08}.png
```

### 11.4. Migrations

```bash
# A new migration
alembic revision --autogenerate -m "0010_add_payment_tables"

# Apply
alembic upgrade head

# Roll back one step
alembic downgrade -1

# Check the state
alembic current
alembic history --verbose
```

`COD_DOC_DB_URL` controls the target: `sqlite:///path/to/state.db` for
embedded, `postgresql://...` for server mode.

### 11.5. Architecture Decision Records (ADR)

Closes task `ADR-008`. ADRs capture decisions that survived design review and
should outlive contributor turnover.

**When to write one.** Anytime the answer to "why is it _this way_ and
not the obvious alternative?" is non-trivial and would cost more than 30
minutes to rederive. Examples: storage layer choice, async vs. sync,
build tool, message format, embedding model. Bug fixes don't get ADRs.

**Lifecycle.**

```
proposed  ─→  accepted  ─→  superseded   (replaced by another ADR)
              │             deprecated   (abandoned without replacement)
              └─→  rejected (decided not to do)
```

**Storage.** ADRs live in the project DB (tables `adr`, `adr_diagram`,
`adr_supersedes`, `adr_task` — migration `0018_adr_tables`). The
canonical 5-row table layout from `arch/architecture.md §5` was the
historical source of truth; running
`cod_doc.services.adr_migrator.migrate_from_file` back-ports legacy
markdown ADRs into the DB. Idempotent — re-running skips already-imported
ids.

**Entry points.**

| Surface | How to use it |
|---|---|
| **Web** | `/p/<slug>/adr` (list), `/adr/new` (form), `/adr/<id>` (detail + edit + diagram-attach + supersede), `/adr/graph` (Mermaid supersede DAG) |
| **CLI** | `cod-doc adr new -p <slug> --title "..." --status accepted` · `adr list` · `adr show <id>` · `adr supersede <new> <old>` · `adr graph --format mermaid\|json` |
| **MCP** | 8 tools under `--profile standard\|full`: `adr_create`, `adr_get`, `adr_list`, `adr_update`, `adr_add_diagram`, `adr_supersede`, `adr_link_task`, `adr_graph` |
| **Migrator** | `cod_doc.services.adr_migrator.migrate_from_file(session, project_id, md_path)` |

**ADR ↔ task link.** When a task implements / invalidates / discovers
an ADR, record it via `adr_link_task` (or the web supersede form) with
relation `implements | invalidates | discovers | relates`. The link
surfaces on both the task page and the ADR detail page.

**Supersede semantics.** `adr_supersede(new, old)` is idempotent: it
creates the DAG edge AND auto-flips the old ADR's status to
`superseded` in one transaction. Self-supersede (`new == old`) raises.

### 11.6. Architectural rules (auto-enforced)

- **The web layer does not import `cod_doc.infra.*`** —
  `tests/api/test_web_layer_imports.py` catches regressions via AST scanning.
- **Services write a revision on every mutation** — empty revision tables =
  a signal that something bypasses the service layer.
- **Frontmatter validation** — `FM-002` (active without owner) and `FM-003`
  (sot=false without canonical_source) are escalated into a `ValidationError` on
  the write-path; freshness rules (`FM-004/005`) are advisory.

---

## What's next

- **Target architecture:** [docs/system/MASTER.md](system/MASTER.md) — the package
  of capability documents and standards (source of truth).
- **Roadmap:** [docs/system/roadmap/cod-doc-task-plan.md](system/roadmap/cod-doc-task-plan.md)
  and [web-frontend-task-plan.md](system/roadmap/web-frontend-task-plan.md).
- **Audit trail:** [docs/system/audit/](system/audit/) — closed sections
  and checkpoints.
- **Restate migration guide:** [docs/system/migration/from-restate.md](system/migration/from-restate.md).
