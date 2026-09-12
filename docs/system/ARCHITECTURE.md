---
type: architecture
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_code:
  - cod_doc/core/
  - cod_doc/agent/
  - cod_doc/api/
  - cod_doc/mcp/
  - cod_doc/cli/
---

# COD-DOC — Architecture

A layered, modular architecture. No layer references the layer above it.

```text
┌─────────────────────────────────────────────────────────────┐
│  Presentation                                               │
│   CLI (click)   TUI (textual)   REST API (FastAPI)   MCP    │
└──────────────┬──────────────┬────────────┬────────────┬─────┘
               │              │            │            │
               ▼              ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────┐
│  Application / Services                                     │
│   DocService    TaskService   PlanService   GraphService    │
│   ContextService  RevisionService  LinkService  StoryService│
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Domain                                                     │
│   Document  Task  Link  Revision  UserStory  Dependency     │
│   Plan  Section  Tag  Project  Module                       │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Infrastructure                                             │
│   SQLite/Postgres  FS (markdown projections)   Git hooks    │
│   Embeddings store (sqlite-vss / pgvector)  LLM provider    │
└─────────────────────────────────────────────────────────────┘
```

## 1. Presentation layer

| Surface | Purpose | Constraints |
|-------------|-----------|-------------|
| CLI `cod-doc` | Human operator, scripts, CI | No direct SQL queries; only through services |
| TUI `cod-doc wizard`/`dashboard` | Interactive work, onboarding | Uses the same CLI layer via in-process calls |
| REST API (`cod_doc/api`) | Web clients, external integrations | Stateless, authentication via project token |
| MCP (`cod_doc/mcp`) | LLM agents | A pair of tools per service action; no intermediate shell |

All four surfaces are **equal**: if functionality is added to a service, it must appear in the CLI and MCP within at least half an hour, so that the agent and a human have an identical interface (a rule inherited from Restate: `task-plan-ecosystem.md §6.4` — "the CLI remains the canonical alternative to MCP").

## 2. Application layer (services)

| Service | Responsibility |
|--------|----------------|
| `DocService` | Document CRUD, skeleton generation, markdown import/export |
| `TaskService` | Task CRUD, format validation (ID, type, title-verb-pattern) |
| `PlanService` | Recompute `tasks_done`/`tasks_total`, Progress Overview, Next Batch |
| `GraphService` | Dependencies, critical path, cycles, reverse chain |
| `ContextService` | Build "concentrated context" on demand (L0/L1/L2) |
| `RevisionService` | Write revisions, diff generation, rollback |
| `LinkService` | Resolve internal links, detect broken ones, update on rename |
| `StoryService` | User stories, link stories to tasks and modules |

Services are transactional: any operation either commits entirely or rolls back. Each write action is accompanied by a record in `Revision`.

## 3. Domain layer

Pure entities — see [DATA_MODEL.md](DATA_MODEL.md). They have no external dependencies (no SQLAlchemy models in the domain; repositories live at the infrastructure level and return dataclass entities).

## 4. Infrastructure layer

### 4.1 Storage

Two profiles:

| Profile | DBMS | Purpose |
|---------|------|-----------|
| `embedded` | SQLite in `.cod-doc/state.db` | A single local project, no server |
| `server` | PostgreSQL | Teamwork, CI, multiple clients per project |

The DB schema is shared; dialects differ (`JSON` vs `JSONB`, `TEXT` vs `VARCHAR`, `INTEGER` vs `BIGINT`). Migrations are managed by Alembic.

### 4.2 Markdown projection

`.cod-doc/mirror/` — a tree of markdown files mirroring the DB. Not sources, but **artifacts**. Rules:

- `export` regenerates all files deterministically.
- `import` parses files and tries to apply changes through services (not via direct DB writes).
- The hash of each file is stored in `Document.projection_hash`. If the on-disk hash does not match the last exported hash, the file is considered edited-in-place, and reconciliation is triggered.

### 4.3 Embeddings

For concentrated-context retrieval (`ContextService`, level L3), a vector index of documents is stored. The implementation is ChromaDB (`PersistentClient` in `chroma_path`, collection `cod_doc`, cosine metric); the previously announced `sqlite-vss`/`pgvector` are not implemented.

**The embedding provider is a config choice and a separate axis from the LLM (ADO-071).** `cod_doc/core/embeddings/` is a registry of adapters (`EmbeddingAdapter` + `registry.py`, external plugins from `~/.cod-doc/embeddings.json`), structured the same way as the LLM adapter registry. Built-in: `openai` (any OpenAI-compatible endpoint), `openrouter` (own key, `dimensions`, `usage.cost`), `local` (sentence-transformers), `mock`. The core (`core/reindex.py`) accepts `EmbeddingSettings` and does not know any provider by name.

