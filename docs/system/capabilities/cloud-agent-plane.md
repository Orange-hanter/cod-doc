---
type: capability
scope: cloud-agent-plane
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-07-29
last_updated: 2026-07-29
related_docs:
  - ../ARCHITECTURE.md
  - ../VISION.md
  - agents-and-skills.md
  - doc-evolution.md
  - project-bootstrap.md
  - ../roadmap/cloud-agent-plane-task-plan.md
  - ../../../proposals/23-cloud-decentralized-agent-plane.md
---

# Capability — Cloud Agent Plane

> COD-DOC as a cloud control plane for documentation: any AI-agent
> (Cursor, Claude Code, the built-in orchestrator, CI) fully manages
> docs/tasks via MCP, without a shared local disk. Agents are
> decentralized workers; the SoT is the DB in the cloud.

## 1. The problem

Today COD-DOC already has a task-centric agent surface (cycle-5:
`agent_pick` → work → `agent_complete`) and streamable-http MCP, but
it remains **locally-bound**:

| Fact today | Why it blocks "AI manages docs in the cloud" |
|--------------|----------------------------------------|
| MCP defaults to `stdio` on the developer's machine | A remote Cursor Cloud / another host cannot connect |
| `streamable-http` listens on `127.0.0.1`, without Bearer auth | Cannot be safely exposed to the network |
| Agent profile = 6 tools **without document writes** | The agent closes a task, but cannot patch a doc through the same profile |
| `DocService.patch_section` exists, MCP `doc_patch_*` does not | The [doc-evolution](doc-evolution.md) capability promises MCP-patch; the surface is missing |
| `project.root_path` + markdown projection on FS | A cloud node requires mounting foreign paths (`docker-compose` with host paths) |
| Per-project SQLite by default | Multiple agents/clients do not share one SoT without Postgres |
| Identity/authz described in ARCHITECTURE §12, no code | Anyone who reaches the HTTP has full write |

The goal of the capability is to close these gaps without turning COD-DOC into
a multi-tenant SaaS (this is still a non-goal, see VISION §5 and
proposals/README "What was left out of scope").

## 2. The target model

```text
┌─────────────────────────────────────────────────────────────┐
│                 Cloud COD-DOC node (team)                   │
│  Postgres (SoT) · MCP streamable-http · REST/Web · routines │
│  Bearer → actor · atomic checkout · activity/run_id         │
└───────────────┬─────────────────┬─────────────────┬─────────┘
                │                 │                 │
        ┌───────▼──────┐  ┌───────▼──────┐  ┌───────▼──────┐
        │ Cursor Cloud │  │ Claude Code  │  │ Orchestrator │
        │ agent        │  │ / Copilot    │  │ daemon       │
        │ (MCP client) │  │ (MCP client) │  │ (in-process) │
        └──────────────┘  └──────────────┘  └──────────────┘
                ▲                 ▲                 ▲
                │    no shared disk between agents │
                └──────── decentralized workers ┘
```

**Decentralization** here means:

1. Many independent AI-clients connect to one cloud node.
2. Coordination — via the DB (checkout lock, status machine, activity), not
   via a shared filesystem.
3. Markdown on disk — an **optional projection** (git sync / object
   storage / local mirror), not a prerequisite for the agent to work.
4. One node = one team/set of projects (not a multi-master federation and
   not SaaS-tenants). Federation of nodes — out of scope of this capability.

## 3. The contract for AI ("fully via the service")

The agent **does not** edit the project's markdown files directly as the SoT.
The canonical cycle:

```text
1. agent_capabilities()
2. agent_pick(project, agent_id)          # lock + task card + skills
3. agent_apply / agent_get …              # doc/task mutations in the DB
4. agent_complete | agent_report | agent_release
```

The minimal **agent** surface after the extension (see task-plan §B):

| Tool | Role |
|-----|------|
| `agent_capabilities` | L0 bootstrap |
| `agent_pick` | checkout + context card |
| `agent_get` | deep fetch (`full_doc_body`, …) |
| `agent_apply` | **new**: atomic doc/task mutations within a checkout |
| `agent_report` | progress / blocker / approval |
| `agent_complete` | done + release |
| `agent_release` | drop lock |

