---
type: proposal
number: 23
title: Cloud decentralized agent plane
category: architecture
risk: high
status: deferred
created: 2026-07-29
updated: 2026-09-07
depends_on: [04, 06, 09, 12]
related_code:
  - cod_doc/mcp/server.py
  - cod_doc/mcp/profiles.py
  - cod_doc/mcp/tools/agent_tools.py
  - cod_doc/services/agent_service.py
  - cod_doc/services/doc_service.py
  - docker-compose.yml
---

# Proposal 23 — Cloud decentralized agent plane

> Category: 🔵 Architecture · Risk: high · Dependencies: 04 run-id,
> 06 checkout, 09 activity, 12 approvals
>
> **Status: 🟠 DEFERRED (2026-09-07)** — dependencies are implemented, but tasks CAP-001…CAP-033
> are deferred to M6 "Hub + cross-project". Priority now on adoption (track C/E Symbiosis).
> See [ROADMAP](../docs/system/roadmap/ROADMAP.md) → M6.

## Problem

Cycle-5 made the agent UX task-centric (`agent_pick` → complete), but
**documentation is still not maintained "only through the service" in the cloud**:

1. Agent profile cannot write documents (no write-tool; CRUD
   `doc_*` is hidden by the profile).
2. `DocService.patch_section` is not exposed in MCP — a gap relative to
   the capability doc-evolution.
3. Remote MCP (`streamable-http`) exists, but without auth and with localhost bind.
4. Session factory is bound to a per-project sqlite path on local FS.
5. Multiple AIs (Cursor Cloud, Claude, daemon) cannot safely
   work as independent workers against one cloud SoT.

## Proposal

Introduce a **`cloud`** deployment profile and the capability
[cloud-agent-plane](../docs/system/capabilities/cloud-agent-plane.md):

- One team-node COD-DOC in the cloud (Postgres SoT).
- Decentralized agents = MCP-clients with Bearer → `actor`.
- Extend the agent surface with a composite **`agent_apply`** for doc/task
  mutations under checkout (do not drag 80 CRUD-tools into the agent profile).
- Make markdown projection optional.

This is **not** multi-tenant SaaS and **not** P2P-federation (explicitly out of scope).

## `agent_apply` design

```text
agent_apply(
  project: str,
  task_id: str,
  agent_id: str,
  ops: [
    {"op": "patch_section", "doc_key": "...", "anchor": "...",
     "new_body": "...", "base_revision_id": "...", "reason": "..."},
    {"op": "create_doc", "doc_key": "...", "doc_type": "module-spec", ...},
    ...
  ]
) -> {results: [...], run_id, revisions: [...]}
```

Invariants:

- Caller must hold a checkout on `task_id` (otherwise `CheckoutError`).
- One transaction per call; all ops → one `run_id`.
- Optimistic lock on sections via `base_revision_id`.
- Activity event per successful op (proposal 09).
- Sensitivity/authz before write (ARCHITECTURE §12.3).

Agent profile tool list becomes:

`agent_capabilities`, `agent_pick`, `agent_get`, **`agent_apply`**,
`agent_report`, `agent_complete`, `agent_release` (7 tools).

## Implementation stages

See [cloud-agent-plane-task-plan.md](../docs/system/roadmap/cloud-agent-plane-task-plan.md)
sections A–D (CAP-001…CAP-033).

Critical path: Postgres shared session (CAP-005) → MCP patch
(CAP-010) → `agent_apply` (CAP-012) → Bearer (CAP-020) → no-FS mode
(CAP-030).

## Risks

| Risk | Solution |
|------|---------|
| Agent profile bloat | Only the composite `agent_apply`, not `doc_*` |
| Break local DX | Auth optional for stdio/embedded |
| Agents write past the service into git | Skill + handbook: FS is not SoT in cloud |
| Scope → SaaS | Non-goals in capability §7 |

## Acceptance

- A remote agent without access to the project disk goes through the full doc-cycle
  via MCP.
- Two agents on one cloud node do not corrupt one checkout.
- Spec and code identity match (no longer "only ARCHITECTURE").

## Relation to paperclip

Does not cancel Sections A–F of the paperclip-plan: heartbeat/wake/run_id/
approvals — fuel for cloud workers. New agent-features per
AGENTS.md → think of as a continuation of the task-centric surface (not
internal-CRD bloat).

## Relation to M6

RFC 23 is scheduled to launch in **M6 "Hub + cross-project"** after
completing track C/E adoption (SYM-*). Tasks CAP-001…CAP-033 are not started,
awaiting prioritization in the M6 plan.
