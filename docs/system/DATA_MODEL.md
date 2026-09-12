---
type: data-model
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-25
---

# COD-DOC — Data Model

> A normalized storage schema. Markdown is a projection of this schema.
> The SQLite dialect is specified as the base; for Postgres — see comments.

## 1. Entity overview

```text
Project ─┬─< Document ─┬─< Section ─┬─< Block
         │             │            └─< Link (out)
         │             └─< Revision
         │
         ├─< Plan ─┬─< Plan.Section ─┬─< Task ─┬─< Task.Revision
         │        │                  │        ├─< Dependency (out)
         │        │                  │        └─< AffectedFile
         │        │                  │
         │        │                  └─< SectionTotals (view)
         │        │
         │        └─< NextBatchCache
         │
         ├─< UserStory ─┬─< StoryLink (to Document / Task / Module)
         │              └─< StoryAcceptance
         │
         ├─< Module ─┬─< ModuleDependency (module→module)
         │           └─< ModuleCode (paths)
         │
         ├─< Tag ──< DocumentTag, TaskTag, StoryTag
         │
         └─< ActivityEvent (everything that went through the write-path) ──< AgentRun (run_id)
```

## 2. Key invariants

- All external entity IDs (task, module) are human-readable (`AUTH-025`, `M1-auth`). The DB also stores a surrogate `row_id` BIGINT PK.
- `Revision` — immutable history; never updated, append-only.
- `Link` — a direct reference `(from_doc, to_ref)`; the resolution to `to_doc_id` is cached but always re-checked on read.
- `Task.status` — an enum of 3 values; `Plan.status` — computed, not stored.
- `tasks_done` / `tasks_total` of a section — stored in `SectionTotals` as a materialized view with a trigger on task changes.

## 3. Tables

### 3.1 `Project`

```sql
CREATE TABLE project (
  row_id       INTEGER PRIMARY KEY,
  slug         TEXT    NOT NULL UNIQUE,   -- 'restate', 'weather-cli'
  title        TEXT    NOT NULL,
  root_path    TEXT    NOT NULL,
  created      TEXT    NOT NULL,
  updated      TEXT    NOT NULL,
  config_json  TEXT    NOT NULL DEFAULT '{}'
);
```

### 3.2 `Document`

`Document` stores **only metadata and frontmatter**. The body is not duplicated here — it is assembled from `section.body` (see §3.3) via the view in §4.

```sql
CREATE TABLE document (
  row_id           INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES project(row_id),
  doc_key          TEXT    NOT NULL,             -- 'modules/M1-auth/overview'
  path             TEXT    NOT NULL,             -- relative projection path
  type             TEXT    NOT NULL,             -- 'module-spec','execution-plan',...
  status           TEXT    NOT NULL,             -- frontmatter.md §Status
  source_of_truth  INTEGER NOT NULL DEFAULT 1,   -- boolean
  sensitivity      TEXT    NOT NULL DEFAULT 'internal',  -- public|internal|confidential|restricted
  owner            TEXT,
  title            TEXT    NOT NULL,
  preamble         TEXT    NOT NULL DEFAULT '',  -- text before the first H2 (short description/intro)
  frontmatter_json TEXT    NOT NULL DEFAULT '{}',
  frontmatter_raw  TEXT,                         -- ADO-010: YAML block as in the file; '' = the file had no frontmatter, NULL = the document was created in the DB OR the row was created before migration 0025 (ADO-022)
  title_in_body    INTEGER,                      -- ADO-010: whether the source had '# H1' (NULL = unknown → H1 is rendered; see ADO-022)
  projection_hash  TEXT,                         -- hash of the last export
  created          TEXT    NOT NULL,
  last_updated     TEXT    NOT NULL,
  last_reviewed    TEXT,
  UNIQUE(project_id, doc_key)
);
CREATE INDEX ix_document_type ON document(type, status);
CREATE INDEX ix_document_sensitivity ON document(sensitivity);
```

> **ADO-022.** The pair `frontmatter_raw` / `title_in_body` has two different sources of NULL, and they cannot be distinguished by the row itself: a document created in the DB (`doc create`) and a document imported before the `0025_projection_fidelity` migration. For the first, NULL is true; for the second, it is data loss: the render re-serializes the frontmatter from `frontmatter_json` and appends `# H1` that was not in the source. Therefore `doc export` refuses to overwrite such a file, and it is fixed with `cod-doc doc backfill-projection` (MCP: `doc_backfill_projection`) — restoring exactly these two columns from disk, without rolling back the metadata that was changed in the DB.