The collection identity is fixed by the signature `<backend>:<model>@<dimensions>` in its metadata: vectors of different models are not comparable, so a config mismatch with a non-empty index is an explicit error requiring the index to be rebuilt, not a silent degradation.

Indexing happens on explicit call (`reindex_project`); search is fail-open: any backend error yields an empty list, while `cod-doc embed status/probe` gives a loud signal.

### 4.4 LLM provider

The provider choice is a config (OpenRouter, Anthropic API, local Ollama). The domain and services do not know about the specific provider; the agent/orchestrator does. This is inherited from the current cod-doc (see `cod_doc/agent/orchestrator.py`, `cod_doc/config.py`). The embedding provider is chosen **independently** (§4.3): the chat provider may not have `/embeddings` at all.

## 5. Data flows

### 5.1 Creating a task

```text
human|agent
   │  task create ...
   ▼
CLI/MCP ──► TaskService.create()
               │
               ├─► validates the format (task-plan.md §5)
               ├─► computes the ID within the section range
               ├─► writes the Task to the DB
               ├─► writes a Revision
               ├─► triggers PlanService.recalc(plan_id)
               └─► triggers LinkService.reindex(doc=section_file)
```

### 5.2 Modifying a document

```text
DocService.apply_patch(doc_id, patch)
   │
   ├─► applies the diff to the canonical body (in the DB)
   ├─► re-resolves outgoing links (LinkService)
   ├─► writes a Revision(diff, author, reason)
   ├─► queues the task for re-embedding
   └─► on export — updates the markdown projection
```

### 5.3 Agent's context request

```text
MCP: context.get(module="M1-auth", depth="L1")
   │
   ▼
ContextService.build(target, depth)
   │
   ├─► L0: only MASTER + explicit target
   ├─► L1: + direct links (module-spec, open task-plan, last 3 open stories)
   ├─► L2: + depends_on chains, nearest open questions, cross-module dependencies
   │
   └─► returns JSON + markdown-excerpts under a token budget
```

## 6. Contracts between layers

- Presentation → Application: typed DTOs (pydantic).
- Application → Domain: dataclass entities.
- Domain → Infrastructure: abstract repositories (`Protocol`), implementations in infra.

Not allowed:

- Writing SQL directly in the MCP server.
- Depending on `sqlite3`/`psycopg` in the domain.
- Duplicating business logic in the CLI that is not in the service (if needed — service first).

## 7. Inversion of dependencies

`cod_doc/core/project.py` already implements part of the domain (Task, TaskStatus). Migration to the target architecture:

1. Extract `cod_doc/domain/` with pure entities.
2. Keep `cod_doc/core/` as a backward-compat shim until all consumers have migrated.
3. Introduce `cod_doc/services/` (Doc, Task, Plan, …).
4. Introduce `cod_doc/infra/repositories/` with adapters for SQLite/Postgres.
5. Rewrite `cod_doc/cli/`, `cod_doc/mcp/`, `cod_doc/api/` on top of services.
6. Remove the shim.

The order is iterative, see [roadmap/cod-doc-task-plan.md](roadmap/cod-doc-task-plan.md).

## 8. Deployment and environments

| Profile | When | DB | MCP | Auth | Projection |
|---------|-------|-----|-----|------|------------|
| **embedded** | single developer | SQLite `.cod-doc/state.db` | stdio | implicit OS user | FS mirror required |
| **server** | team, single host | Postgres | stdio and/or localhost HTTP | token (spec §12) | FS volume |
| **cloud** | remote AI agents | Postgres | streamable-http + TLS | Bearer enforced | optional export |

- **Local embedded**: no REST, only CLI + MCP.
- **Shared Postgres (server)**: docker-compose stack, REST API active;
  MCP can live on the user's machine and talk to a shared Postgres.
- **Cloud agent plane** (target): one team node in the cloud; several
  independent AI clients (Cursor Cloud, Claude, orchestrator) —
  decentralized workers via remote MCP; SoT = DB; markdown —
  optional projection. See
  [capabilities/cloud-agent-plane.md](capabilities/cloud-agent-plane.md)
  and [roadmap/cloud-agent-plane-task-plan.md](roadmap/cloud-agent-plane-task-plan.md).
- **CI**: headless; CLI (`cod-doc audit`, `cod-doc plan next`,
  `cod-doc link verify`).

Switching is done via `COD_DOC_DB_URL` (+ `COD_DOC_AUTH` / MCP transport
for cloud).

## 9. Security

