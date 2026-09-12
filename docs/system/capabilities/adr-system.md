---
type: module-spec
scope: adr-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, agents]
related_code:
  - cod_doc/services/adr_service.py
  - cod_doc/mcp/tools/adr_tools.py
  - cod_doc/api/web/pages/adr.py
  - cod_doc/templates/web/project/adr_*.html
---

# Capability — ADR System (Architecture Decision Records)

> **Purpose.** A first-class ADR-document system: auto-numbering,
> statuses, supersede-chains, a visual editor in the Web UI and a Mermaid-graph
> of decision dependencies. An ADR is a separate entity, not free markdown.

## 1. Why

Today architectural decisions in cod-doc live scattered:
- In `arch/architecture.md §5 ADR` — five ADRs in one file, without status/supersede.
- In `proposals/*.md` — RFCs, formally not ADRs, but decisions on them are made ad-hoc.
- In commit messages — context is lost after 6 months.
- In audit reports — a justification of one-off decisions (e.g. validation-pattern).

**Pain:**
- No single registry of "which decision was made when and by whom".
- Impossible to trace a supersede-chain ("`ADR-NNN` replaced by `ADR-NNN`, which partially reverts `ADR-NNN`").
- No visual tool for authors — markdown-tables and embedding
  Mermaid diagrams require manual markup.

## 2. Model

### 2.1 The `Adr` entity

```python
@dataclass
class Adr:
    id: str                           # ADR-NNN, auto-numbered per project
    project_id: int
    title: str
    status: AdrStatus                 # PROPOSED | ACCEPTED | DEPRECATED | SUPERSEDED
    context: str                      # markdown — what we are deciding
    decision: str                     # markdown — what we chose
    consequences: str                 # markdown — positive/negative consequences
    supersedes: list[str] = []        # IDs of previous ADRs replaced by this one
    superseded_by: str | None = None  # ID of the ADR that replaced this one
    diagrams: list[Diagram] = []      # 0+ Mermaid blocks with a caption
    decided_by: str                   # human:<user> | agent run-id
    decided_at: datetime
    related_tasks: list[str] = []     # task_ids whose execution is prescribed by the ADR
```

### 2.2 Statuses and transitions

```
PROPOSED  ──accept──▶  ACCEPTED  ──supersede──▶  SUPERSEDED
   │                       │
   └─reject──▶ DEPRECATED   └─deprecate──▶  DEPRECATED
```

- `PROPOSED` — a proposal, open for discussion.
- `ACCEPTED` — the active decision, binding.
- `DEPRECATED` — canceled, but not replaced by a new one (just "we don't do this anymore").
- `SUPERSEDED` — replaced by a specific ADR (the `superseded_by` field).

### 2.3 Links

- ADR ⇔ Document — an ADR can reference docs (as rationale).
- ADR ⇔ Task — an ADR can prescribe tasks (`related_tasks`).
- ADR ⇔ ADR — the supersede-graph (a DAG, not a cycle).

## 3. API

### 3.1 MCP-tools

```
adr_create(project, title, context, decision, consequences, supersedes?=[], decided_by) -> Adr
adr_get(project, adr_id) -> Adr
adr_list(project, status?, limit?) -> [Adr]
adr_update(project, adr_id, *fields) -> Adr     # mutates ACCEPTED only with justification
adr_supersede(project, old_adr_id, new_adr_id, reason) -> (old, new)
adr_deprecate(project, adr_id, reason) -> Adr
adr_add_diagram(project, adr_id, mermaid_source, caption) -> Adr
adr_link_task(project, adr_id, task_id) -> None
adr_graph(project, format='mermaid'|'json') -> str | dict
```

### 3.2 CLI

```
cod-doc adr new --title "Use SQLite by default" --context-file ./ctx.md
cod-doc adr list --status accepted
cod-doc adr show ADR-NNN
cod-doc adr supersede ADR-NNN ADR-NNN --reason "performance regression"
cod-doc adr graph --format mermaid > docs/adr-graph.mmd
```

### 3.3 Storage

- Table `adr` (id auto-numbered ADR-NNN, project_id FK, fields).
- Table `adr_diagram` (adr_id FK, position, mermaid_source, caption).
- Table `adr_supersedes` (from_adr_id, to_adr_id, kind='supersedes').
- Table `adr_task` (adr_id, task_id, kind='predicates').

Every mutating action writes a revision (entity_kind='ADR') like the other
services — a single append-only history pattern.

## 4. Web UI (the visual part)

### 4.1 Pages

- `/p/<slug>/adr` — ADR list with filters (status, supersedes-chain, year).
  Cards: ID, Title, Status badge (color by status), Decided-at, supersedes/superseded-by.
- `/p/<slug>/adr/new` — visual editor.
  A form with sections: Title, Context (textarea + markdown preview), Decision,
  Consequences, Diagrams (multi-Mermaid with preview), Supersedes (multi-select from
  existing ACCEPTED-ADRs).
- `/p/<slug>/adr/<id>` — detail page with rendered markdown, Mermaid blocks,
  the supersede-chain (visually), linked tasks.
- `/p/<slug>/adr/graph` — the full supersede-graph of the project (Mermaid `graph TD`),
  clickable nodes → /adr/<id>.

### 4.2 Status badges

| Status | Color | Icon |
|--------|-------|------|
| PROPOSED | `#facc15` (amber) | 🟡 |
| ACCEPTED | `#22c55e` (green) | 🟢 |
| DEPRECATED | `#737373` (grey) | ⚪ |
| SUPERSEDED | `#94a3b8` (slate) | 🔁 |

### 4.3 Mermaid in the editor

Live-preview via client-side Mermaid.js (the same one cod-doc already uses
for plan-graphs). On save — sanitize the input, stamp `mermaid-version`
for render reproducibility.

### 4.4 Supersede-flow (visually)

When creating a new ADR with a filled `supersedes` — on the old ADR page
a banner appears "Replaced by: ADR-NNN — <title> (decided <date>)"; statuses
are updated transactionally.

## 5. Acceptance (capability-level)

- ADR-NNN are auto-numbered per-project; no collisions on concurrent create.
- The supersede-graph is a DAG (cycle-detection in `adr_supersede`).
- The Web UI shows rendered markdown (not source).
- `adr_graph(format='mermaid')` returns a valid `graph TD` with clickable nodes.
- Every ADR change writes a revision (recoverable via `revision_revert`).
- Deleting an ADR is forbidden; only deprecate/supersede.

## 6. Out of scope

- Voting/quorum — an ADR in cod-doc is accepted by one person or agent; this is not an RFC-process.
- Comment-threads on an ADR — the discussion goes in the issue-tracker of an external tool.
- Auto-suggest "an ADR is needed here" — not done; an ADR is created explicitly.

## 7. Relationship with other capabilities

- [Doc evolution](doc-evolution.md) — the shared revision mechanism.
- [User stories graph](user-stories-graph.md) — a parallel entity for the
  product-level (ADR — for the tech-level).
- [Decisions and questions](decisions-and-questions.md) — open questions
  that can grow into an ADR.

## 8. Roadmap

Implementation — plan [adr-system-task-plan.md](../roadmap/adr-system-task-plan.md),
3 sections (Domain & MCP, Web UI, Templates & Migration), 8 tasks
`ADR-001`..`ADR-008`.