`agent_apply` composes existing services (`DocService.patch_section`,
`doc_create`, rename, …) and writes revision + activity + `run_id` in one
transaction. Admin CRUD (`doc_*`, `task_*`) stays in `--profile
standard|full`.

### 3.1 What counts as "successful full management"

An AI-agent on a clean project, connected only to a remote MCP:

1. Picks a ready-task via `agent_pick`.
2. Reads/patches documents, creates related docs/tasks.
3. Escalates via `agent_report(kind='approval_request')` on FM.
4. Closes the task via `agent_complete`.
5. Does not mount the project's `root_path` and does not write to the git working tree
   (the projection — a separate export-job).

This strengthens VISION §6.4 ("the cycle only on the COD-DOC contract") up to a
cloud remote-client.

## 4. The `cloud` deployment profile

An extension of [ARCHITECTURE.md §8](../ARCHITECTURE.md):

| Parameter | `embedded` | `server` (today) | `cloud` (target) |
|----------|------------|--------------------|----------------|
| DB | SQLite | Postgres | Postgres (+ pgvector later) |
| MCP | stdio | stdio / http localhost | streamable-http + TLS + Bearer |
| REST/web | optional | on | on, behind a reverse-proxy |
| Auth | implicit OS user | token (spec) | token enforced |
| Projection | mandatory FS mirror | FS volume | optional (export job / git sync) |
| Agents | 1 local | N local clients | N remote workers |
| Identity | `human:<os>` | `actor` + token_hash | same + project-scoped tokens |

Switching: `COD_DOC_DB_URL=postgresql://…` +
`COD_DOC_MCP_TRANSPORT=streamable-http` +
`COD_DOC_AUTH=required`.

## 5. Security and identity

Implements the already-described contract of ARCHITECTURE §12:

1. Every remote-call carries `Authorization: Bearer <token>`.
2. Token → `actor(project_id, kind, handle)`.
3. Authz: allowed_tools / sensitivity_clearance.
4. All writes → `revision` + `activity` + `run_id` (proposals 04, 09).
5. Checkout TTL + idempotent `agent_pick` protect against races between
   workers (proposal 06 / already in code).

The Web UI can still sit behind a reverse-proxy; MCP — a separate
port/path with the same token store.

## 6. Projections and git (not SoT)

- **SoT** = Postgres.
- **Projection** = an artifact:
  - on-demand `doc_export` / batch export to a volume or object storage;
  - an optional git-sync worker (commit projection → repo), does not block
    the agent loop.
- Drift detection remains for nodes where projection is enabled; in
  pure-cloud mode drift against FS is off.

## 7. Non-goals

- Multi-tenant SaaS with billing and an org-chart (proposals/README).
- Peer-to-peer federation of several COD-DOC nodes.
- Replacing Plane/Jira / the runtime business-logic of a user project
  (VISION §5).
- A mandatory shared NFS between agents.

## 8. Acceptance (capability-level)

- [ ] A remote MCP client (not on the same host) goes through the cycle
      pick → apply(doc patch) → complete against Postgres.
- [ ] Two parallel agents with different `agent_id` do not get the same
      checkout; the second sees an idempotent replay or a different task.
- [ ] Without a valid Bearer a write is rejected (`AuthDeniedError`).
- [ ] The agent does not require `root_path` on the server disk to mutate a body.
- [ ] Activity/run_id link all mutations of one heartbeat.
- [ ] A recipe for connecting Cursor Cloud / Claude to
      `https://<host>/mcp` is documented.

## 9. Related artifacts

- Kickoff: [roadmap/cloud-agent-plane-kickoff-2026-07-29.md](../roadmap/cloud-agent-plane-kickoff-2026-07-29.md)
- Plan: [roadmap/cloud-agent-plane-task-plan.md](../roadmap/cloud-agent-plane-task-plan.md)
- RFC: [proposals/23-cloud-decentralized-agent-plane.md](../../../proposals/23-cloud-decentralized-agent-plane.md)