- Project data does not leave the DB without an explicit export.
- The built-in LLM client does not see document contents beyond what ContextService put into the session.
- Audit log of all write operations via MCP/REST/CLI/TUI — in the `Revision` (what changed) and `ActivityEvent` (who did what) tables, see [DATA_MODEL.md §3.13](DATA_MODEL.md). Written with a single atomic call to `activity_service.write_revision_and_emit_event` inside the mutation transaction (ADO-040). There is no separate `AuditLog` table anymore — it was declared but never got a single writer throughout the project's history and was removed per ADR-012.
- **`/api/v1` — the only surface of the future Bearer gate** (RFC 22 §3.3 contract, see `proposals/22-symbiosis-zairgrush-orakul.md`): `COD_DOC_API_TOKEN`, ASGI middleware only on `/api/v1` (`cod_doc/api/v1/`), constant-time compare, 401 JSON. The gate is enabled when the first remote caller appears. Legacy `/api/*` is frozen and will not get a Bearer gate.

## 10. Error Model

A unified set of domain/service-level exceptions (`cod_doc.errors`). All surfaces (CLI, MCP, REST, TUI) map them to their own format.

### 11.1 Hierarchy

```
CodDocError
├── ValidationError       — format violation (frontmatter, task verb-pattern, enum)
├── NotFoundError         — target entity does not exist
├── ConflictError         — state does not allow the operation
│   ├── DependencyError   — unsatisfied depends_on
│   ├── CycleError        — attempt to create a cycle in the graph
│   └── OptimisticLockError — parent_revision_id is stale (see §11)
├── AuthDeniedError       — actor has no right to the tool / sensitivity
├── IntegrityError        — schema / FK / uniqueness violation
└── ExternalError         — external service failure (LLM, embeddings, git)
```

Each exception carries:

```python
class CodDocError(Exception):
    code: str         # 'TP-004', 'FM-001', 'AUTHZ-001' — stable for integrations
    message: str      # human-readable
    details: dict     # structured fields (entity_id, suggestion, ...)
```

### 11.2 Mapping per surface

| Error → | CLI exit | MCP isError + payload | REST status | TUI |
|---------|---------:|------------------------|-------------|-----|
| ValidationError    | 2 | `{"code":"FM-001",...}` | 400 | inline form error |
| NotFoundError      | 3 | ↑ | 404 | toast |
| ConflictError      | 4 | ↑ | 409 | modal |
| AuthDeniedError    | 5 | ↑ | 403 | modal |
| IntegrityError     | 6 | ↑ | 500 | crash screen |
| ExternalError      | 7 | ↑ | 502 | retry-toast |
| Unknown / panic    | 1 | ↑ | 500 | crash screen |

### 11.3 Write-path rule

Any error in a transaction = full rollback.
No partial updates: either the entire set of changes (task + dependency + revision + section_totals refresh) is applied, or none.
The mutation trace (`revision` + `activity_event`) is written **inside** the same transaction as the mutation itself — there is no separate pre-commit journal (ADR-012). A write-path error leaves no record at all: the transaction rolls back entirely.

### 11.4 Idempotency

- `task.create` accepts an `idempotency_key` (optional); a repeated call with the same key returns the original result.
- `doc.patch_section` is idempotent by `parent_revision_id` — a repeated apply of the same patch with the same parent yields the same revision_id (deterministic ULID with the `--deterministic` flag).

## 11. Concurrency & Identity

### 12.1 Optimistic locking

Writing a revision requires `parent_revision_id` — the latest known revision of the entity. If a new one has appeared in the meantime — `OptimisticLockError`. The client re-reads the state and retries.

### 12.2 Identity

Each actor has a record in the `actor` table (separate from `agent_definition`):

```sql
CREATE TABLE actor (
  row_id     INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(row_id),
  kind       TEXT    NOT NULL,    -- 'human'|'agent'|'mcp'|'system'
  handle     TEXT    NOT NULL,    -- 'dakh','task-steward','claude-code'
  token_hash TEXT,                -- SHA256 for server-profile; NULL for embedded
  created    TEXT    NOT NULL,
  UNIQUE(project_id, kind, handle)
);
```

Embedded profile: a single implicit actor `human:<os-user>` without a token.
Server profile: REST/MCP require `Authorization: Bearer <token>`; the token resolves to `actor.handle`. CLI locally on the server — via keyring.

### 12.3 Authz

Before every tool call:

1. Resolve the actor.
2. Check allowed_tools/denied_tools (see [capabilities/agents-and-skills.md §3](capabilities/agents-and-skills.md)).
3. Check sensitivity_clearance vs target document (see [standards/sensitive-data.md §3](standards/sensitive-data.md)).
4. On deny — `AuthDeniedError(code='AUTHZ-001'|'AUTHZ-002')`. Denials are not logged yet: `activity_event` records only completed mutations, and `audit_log` is removed (ADR-012). The gate is inactive — it is enabled together with the `/api/v1` Bearer gate.

## 12. References

- [VISION.md](VISION.md)
- [DATA_MODEL.md](DATA_MODEL.md)
- [capabilities/context-retrieval.md](capabilities/context-retrieval.md)
- [roadmap/cod-doc-task-plan.md](roadmap/cod-doc-task-plan.md)