### 3.3 `Section`

Document sections are the **canonical carrier of the body**. Fine-grained edits, local revisions, embeddings, links — everything is attached to the section, not to the document.

```sql
CREATE TABLE section (
  row_id       INTEGER PRIMARY KEY,
  document_id  INTEGER NOT NULL REFERENCES document(row_id),
  anchor       TEXT    NOT NULL,  -- 'data-model'
  heading      TEXT    NOT NULL,
  level        INTEGER NOT NULL,  -- 1..6
  position     INTEGER NOT NULL,  -- order within the document
  body         TEXT    NOT NULL,  -- canonical body of this section
  content_hash TEXT    NOT NULL,  -- sha256(body) — for embedding invalidation and drift detection
  UNIQUE(document_id, anchor)
);
CREATE INDEX ix_section_position ON section(document_id, position);
```

> **Decision DOC-HI-8:** `Document.body` is abolished; `Section.body` is the single source. The full document body is assembled via the `document_body` view (§4.4).

### 3.4 `Link`

Outgoing links extracted from a section body.

```sql
CREATE TABLE link (
  row_id           INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES project(row_id),
  from_section_id  INTEGER NOT NULL REFERENCES section(row_id),
  raw              TEXT    NOT NULL,  -- as written: '[[M1 AUTH v2]]' or '../M1 AUTH v2.md'
  kind             TEXT    NOT NULL,  -- 'wiki','markdown','url','task','story'
  to_doc_key       TEXT,
  to_task_id       TEXT,
  to_story_id      TEXT,
  resolved         INTEGER NOT NULL DEFAULT 0,
  last_checked     TEXT,
  broken_reason    TEXT
);
CREATE INDEX ix_link_target_doc  ON link(to_doc_key);
CREATE INDEX ix_link_target_task ON link(to_task_id);
CREATE INDEX ix_link_broken      ON link(resolved) WHERE resolved = 0;
```

### 3.5 `Revision`

A universal immutable history.

```sql
CREATE TABLE revision (
  row_id       INTEGER PRIMARY KEY,
  revision_id  TEXT    NOT NULL UNIQUE,    -- ULID, 26 chars: '01HQX5Z9F0K8R...'
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  entity_kind  TEXT    NOT NULL,   -- see enum below
  entity_id    INTEGER NOT NULL,   -- row_id of the corresponding entity (polymorphic, no FK)
  parent_revision_id TEXT,         -- previous revision of the same entity; NULL if first
  author       TEXT    NOT NULL,   -- 'agent:task-steward','human:dakh','mcp:claude'
  at           TEXT    NOT NULL,   -- ISO-8601; must match the timestamp in revision_id
  diff         TEXT    NOT NULL,   -- unified diff or JSON-patch
  reason       TEXT,
  commit_sha   TEXT,               -- if tied to a git commit
  run_id       TEXT                -- orchestrator telemetry; de facto always NULL, see §3.13.1
);
CREATE INDEX ix_revision_entity ON revision(entity_kind, entity_id, at);
CREATE INDEX ix_revision_parent ON revision(parent_revision_id);
```

**`entity_kind` ∈** `'document' | 'section' | 'task' | 'plan' | 'story' | 'link' | 'module'`. The service initiating the revision is responsible for the correctness of `entity_kind + entity_id`.

**Polymorphic `entity_id` without FK.** Intentional: append-only history must survive the deletion of the target entity (audit invariant). A cascade from the parent table does NOT affect revision; "orphan" revisions are normal and are read by `entity_kind + entity_id` over the project's lifetime.

