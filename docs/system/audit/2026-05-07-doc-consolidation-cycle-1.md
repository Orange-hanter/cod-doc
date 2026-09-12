---
type: audit-report
scope: documentation-consolidation
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - ../MASTER.md
  - ../../../MASTER.md
  - ../../../proposals/README.md
  - ../roadmap/cod-doc-task-plan.md
---

# Documentation Consolidation — Cycle 1 (Anchor & Disambiguate)

> **Purpose.** Record the findings on the state of the documentation at the start of 2026-05-07
> and formalize the first wave of consolidation: eliminate the double MASTER, refresh the L0 set
> at the root, bring US-001..US-004 to the actual delivered state.

## 1. TL;DR

- **2 parallel master indexes** — `/MASTER.md` (polluted with a fixture heading
  `integration-test`, last_updated 2026-04-05) and `docs/system/MASTER.md` (current,
  2026-05-02).
- **Stale L0 set** (`/arch`, `/specs`, `/models`) — frontmatter v0.1/0.2 from
  2026-04-05; the actual live architecture is described in `docs/system/ARCHITECTURE.md`
  and `docs/system/DATA_MODEL.md`.
- **15 RFCs** in `/proposals/` from 2026-05-06 are not connected to the master and not reflected in
  the DB backlog (58 done, 0 pending).
- **US-001..US-004 in draft**, actually US-001/US-002/US-004 are already delivered
  (commits `bb197bf`, `7e72b30`, `4441ce2`); US-003 is covered by the `tool_defs.py` inventory
  (all 6 target tools are present).

## 2. Detailed Findings

### F1 — Double MASTER, root with a fixture

`/MASTER.md` starts with the heading `🧭 Project Navigator: integration-test` and
a meta block where `repo` points to `/private/var/folders/.../pytest-17/test_agent_run_full_cycle0/my-repo`.
This is clearly a remnant of an integration test that got into a commit. At the same time, all hashes in the
Validation Table (5.1) match the disk ones (via `check_stale_refs` —
10/10 VALID), i.e. **the content is valid, but the meta is misleading**.

**Cycle 1 decision:** repurpose `/MASTER.md` as a thin navigator-aggregator pointing to:
- `docs/system/MASTER.md` — the system-of-truth for the target state of COD-DOC,
- `proposals/README.md` — the RFC catalog,
- `arch/architecture.md`, `specs/modules.md`, `models/domain.md` — the bootstrap
  set, left for the agent L0 scenario, but explicitly marked as legacy.

### F2 — Stale L0 set

| File | meta.version | meta.last_updated | Real canonical document |
|------|--------------|-------------------|---------------------------------|
| `arch/architecture.md` | 0.2 | 2026-04-05 | `docs/system/ARCHITECTURE.md` |
| `specs/modules.md` | 0.2 | 2026-04-05 | (no direct analog — expanded in `docs/system/capabilities/`) |
| `models/domain.md` | 0.1 | 2026-04-05 | `docs/system/DATA_MODEL.md` |

The bootstrap set was conceived as the L0 input for the agent Snowball protocol.
In `docs/system/` a deeper package with a capability breakdown evolved. Instead of
deleting — we update the frontmatter (status `redirect` or a link to the canonical),
leave the existing content as a valid overview, add a pointer to
`docs/system/`.

### F3 — Proposals are not connected to the master

`/proposals/` contains 15 RFCs on adapting paperclip patterns:

| Phase | Numbers | Topic |
|-------|---------|------|
| 1 | 01-04 | Skills layer, Heartbeat-context, Wake-payload, Run-id audit |
| 2 | 05, 09, 12 | Issue documents, Activity log, Approvals |
| 3 | 06, 07, 08, 11 | Atomic checkout, Routines (cron), Status taxonomy, AGENTS.md |
| 4 | 10 | Adapter pattern for LLM |
| n/a | 13, 14, 15 | Import UX, Legacy-tasks migration UX, Link system & rendering |

**Decision:** in Cycles 2-3 open the plan `paperclip-adoption-task-plan` in
`docs/system/roadmap/`, a kickoff-brief, and generate stories+tasks for
all 15 proposals.

