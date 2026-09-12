---
type: capability
scope: decisions-and-questions
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-05-17
related_docs:
  - adr-system.md
  - ../audit/2026-04-19-initial-audit.md
  - ../standards/frontmatter.md
---

# Capability — Decisions & Open Questions

> A registry of architectural decisions and open questions. The "Decisions" part
> is now implemented as the [ADR System](adr-system.md); this document
> remains for "Open Questions" and history.

## 1. Decisions = ADR

The decision taken in this vision (see [adr-vision.html](../adr-vision.html) §9):
**the Decision-entity is implemented as an ADR**. One prefix `ADR-NNN`, one
table, one API. See [adr-system.md](adr-system.md) for:

- the domain model (status, supersedes, decided_by, decided_at, …);
- MCP-tools (`adr_create`, `adr_get`, `adr_list`, `adr_update`,
  `adr_supersede`, `adr_link_task`, `adr_add_diagram`, `adr_graph`);
- CLI (`cod-doc adr new/list/show/supersede/graph`);
- Web UI (`/p/<slug>/adr` — list, form, detail, supersede-graph).

"Decision" as a separate entity with the `DEC-NNN` prefix **is not implemented**.
If a `DEC-NNN`-format remains in project files — it is a historical
artifact, it should be migrated to an ADR via `adr_create` with
`adr_id="ADR-NNN"` (see [adr_migrator.py](../../../cod_doc/services/adr_migrator.py)).

## 2. Open Questions (a separate entity)

`OpenQuestion` remains a parallel small entity: "a question
formulation without a decision". When a question is closed — it references an ADR-id.

```yaml
type: open-question
question_id: Q-021
status: open | resolved | dropped
owner: <responsible>
created: YYYY-MM-DD
related: [modules/M1-auth, ADR-014]
resolved_by: ADR-014   # appears with status=resolved
```

### 2.1 Operations (planned)

| Operation | CLI | MCP |
|----------|-----|-----|
| Open question | `cod-doc question new` | `question_create` |
| Close a question | `cod-doc question resolve Q-021 --by ADR-014` | `question_resolve` |
| List open | `cod-doc question list --status open` | `question_list` |

> **Implementation status.** The OpenQuestion-entity is not yet rolled out; this
> section is a specification. Priority — after the ADR System stabilizes.

## 3. Links

- ADR ↔ task / document / module — via [adr-system](adr-system.md)
  (tables `adr_task`, `adr_supersedes`; a future `adr_link` for doc/module).
- Auto-link `[ADR-NNN]` in any markdown — via [auto-linking](auto-linking.md)
  (`LinkKind.ADR`).
- `OpenQuestion` will have its own `question_link` modeled on `story_link`.

## 4. Surface for agents

- `context.get(target=module:..., depth=L1)` includes ≤ 3 open
  questions + a list of ACCEPTED-ADRs of the project (see [context-retrieval](context-retrieval.md)).
- When creating a task you can specify `--addresses Q-021` or
  `--implements ADR-014` — the links are saved.

## 5. When to write an ADR vs an Open Question

| Situation | What to create |
|----------|---------------|
| Decided: "use X instead of Y", the rationale is known | **ADR** (status `accepted`) |
| Discussed: "X or Y, leaning towards X but unsure" | **ADR** (status `proposed`) |
| A question without solution options: "how will we scale?" | **Open Question** |
| A request for an experiment: "try ChromaDB vs Qdrant" | **Open Question** + an ADR in the end |

More on when to write an ADR — see the skill [adr-author](../../../cod_doc/skills/adr-author/SKILL.md).

## 6. What we do not do

- We do not turn every comment into an ADR — the threshold: "a decision affects
  ≥ 2 modules or changes the DB schema".
- We do not automate the formulation — only storage and links.
- We do not do voting / quorum — an ADR is accepted by one person or agent;
  this is not an RFC-process.