**`revision_id` = ULID** ([Crockford-base32, 128 bits, lexicographically sortable](https://github.com/ulid/spec)).
The first 48 bits are a timestamp with millisecond precision; the remaining 80 are random.

Why ULID instead of AUTOINCREMENT INTEGER:
- sorts by time without a separate `at` index;
- generated on the client without a DB round-trip (important for the propose-flow and offline sessions);
- safely merged from multiple replicas (no sequence-counter conflict).

`parent_revision_id` provides optimistic concurrency control: the write-path in a transaction does `WHERE revision_id = (SELECT MAX(revision_id) FROM revision WHERE entity ...)`, and if someone else managed to write earlier — a conflict.

### 3.6 `Plan` and `Plan.Section`

```sql
CREATE TABLE plan (
  row_id          INTEGER PRIMARY KEY,
  project_id      INTEGER NOT NULL REFERENCES project(row_id),
  scope           TEXT    NOT NULL UNIQUE,  -- 'M1-auth-module','infra-cors'
  principle       TEXT,                     -- 'test-first','fix-first'
  module_id       TEXT,                     -- 'M1-auth'
  parent_doc_id   INTEGER REFERENCES document(row_id),
  completed_log_id INTEGER REFERENCES document(row_id),
  created         TEXT NOT NULL,
  last_updated    TEXT NOT NULL
);

CREATE TABLE plan_section (
  row_id      INTEGER PRIMARY KEY,
  plan_id     INTEGER NOT NULL REFERENCES plan(row_id),
  letter      TEXT    NOT NULL,   -- 'A','B','C',...
  title       TEXT    NOT NULL,
  slug        TEXT    NOT NULL,   -- 'A-Test-Coverage'
  position    INTEGER NOT NULL,
  doc_id      INTEGER REFERENCES document(row_id),  -- section file (split format)
  UNIQUE(plan_id, letter)
);
```

### 3.7 `Task`

```sql
CREATE TABLE task (
  row_id        INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(row_id),
  task_id       TEXT    NOT NULL UNIQUE,      -- 'AUTH-025'
  plan_id       INTEGER NOT NULL REFERENCES plan(row_id),
  section_id    INTEGER NOT NULL REFERENCES plan_section(row_id),
  title         TEXT    NOT NULL,
  status        TEXT    NOT NULL,   -- pending|in-progress|done
  type          TEXT    NOT NULL,   -- feature|test|bug|refactor|...
  priority      TEXT    NOT NULL,   -- critical|high|medium|low
  description   TEXT,
  acceptance    TEXT,
  created       TEXT NOT NULL,
  last_updated  TEXT NOT NULL,
  completed_at  TEXT,
  completed_commit TEXT
);
CREATE INDEX ix_task_status ON task(status, priority);
CREATE INDEX ix_task_plan ON task(plan_id, section_id);
```

### 3.8 `Dependency`

```sql
CREATE TABLE dependency (
  row_id       INTEGER PRIMARY KEY,
  from_task_id INTEGER NOT NULL REFERENCES task(row_id),
  to_task_id   INTEGER NOT NULL REFERENCES task(row_id),
  kind         TEXT    NOT NULL DEFAULT 'blocks',   -- blocks|relates|duplicates
  note         TEXT,
  UNIQUE(from_task_id, to_task_id, kind)
);
-- Cycle detection: in the service, on every insert.
```

### 3.9 `AffectedFile`

```sql
CREATE TABLE affected_file (
  row_id    INTEGER PRIMARY KEY,
  task_id   INTEGER NOT NULL REFERENCES task(row_id),
  path      TEXT    NOT NULL,
  kind      TEXT    NOT NULL DEFAULT 'source',   -- source|test|migration|config
  UNIQUE(task_id, path)
);
CREATE INDEX ix_affected_path ON affected_file(path);
```

Used for N:1 / N:M diff-based sync (as in Restate task-plan.md §4.6).

### 3.10 `UserStory`

```sql
CREATE TABLE user_story (
  row_id       INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  story_id     TEXT    NOT NULL UNIQUE,    -- 'US-014'
  persona      TEXT    NOT NULL,           -- 'Agency Owner','Platform Admin'
  narrative    TEXT    NOT NULL,           -- 'As X, I want Y, so Z'
  status       TEXT    NOT NULL,           -- draft|accepted|delivered|deferred
  priority     TEXT    NOT NULL,
  created      TEXT NOT NULL,
  last_updated TEXT NOT NULL
);

CREATE TABLE story_acceptance (
  row_id    INTEGER PRIMARY KEY,
  story_id  INTEGER NOT NULL REFERENCES user_story(row_id),
  position  INTEGER NOT NULL,
  criterion TEXT    NOT NULL,
  met       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE story_link (
  row_id       INTEGER PRIMARY KEY,
  story_id     INTEGER NOT NULL REFERENCES user_story(row_id),
  to_kind      TEXT    NOT NULL,       -- 'task'|'document'|'module'
  to_ref       TEXT    NOT NULL,       -- 'AUTH-025' | 'modules/M1-auth' | 'M1-auth'
  relation     TEXT    NOT NULL        -- 'implemented_by'|'specified_in'|'owned_by'
);
```

### 3.11 `Module`

```sql
CREATE TABLE module (
  row_id       INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  module_id    TEXT    NOT NULL UNIQUE,    -- 'M1-auth'
  name         TEXT    NOT NULL,
  status       TEXT    NOT NULL,
  spec_doc_id  INTEGER REFERENCES document(row_id),
  plan_id      INTEGER REFERENCES plan(row_id)
);

CREATE TABLE module_dependency (
  row_id      INTEGER PRIMARY KEY,
  from_module INTEGER NOT NULL REFERENCES module(row_id),
  to_module   INTEGER NOT NULL REFERENCES module(row_id),
  reason      TEXT,
  UNIQUE(from_module, to_module)
);

CREATE TABLE module_code (
  row_id     INTEGER PRIMARY KEY,
  module_id  INTEGER NOT NULL REFERENCES module(row_id),
  kind       TEXT    NOT NULL,   -- 'backend'|'tests'|'migrations'|'admin_panel'
  path       TEXT    NOT NULL
);
```

### 3.12 `Tag`

```sql
CREATE TABLE tag (
  row_id     INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(row_id),
  name       TEXT    NOT NULL,
  UNIQUE(project_id, name)
);
CREATE TABLE document_tag (document_id INTEGER, tag_id INTEGER, PRIMARY KEY(document_id, tag_id));
CREATE TABLE task_tag     (task_id     INTEGER, tag_id INTEGER, PRIMARY KEY(task_id, tag_id));
CREATE TABLE story_tag    (story_id    INTEGER, tag_id INTEGER, PRIMARY KEY(story_id, tag_id));
```

### 3.13 `ActivityEvent` — write-operation log

A unified project audit timeline: one row per mutation, written in the same transaction as the mutation itself (ADO-040), via `activity_service.write_revision_and_emit_event` / `emit_for_write`.

```sql
CREATE TABLE activity_event (
  id         TEXT    PRIMARY KEY,         -- UUIDv7, time-sortable
  project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  ts         TEXT    NOT NULL,
  actor_kind TEXT    NOT NULL,            -- see ActorKind below
  actor_id   TEXT,                        -- author string: 'human:dakh', 'agent:claude-opus-5'
  run_id     TEXT,                        -- orchestrator telemetry, see §3.13.1
  kind       TEXT    NOT NULL,            -- 'task.status_changed', 'doc.updated', …
  scope_kind TEXT,                        -- 'task' | 'doc' | 'story' | 'approval' | 'run'
  scope_id   TEXT,
  payload    TEXT    NOT NULL DEFAULT '{}',
  summary    TEXT
);
```

**`actor_kind`** — enum `domain.entities.ActorKind`:
`human | agent | orchestrator | routine | system | cli | api`.
Derived from the author string **only** via `domain.entities.actor_kind_for_author()` — the single point of derivation (ADR-012). The canonical format of `actor_id` / `author` is `<kind>:<id>` (`human:dakh`, `agent:claude-opus-5`, `routine:doc_drift_daily`); an orchestrator run is historically written with a hyphen (`orchestrator-run-<name>`), the resolver understands both spellings.
`cli` and `api` are surface actors: set explicitly at their call sites and not derived from the string.

> **Rows before 2026-09-06 are inaccurate.** Before ADR-012, the heuristic was duplicated in eleven places in three variants, so the history contains `orchestrator-run-*` events with `actor_kind='human'` and `agent:*` events with `actor_kind='human'`. There is no backfill by design: an observation log is not rewritten retroactively.

#### 3.13.1 `AgentRun` and the `run_id` column — telemetry, not a contract

`agent_run` — one row per `Orchestrator.run_task` call of the built-in runner. The `run_id` column is present in seven tables (`revision`, `activity_event`, `approval`, `agent_run`, `routine_run`, `finding`, plus historically `audit_log`) and is populated **only** inside `run_scope` / `start_orchestrator_run`.

**Mutations via MCP, CLI, and REST do not open a run scope, so they have `run_id IS NULL`.** Measurement on the live cod-doc DB 2026-09-06: `revision` 2166/2166 NULL, `activity_event` 1114/1114 NULL, `agent_run` — one row from 2026-06-06.

This is a fixed decision (ADR-012), not unclosed debt: the "run-id on all mutations" rule is lifted from `AGENTS.md` §5.4, MCP tools `run_list` / `run_revert` / `activity_for_run` are removed. The columns are left nullable — dropping them would mean rewriting the append-only `revision` in every existing DB for zero benefit. Do not write code that relies on a non-empty `run_id`.

> **`audit_log` is removed** (migration `0029_drop_audit_log`, 2026-09-06).
> The table was declared the write-operation log in ARCHITECTURE §9 and in this paragraph, but never got a single writer throughout the project's history — 0 rows. The need is covered by `activity_event` above.

### 3.14 `Embedding`

Vector representations of sections for semantic-search (see [capabilities/context-retrieval.md §5](capabilities/context-retrieval.md)).

```sql
CREATE TABLE embedding (
  row_id        INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(row_id),
  section_id    INTEGER NOT NULL REFERENCES section(row_id) ON DELETE CASCADE,
  model         TEXT    NOT NULL,         -- 'openai:text-embedding-3-small','bge-small-en-v1.5'
  dim           INTEGER NOT NULL,         -- 1536 / 384 / ...
  vector        BLOB    NOT NULL,         -- packed float32; pgvector uses its own
  content_hash  TEXT    NOT NULL,         -- = section.content_hash at generation time
  generated_at  TEXT    NOT NULL,
  UNIQUE(section_id, model)
);
CREATE INDEX ix_embedding_section ON embedding(section_id);
CREATE INDEX ix_embedding_stale ON embedding(content_hash);  -- fast join to section for invalidation
```

Lifecycle:

- On commit of a revision on a section → `EmbeddingService.enqueue(section_id)`.
- A worker computes the vector, writes / updates the row.
- When `section.content_hash` changes, the record is considered stale and regenerated.
- On `DELETE section` — cascaded (CASCADE).

Chunking: one section — one embedding row; if body > N tokens (default 1024) — the section is considered "too large", a warning `EMB-001` is raised in the audit, a split is recommended.

The pg profile uses the `pgvector` type `vector(<dim>)` instead of `BLOB` and an `ivfflat`/`hnsw` index.

### 3.15 `Proposal`

A pending edit awaiting approve/reject (see [capabilities/doc-evolution.md §5](capabilities/doc-evolution.md)).

```sql
CREATE TABLE proposal (
  row_id         INTEGER PRIMARY KEY,
  proposal_id    TEXT    NOT NULL UNIQUE,    -- ULID
  project_id     INTEGER NOT NULL REFERENCES project(row_id),
  target_kind    TEXT    NOT NULL,           -- 'document'|'section'|'task'|'story'
  target_id      INTEGER NOT NULL,           -- row_id of the target entity
  author         TEXT    NOT NULL,           -- agent:... / mcp:... / human:...
  patch          TEXT    NOT NULL,           -- unified diff | json-patch
  reason         TEXT,
  status         TEXT    NOT NULL,           -- pending|approved|rejected|withdrawn
  created        TEXT    NOT NULL,
  decided_at     TEXT,
  decided_by     TEXT,
  resulting_revision_id TEXT                 -- ULID when status=approved
);
CREATE INDEX ix_proposal_pending ON proposal(project_id, status) WHERE status='pending';
CREATE INDEX ix_proposal_target ON proposal(target_kind, target_id);
```

Lifecycle:

- `propose_edit` → row with `status=pending`, ULID `proposal_id`.
- `approve` → applies the patch through the corresponding service, writes a revision, sets `resulting_revision_id`, `status=approved`.
- `reject` → `status=rejected`, no revision.
- `withdraw` (by the author or on timeout) → `status=withdrawn`.

Auto-approve for agents with `auto_approve: true` ([agents-and-skills.md §1.1](capabilities/agents-and-skills.md)) — skips the proposal-row creation, goes straight to revision.

## 4. Computed views

### 4.1 `section_totals`

```sql
CREATE VIEW section_totals AS
SELECT
  s.row_id           AS section_id,
  COUNT(t.row_id)    AS tasks_total,
  SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS tasks_done,
  SUM(CASE WHEN t.status='in-progress' THEN 1 ELSE 0 END) AS tasks_in_progress
FROM plan_section s
LEFT JOIN task t ON t.section_id = s.row_id
GROUP BY s.row_id;
```

### 4.2 `plan_totals`

Analogous — an aggregate over the plan. Used when generating the Progress Overview.

### 4.3a `document_body`

```sql
CREATE VIEW document_body AS
SELECT
  d.row_id AS document_id,
  d.preamble
    || CASE
         WHEN d.preamble <> '' AND COALESCE(s.body, '') <> ''
         THEN E'\n\n'
         ELSE ''
       END
    || COALESCE(s.body, '') AS body
FROM document d
LEFT JOIN (
  SELECT
    document_id,
    string_agg(
      repeat('#', level) || ' ' || heading || E'\n\n' || body,
      E'\n\n'
      ORDER BY position
    ) AS body
  FROM section
  GROUP BY document_id
) s ON s.document_id = d.row_id;
```

> Implemented in `cod_doc/infra/migrations/versions/20260825_0025_projection_fidelity.py`: the SQLite variant uses `group_concat(... , char(10) || char(10))` over an ordered subquery (`SELECT ... ORDER BY position`); Postgres — `string_agg(... , E'\n\n' ORDER BY position)`. Both variants return identical text.

> **ADO-010 (finding F7).** Before migration 0025, the view glued `preamble` to the first heading without a separator — `preamble` is stored without a trailing newline, so the output was `> …in advance.## 1. Why`. This was content corruption, not formatting: any `doc export` broke the document. The `\n\n` separator is inserted only when both parts are non-empty; the section aggregate is moved into a derived table so the condition can check it without repeating `group_concat`.

### 4.3 `ready_tasks`

```sql
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status='pending'
  AND NOT EXISTS (
    SELECT 1 FROM dependency d
    JOIN task dep ON dep.row_id = d.to_task_id
    WHERE d.from_task_id = t.row_id
      AND dep.status <> 'done'
  );
```

## 5. Migrations and seed

- Migrations are Alembic (`cod_doc/infra/migrations/`), numbered `0001_*`, `0002_*`.
- Seed adds only system tags and enum validations.
- For importing Restate — a separate one-shot script (see [migration/from-restate.md](migration/from-restate.md)).
- **JSON `NOT NULL` columns** (`project.config_json`, `document.frontmatter_json`) — `server_default '{}'`. Safe for raw INSERT and bulk import.
- **Timestamp columns** (`created`, `last_updated`, `at`) — `NOT NULL` without `server_default`. Filled on the application side (`_utcnow` in the ORM); raw SQL must pass values explicitly. This is a compromise: a single source of truth — the Python timezone, without desync with the server `current_timestamp` between dialects.

## 6. Naming and id format

| Entity | Human ID | Rule |
|----------|----------|---------|
| Module   | `M<N>-<slug>` | `M1-auth`, `M10-agencies` |
| Task     | `<PREFIX>-<NNN>` | `AUTH-025`; ranging by sections as in Restate |
| Story    | `US-<NNN>` | globally unique |
| Plan     | `<MODULE-ID>-<kebab>` | `M1-auth-module` |
| Document | `<path-without-ext>` | `modules/M1-auth/overview` |

All rules are validated by services — see [standards/task-plan.md](standards/task-plan.md).

## 7. Integrity

Triggers and service rules:

1. **Insert Dependency** → run a cycle-check, error if a cycle appears (DB level: `CHECK from_task_id <> to_task_id` cuts off self-loops; full cycle-check — in the service).
2. **Update Task.status → done** → ensure all `depends_on` are already `done`, otherwise error.
3. **Delete Document** is forbidden while there are live `Link`s to it (force-flag only in the service with audit).
4. **Update Document.body** → triggers link recompute and (asynchronously) embeddings.
5. **Insert ModuleDependency** — at the DB level `CHECK from_module <> to_module`; cycle-check — in the service.

## 8. Why not NoSQL / plain markdown

- NoSQL does not give ACID transactions on "created a task → recalculated a section → wrote a revision → updated links".
- Plain markdown = Restate today = we already know it degrades.
- A relational DB gives a clean recursive CTE for the dependency graph and the critical path without an external graph engine.
