---
type: vision
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
---

# COD-DOC — Vision

## 1. Why

`~/Git/Restate` — a live project maintained manually through Obsidian + a set of markdown standards (`Docs/standards/task-plan.md`, `frontmatter.md`, `module-spec.md`) and scripts (`tools/task-plan-audit.mjs`, `tools/generate-workspace-map.mjs`, `tools/lightrag`). It works, but requires:

- manual compliance with the frontmatter format and computing `tasks_done` / `tasks_total`;
- manual linking between documents and finding stale links on rename;
- manual changelog entries and synchronization with Plane;
- running validator scripts (`--strict`) before every commit;
- continuously compacting "what lives where" in the author's head.

Over time, discipline degrades: metadata goes stale, links break, tasks drift between `pending` and `in-progress`, user stories lose their connection to modules. COD-DOC must replace manual discipline with stored state and "by construction" validation.

## 2. What COD-DOC is

**COD-DOC is a documentation engine with a DB backend, Markdown projections, and an MCP/CLI interface for automated work with project documentation.**

Core:
- A normalized DB (Documents, Tasks, Links, Revisions, Stories, Dependencies, Tags).
- Markdown is only a projection: generated from the DB on `export`, parsed back on `import`.
- A single query language for the project state: via CLI, REST-API, MCP tools.
- An LLM agent that uses MCP to evolve documentation, create tasks, and maintain the knowledge graph.

## 3. Basic promises of the system

| Promise | How it is supported |
|----------|---------------------|
| Any link is either valid or known to be broken | auto-linking + periodic verification ([auto-linking](capabilities/auto-linking.md)) |
| Any change is reversible and explainable | revision history on every entity ([revision-history](standards/revision-history.md)) |
| The task format cannot be broken | DB schema + write-path-level validation ([task-plan](standards/task-plan.md)) |
| The plan is consistent with itself | `tasks_done` / `tasks_total` are computed, not stored ([plan-management](capabilities/plan-management.md)) |
| The agent can get "sufficient but minimal" context | concentrated-context queries ([context-retrieval](capabilities/context-retrieval.md)) |
| User stories are linked to tasks and modules | explicit edges in the graph ([user-stories-graph](capabilities/user-stories-graph.md)) |

## 4. Target user experience

### 4.1 Documentation author

```bash
cod-doc doc new --module M1-auth --type spec --title "Auth Module"
# creates a Document record, generates skeleton-markdown, registers links,
# fills in owner/created/last_updated automatically

cod-doc doc edit M1-auth --section "Data Model" --from-file /tmp/section.md
# the diff is applied, a revision is written to history, outgoing links are re-resolved
```

### 4.2 Plan author

```bash
cod-doc task new --plan M1-auth --title "Implement account deactivation" --type feature
# generates an ID in the correct range (AUTH-025..), sets priority, validates the title

cod-doc task depend AUTH-025 --on AUTH-020 AUTH-021
# updates the graph, recalculates the critical path

cod-doc plan next --count 5
# returns readyTasks without terminal confirmation; the format is identical to Restate
```

### 4.3 Agent (LLM via MCP)

```text
→ cod_doc.context.get(module="M1-auth", depth="L1")
← { master, spec, task_plan_progress, open_questions, related_stories }

→ cod_doc.task.update_status(id="AUTH-025", status="in-progress")
← ok, revision=01HQX5Z9F0K8RNG6CB7VHQK4XX

→ cod_doc.doc.propose_edit(doc="M1-auth/overview.md", patch=...)
← pending_approval=01HQX60E1A2P7K3MMSV0NRD9YA
```

### 4.4 Migrator from Restate

```bash
cod-doc import restate ~/Git/Restate \
  --docs Docs/obsidian/Modules \
  --plans "Docs/obsidian/Modules/*/*-task-plan.md" \
  --standards Docs/standards
# reads existing markdown, fills the DB, validates,
# keeps the original files as a "frozen projection" until the first export
```

## 5. Non-goals (what COD-DOC does not do)

- Does not replace Plane/Jira — it is a local orchestrator, like the Restate task-plan.
- Does not take over the runtime business logic of the user's project — only its documentation and plan.
- Does not force a specific LLM provider — the MCP contract is stable, the agent is swappable.
- Does not build its own graph engine — PostgreSQL + recursive CTE / pgrouting cover the needs (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## 6. Success

COD-DOC is considered successful when:

1. The full Restate documentation is imported and lives for ≥ 1 month without quality regressions.
2. `cod-doc audit` covers all the rules that `tools/task-plan-audit.mjs --strict` currently provides in Restate.
3. Any structural change (renaming a document, moving a task between sections) is correctly handled via CLI/MCP without manual markdown edits.
4. An LLM agent can complete the cycle "understand the module → create a plan → bring 3 tasks to done" on a new clean project in a single long session, relying only on the COD-DOC contract.
5. The same cycle is doable by a **remote** AI client (Cursor Cloud / Claude) against a cloud COD-DOC node without mounting the project disk — see [capabilities/cloud-agent-plane.md](capabilities/cloud-agent-plane.md).
