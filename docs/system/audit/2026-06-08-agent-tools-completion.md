---
type: audit-report
scope: agent-tools-completion
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-06-08
last_updated: 2026-06-08
related_docs:
  - ../roadmap/agent-tools-completion-task-plan.md
  - ../roadmap/ROADMAP.md
  - 2026-06-04-self-improvement-compared.md
audience: [contributors, agents]
---

# Audit — Agent Tools Completion (Section H / STB-001)

> **Context.** The self-improvement audit ([2026-06-04](2026-06-04-self-improvement-compared.md))
> raised P0-1: the cycle-5 agent-profile tools (AGT-003..007) looked like
> `NotImplementedError` stubs. A re-check against the code showed that the bodies
> **are already implemented** in `services/agent_service.py`, and the real gap is
> (a) an outdated docstring and (b) the lack of end-to-end coverage through
> `mcp.call_tool`. This audit records the closure of P0-1 (task STB-001 of the
> `stabilization-2026-06` plan).

## TL;DR

The cycle-5 agent profile is fully functional. Both gaps are closed:

- **Docstring drift** — the comment in `agent_tools.py` ("AGT-003..AGT-007 — MCP
  wrappers… Bodies live in `cod_doc.services.agent_service` and have full test
  coverage") honestly describes the thin-wrapper layer; the outdated wording
  "Bodies will be implemented" is gone.
- **End-to-end coverage** — `tests/integration/test_agent_profile_mcp.py`
  drives all 6 tools through a real MCP session (`ClientSession.call_tool`).

P0-1 is closed.

## Scope

| ID | What was done |
|----|-------------|
| AGN-001 | Docstring in `agent_tools.py` (AGT-003..007 block) — a correct thin-wrapper text without "to be implemented". |
| AGN-002 | `AGENTS.md` cycle-5: the agent-profile is described as implemented (services + MCP wrappers). |
| AGN-010 | `tests/integration/test_agent_profile_mcp.py` — fixtures + `agent_capabilities` smoke (tools/list == 6 names `AGENT_TOOLS`). |
| AGN-011 | `agent_pick` end-to-end: seed a ready task → `call_tool("agent_pick")` → assert the task card (task / context / navigation / applicable_skills / success_criteria / legal_status_transitions), status → `in-progress`. |
| AGN-012 | `agent_complete` (status → `done`, lock released in the DB) + `agent_release` (status → `todo`, lock released). |
| AGN-013 | `agent_get` (unknown `what` → `found:false` + `legal_what`), `agent_report` (`kind=progress` → `ok:true` + `next_actions`). |
| AGN-020 | This audit report.

## Test coverage

`tests/integration/test_agent_profile_mcp.py` — **6 tests**, all through
a real MCP session (stdio/in-memory `ClientSession`), not a direct call
of service functions:

| Test | Tools | Check |
|------|------|----------|
| `test_agn010_agent_capabilities_smoke` | `agent_capabilities` | tools/list == `AGENT_TOOLS`; caps keys; profile=`agent`; canonical statuses |
| `test_agn011_agent_pick_end_to_end` | `agent_pick` | full task card; status → in-progress |
| `test_agn012_agent_complete_flow` | `agent_pick`+`agent_complete` | `ok:true`, `done`, lock released (DB) |
| `test_agn012_agent_release_flow` | `agent_pick`+`agent_release` | `ok:true`, `todo`, lock released (DB) |
| `test_agn013_agent_get_unknown_what` | `agent_get` | `found:false`, `legal_what` |
| `test_agn013_agent_report_progress` | `agent_report` | `ok:true`, `next_actions` |

**Run result:** `6 passed` (≈60s, in-memory MCP client + async).
All 6 tools of the `agent` profile are covered end-to-end.

## Findings

- **F-H1 (resolved).** The docstring drift from the self-improvement audit is no longer relevant —
  the code and the comment are in sync.
- **F-H2 (resolved).** The lack of MCP-level coverage is closed: there were 0 e2e tests
  at the `call_tool` level, now there are 6.
- **F-H3 (note).** The test seeds tasks through the service layer and resolves the project
  via workspace-fallback; docker-MCP (`docker exec cod-doc`) is not required for
  the run — an in-memory `ClientSession` is enough.

## Acceptance

- [x] Docstring is correct (AGN-001).
- [x] `AGENTS.md` cycle-5 is accurate (AGN-002).
- [x] 6 integration tests through `mcp.call_tool` are green (AGN-010..013).
- [x] Audit report (AGN-020).

## Next step

- AGN-021 (manual smoke via `cod-doc-mcp --profile agent`) — optional;
  requires pausing Docker. Does not block closing P0-1.
- Move on to STB-002 (remove legacy YAML modules) — the next P0 in Track A.