### F4 — Stories US-001..US-004 actually delivered

Verified by code:

| Story | Key acceptance | Code | Conclusion |
|-------|---------------------|-----|-------|
| US-001 | context_refs in the initial prompt, preview ≤200 lines | `cod_doc/agent/orchestrator.py:176` `_render_context_refs(refs, max_lines=200)` | ✅ delivered |
| US-002 | forward_chain in the initial prompt | `cod_doc/agent/orchestrator.py:205` `_render_prerequisites(task)`, retry with MASTER removed | ✅ delivered |
| US-003 | tool palette = MCP palette | `cod_doc/agent/tool_defs.py` contains all 6 target tools: `plan_forward_chain`, `plan_reverse_chain`, `plan_ready`, `story_get`, `doc_body`, `link_list` | ✅ delivered |
| US-004 | Task with blocked_by/affects_files/acceptance/story_id | `cod_doc/core/project.py:43-60` fields are present, serialized back and forth (`to_dict`/`from_dict`) | ✅ delivered |

**Cycle 1 decision:** move US-001..US-004 to status `delivered` via
`story_update_status`, add linked-docs to the code source of the implementation.
The divergence between the `coverage()` derived-status (`draft` — no linked
DB tasks) and the pinned-status (`delivered`) is recorded explicitly — the DB tasks were done
before Stories entered the schema; we do not backfill with historical links.

### F5 — Other observations

- The DB has a registered document `arch/arch/architecture` with a double
  prefix — an erroneous bootstrap, a candidate for deletion in Cycle 4.
- The DB document `MASTER` is marked `status: draft, source_of_truth: true`,
  while the file itself lives as an L0 navigator. Bring to `redirect` or
  update the frontmatter after Cycle 1.
- The fresh audit files `2026-05-06-ai-usage-audit.md` and `2026-05-06-cli-vs-web-parity.md`
  are not mentioned in `docs/system/MASTER.md` — add in Cycle 4.

## 3. Cycle-1 deliverables

| # | Deliverable | File/action | Status |
|---|------------|----------------|--------|
| D1 | Cycle-1 audit-report | `docs/system/audit/2026-05-07-doc-consolidation-cycle-1.md` | ✅ this file |
| D2 | `/MASTER.md` → thin navigator | rewrite | ⏳ |
| D3 | `arch/architecture.md` frontmatter refresh | edit meta + add canonical pointer | ⏳ |
| D4 | `specs/modules.md` frontmatter refresh | edit meta + add canonical pointer | ⏳ |
| D5 | `models/domain.md` frontmatter refresh | edit meta + add canonical pointer | ⏳ |
| D6 | US-001..US-004 → delivered | `story_update_status` + `story_link` to code | ⏳ |
| D7 | `docs/system/MASTER.md` changelog | append cycle-1 entry | ⏳ |

## 4. Out of cycle (handed off)

- **Cycle 2:** Phase 1 RFC (01-04) → kickoff brief + execution plan + stories +
  tasks.
- **Cycle 3:** Phase 2-4 RFC (05-12) and unscoped (13-15) → expand the plan.
- **Cycle 4:** link integrity, hash refresh, delete the garbage doc-record
  `arch/arch/architecture`, normalize doc-keys.
- **Cycle 5:** final close-out audit + memory updates.

## 5. Acceptance for cycle 1

- [ ] The root `/MASTER.md` does not contain the fixture `integration-test`.
- [ ] The root `/MASTER.md` has prominent links to `docs/system/MASTER.md`,
      `proposals/README.md`, `arch/architecture.md`, `specs/modules.md`,
      `models/domain.md`.
- [ ] All three legacy docs (`arch/architecture.md`, `specs/modules.md`,
      `models/domain.md`) have an up-to-date `last_updated: 2026-05-07` and
      an explicit pointer to the canonical source.
- [ ] US-001..US-004 are in status `delivered` with reason='cycle-1 verification'
      and at least one `story_link`.
- [ ] `docs/system/MASTER.md §6 Changelog` is extended with an entry for 2026-05-07.
- [ ] `check_stale_refs(cod-doc)` is still 10/10 VALID after the edits.
