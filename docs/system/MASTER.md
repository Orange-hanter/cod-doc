---
type: documentation-master
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-07-29
audience: [contributors, agents]
related_code:
  - cod_doc/core/project.py
  - cod_doc/mcp/server.py
  - cod_doc/agent/orchestrator.py
  - cod_doc/api/routes.py
---

# COD-DOC System Documentation — Master Index

> The package describes the **target state** of COD-DOC as an automated project documentation management system with a DB backend.
> Starting point (manual baseline): the `~/Git/Restate` project — a fully manual stack of Obsidian + markdown standards + MCP servers + LightRAG, which currently requires significant human effort and constant manual checks (`node tools/task-plan-audit.mjs --strict`, manually written links, duplicated changelog, etc.).
> Target point: COD-DOC stores the same knowledge graph in a DB, generates markdown projections as artifacts, validates and links everything automatically.

---

## 1. How to read the package

Entry points for different roles:

| Role | Start with | Then |
|------|----------|-------|
| **Anyone — "what to do next"** | [roadmap/ROADMAP.md](roadmap/ROADMAP.md) | [audit/2026-07-29-state-of-the-project.md](audit/2026-07-29-state-of-the-project.md) |
| **cod-doc user** | [../adoption-playbook.md](../adoption-playbook.md) | [../HANDBOOK.md](../HANDBOOK.md) |
| Product / vision | [VISION.md](VISION.md) | [capabilities/](capabilities/) |
| Architect | [ARCHITECTURE.md](ARCHITECTURE.md) | [DATA_MODEL.md](DATA_MODEL.md) |
| Core developer | [DATA_MODEL.md](DATA_MODEL.md) | [capabilities/](capabilities/), [roadmap/cod-doc-task-plan.md](roadmap/cod-doc-task-plan.md) |
| Content author | [standards/](standards/) | [capabilities/doc-evolution.md](capabilities/doc-evolution.md) |
| Migrator from Restate | [migration/from-restate.md](migration/from-restate.md) | [DATA_MODEL.md](DATA_MODEL.md) |
| Agent / LLM | [capabilities/context-retrieval.md](capabilities/context-retrieval.md) | the project's `MASTER.md` |

---

## 2. Package structure

```text
docs/system/
├── MASTER.md                       ← this file (navigation)
├── VISION.md                       ← what problems COD-DOC solves
├── ARCHITECTURE.md                 ← layers and service boundaries
├── DATA_MODEL.md                   ← DB entities and relations
│
├── standards/                      ← file and field formats
│   ├── frontmatter.md
│   ├── task-plan.md
│   ├── document-link.md
│   ├── revision-history.md
│   └── sensitive-data.md           ← classification and redaction
│
├── capabilities/                   ← target capabilities, one per file
│   ├── task-creation.md
│   ├── doc-evolution.md
│   ├── auto-linking.md
│   ├── context-retrieval.md
│   ├── plan-management.md
│   ├── user-stories-graph.md
│   ├── decisions-and-questions.md  ← ADR + Open Questions (free-form)
│   ├── adr-system.md               ← ADR as a first-class entity (visual + MCP)
│   ├── observability-and-indexing.md ← metrics, commit-integration, code-refs, repo+DB index (optional)
│   ├── agents-and-skills.md        ← agent catalog
│   ├── project-bootstrap.md        ← `cod-doc project new`
│   ├── web-frontend.md             ← server-rendered Web UI (Jinja + HTMX)
│   ├── cloud-agent-plane.md        ← cloud SoT + remote AI workers
│   └── audit-and-ci.md             ← check catalog + git/CI
│
├── audit/
│   ├── 2026-04-19-initial-audit.md         ← first formal audit of the package
│   ├── 2026-04-25-section-a-data-core.md   ← core audit (Section A)
│   ├── 2026-04-25-section-b-services.md    ← services audit (Section B)
│   ├── 2026-04-28-section-c-capabilities.md ← capability-layer audit (Section C)
│   ├── 2026-05-01-section-g-hardening.md   ← closing hardening (Section G)
│   ├── 2026-05-02-section-web-frontend.md  ← web-section audit (after WEB-001..011)
│   ├── 2026-05-02-checkpoint-web-batch-1..4.md ← mid-section checkpoints
│   ├── 2026-05-06-ai-usage-audit.md        ← AI usage audit in the system
│   ├── 2026-05-06-cli-vs-web-parity.md     ← comparison of CLI and Web UI surfaces
│   ├── 2026-05-07-doc-consolidation-cycle-{1..5}.md ← consolidation cycle audits
│   ├── 2026-06-04-self-improvement-compared.md ← double LLM review, P0/P1/P2 backlog
│   ├── 2026-06-05-doc-drift-source-of-truth.md ← first three-way DB↔md↔code reconciliation
│   ├── 2026-06-08-agent-tools-completion.md    ← closing Section H (STB-001)
│   └── 2026-07-29-state-of-the-project.md      ← 🧭 current state; basis for the ROADMAP
│
├── migration/
│   └── from-restate.md             ← how to migrate the real Restate state
│
└── roadmap/
    ├── ROADMAP.md                            ← 🧭 canonical priority index (start here; DB = source of truth)
    ├── cod-doc-task-plan.md                  ← adoption plan (dogfood of the task-plan format)
    ├── web-frontend-task-plan.md             ← Web UI plan on top of FastAPI
    ├── web-frontend-kickoff-2026-05-02.md    ← brief to start Section F (after the audit)
    ├── audit-followups-task-plan.md          ← package fixes per the audit
    ├── refactor-large-files-task-plan.md     ← plan to refactor large files
    ├── paperclip-adoption-task-plan.md       ← plan of borrowings from paperclip (15 RFCs → 44 tasks)
    ├── paperclip-adoption-kickoff-2026-05-07.md ← brief for Phase 1 paperclip
    ├── adr-system-task-plan.md               ← ADR-system plan (capability + visual UI, 8 tasks)
    ├── observability-and-indexing-task-plan.md ← optional plan: metrics/commits/code-refs/repo-index/DB-index (8 tasks, 5 stories US-021..US-025)
    ├── cloud-agent-plane-task-plan.md        ← cloud control plane for AI agents (18 tasks CAP-*)
    └── cloud-agent-plane-kickoff-2026-07-29.md ← brief to start the cloud agent plane
```

---

## 3. Source of truth

- **The description package (`docs/system/`)** — source of truth for the system's behavior.
- **The code (`cod_doc/`)** — the implementation; any discrepancy with the package is a bug of either the description or the code.
- **The project DB (`.cod-doc/state.db`)** — source of truth for the content of an individual user project. Markdown files are projections/exports.

Conflict resolution rule:
1. If the DB and markdown have different states and the markdown is not marked as edited — overwrite the markdown from the DB.
2. If the markdown was edited manually (the hash changed without a corresponding revision in the DB) — the agent launches a reconciliation-flow (see [capabilities/doc-evolution.md](capabilities/doc-evolution.md)).

---

## 4. Mapping to the requested capabilities

User request → a specific package document:

| Requested capability | Described in |
|-------------------------|-----------|
| Task creation (standardized, automatic) | [capabilities/task-creation.md](capabilities/task-creation.md) + [standards/task-plan.md](standards/task-plan.md) |
| Documentation evolution | [capabilities/doc-evolution.md](capabilities/doc-evolution.md) |
| Auto-linking of references | [capabilities/auto-linking.md](capabilities/auto-linking.md) + [standards/document-link.md](standards/document-link.md) |
| Links to documents | [standards/document-link.md](standards/document-link.md) |
| Change history | [standards/revision-history.md](standards/revision-history.md) |
| Getting concentrated context | [capabilities/context-retrieval.md](capabilities/context-retrieval.md) |
| Plan management | [capabilities/plan-management.md](capabilities/plan-management.md) + [standards/task-plan.md](standards/task-plan.md) |
| User stories and dependency graph | [capabilities/user-stories-graph.md](capabilities/user-stories-graph.md) |
| AI maintains docs via cloud COD-DOC (remote agents) | [capabilities/cloud-agent-plane.md](capabilities/cloud-agent-plane.md) + [roadmap/cloud-agent-plane-task-plan.md](roadmap/cloud-agent-plane-task-plan.md) |

---

## 5. Package document statuses

| Document | Status | Owner |
|----------|--------|----------|
| VISION | draft | cod-doc core |
| ARCHITECTURE | draft | cod-doc core |
| DATA_MODEL | draft | cod-doc core |
| standards/* | draft | cod-doc core |
| capabilities/* | draft | cod-doc core |
| migration/from-restate | draft | cod-doc core |
| roadmap/ROADMAP | active | cod-doc core |
| roadmap/cod-doc-task-plan | active | cod-doc core |
| roadmap/audit-followups-task-plan | active | cod-doc core |
| roadmap/web-frontend-task-plan | active | cod-doc core |
| audit/2026-04-19-initial-audit | active | cod-doc core |
| audit/2026-04-25-section-a-data-core | resolved | cod-doc core |
| audit/2026-04-25-section-b-services | resolved | cod-doc core |
| audit/2026-04-28-section-c-capabilities | resolved | cod-doc core |
| audit/2026-05-01-section-g-hardening | resolved | cod-doc core |
| audit/2026-05-02-section-web-frontend | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-1 | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-2 | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-3 | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-4 | resolved | cod-doc core |
| audit/2026-06-04-self-improvement-compared | resolved | cod-doc core |
| audit/2026-06-05-doc-drift-source-of-truth | resolved | cod-doc core |
| audit/2026-06-08-agent-tools-completion | resolved | cod-doc core |
| audit/2026-07-29-state-of-the-project | active | cod-doc core |
| capabilities/web-frontend | active | cod-doc core |
| capabilities/cloud-agent-plane | draft | cod-doc core |
| roadmap/cloud-agent-plane-task-plan | active | cod-doc core |
| roadmap/cloud-agent-plane-kickoff-2026-07-29 | active | cod-doc core |

While the package is in `draft` status — changes are allowed without revision history. After `active`, any edit must lead to a revision record (see [standards/revision-history.md](standards/revision-history.md)). The `resolved` status is for audit reports whose tasks are closed (see [standards/frontmatter.md §7](standards/frontmatter.md)).

---

## 6. Changelog

> **Entry format (DOC-LO-5).** A table row is `| YYYY-MM-DD | <event> |`.
> The event: one sentence in the past tense with a **bold** theme heading,
> links to the audit report / plan and the affected ids (`COD-NNN`, `WEB-NNN`, …). The same
> fact for documents with `status: active` is duplicated in `revision` — a single
> format with [revision-history.md §10](standards/revision-history.md). In ordinary
> documents the body of the `## Changelog` section is **generated** from `revision` on
> export (manual editing is forbidden); this L0 navigator is an exception, maintained by hand.

| Date | Event |
|------|---------|
| 2026-04-19 | Initial version of the package; basic structure, standards, and capabilities. |
| 2026-04-19 | First audit conducted ([audit/2026-04-19-initial-audit.md](audit/2026-04-19-initial-audit.md)); 6 tasks closed (HI-1..5, LO-1) with stubs; a follow-up plan created ([roadmap/audit-followups-task-plan.md](roadmap/audit-followups-task-plan.md)) with 17 remaining tasks. |
| 2026-04-25 | Audits of Section A (Data Core) and Section B (Services) — both `resolved`; see [audit/2026-04-25-section-a-data-core.md](audit/2026-04-25-section-a-data-core.md), [audit/2026-04-25-section-b-services.md](audit/2026-04-25-section-b-services.md). |
| 2026-04-28 | Added `standards/sensitive-data.md` to the index §2; recorded a gap: the infrastructure (scanner, redaction, context filters) is missing for now — moved to task COD-025. |
| 2026-04-28 | Capability-layer audit (Section C) — gaps identified: no CI workflow (COD-024), LinkService.rename does not cascade the body (COD-014a), the web-layer bypasses services (WEB-020), the TUI has no tests (COD-026). |
| 2026-05-01 | Section G (Hardening & DevX) closed entirely — 5/5 tasks: COD-024a (strict ruff/mypy debt cleared, CI gates blocking), COD-014a (markdown-relative rename cascade), COD-025 (sensitive-data infrastructure: scanner+FM-007+SD-001 audit+SD-002 redaction+clearance helper), COD-026 (TUI smoke tests, also a bug fix in WizardScreen). Suite 402/402; see [audit/2026-05-01-section-g-hardening.md](audit/2026-05-01-section-g-hardening.md). |
| 2026-05-02 | Web-section audit after closing Section A (Scaffold) + WEB-010/011 — see [audit/2026-05-02-section-web-frontend.md](audit/2026-05-02-section-web-frontend.md). 16 findings (4 high, 7 medium, 5 low); 13 new tasks created in [roadmap/web-frontend-task-plan.md](roadmap/web-frontend-task-plan.md) (new Section F: Hardening — WEB-005, 013, 022 ↑, 050..053; Section E extended with WEB-041, 042; Section B — WEB-006, 014, 060). WEB-040 and WEB-022 promoted to `high`. The `web-frontend` capability moved to `active`, §11 "Current state" and a DI convention in §7 added. 5/14 endpoints implemented (~36 %). |
| 2026-05-02 | Section F batch-1 closed: 5 tasks (WEB-005 engine cache + DI helpers, WEB-040 web→infra bypass removed, WEB-022 alert/error model, WEB-041 tab strip include + status_options Jinja global, WEB-013 batch stats + pagination). 11 / 16 baseline-audit findings closed. Suite 418 → 441; web-tests 27 → 66. The audit report `2026-04-28-section-c-capabilities` moved to `resolved` (its last task SC-HI-3 was closed in WEB-040). A checkpoint audit made: [audit/2026-05-02-checkpoint-web-batch-1.md](audit/2026-05-02-checkpoint-web-batch-1.md): 4 new internal items (WEB-013b/022b/053 ↑/054). |
| 2026-05-02 | Section B batch-2 + polish: 4 commits (WEB-006 server-rendered markdown for doc_show, WEB-013b/022b/054 polish bundle from checkpoint #1, WEB-014 overview agg ready/progress/recent + POST .../complete, WEB-021 revisions log + filter). 13 / 16 baseline findings closed (SW-ME-3, SW-ME-7 in this batch). Suite 441 → 483; web-tests 66 → 108. Endpoints shipped 5/14 → 8/14 (~57 %). A checkpoint audit made: [audit/2026-05-02-checkpoint-web-batch-2.md](audit/2026-05-02-checkpoint-web-batch-2.md). |
| 2026-05-02 | Batch-3 + Section B closed: 3 commits (WEB-004 plan view + Mermaid `<pre>`, WEB-060 settings page, WEB-051 static asset versioning). **Section B (Read views) — 6/6 done.** 14 / 16 baseline findings closed (SW-LO-1 in this batch). Suite 483 → 500; web-tests 108 → 125. Endpoints shipped 8/14 → 10/14 (~71 %). 5 of 6 tabs live (only Run stayed disabled). Checkpoint audit [audit/2026-05-02-checkpoint-web-batch-3.md](audit/2026-05-02-checkpoint-web-batch-3.md). Inline fix: documented the lifespan-vs-set_config footgun in `tests/api/conftest.py`. |
| 2026-05-02 | Batch-4 + Section C closed + baseline audit resolved: 2 commits (WEB-012 HTMX section patch, polish bundle WEB-052/053/053b/014b). **Section C (Write paths) — 3/3 done.** **16 / 16 baseline-audit findings closed** (SW-LO-2/3/5 in this batch). Suite 500 → 512; web-tests 125 → 137. Endpoints shipped 10/14 → 13/14 (~93 %). The audit report `2026-05-02-section-web-frontend` moved to `resolved`. Checkpoint audit [audit/2026-05-02-checkpoint-web-batch-4.md](audit/2026-05-02-checkpoint-web-batch-4.md). Only Section D remains (WEB-030/031 — SSE run console). |
| 2026-05-07 | **Documentation Consolidation — Cycle 1 (Anchor & Disambiguate).** The root `/MASTER.md` rewritten as a thin L0 navigator → `docs/system/MASTER.md` + `proposals/README.md` + L0 bootstrap docs (the fixture `integration-test` heading removed). Frontmatter updated in `arch/architecture.md`, `specs/modules.md`, `models/domain.md` (status: legacy-overview, canonical_source, last_updated 2026-05-07; hashes recomputed via `update_master_hashes` — 3/3 obs). Stories US-001..US-004 moved to `delivered` after code-verification (orchestrator/_render_context_refs+_render_prerequisites, tool_defs.py 6/6, Task struct fields), linked to capability/standards docs via `story_link`. Audit report: [audit/2026-05-07-doc-consolidation-cycle-1.md](audit/2026-05-07-doc-consolidation-cycle-1.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 2 (Phase 1 backlog).** Created `paperclip-adoption-task-plan` + Section A in the DB (via a direct `PlanRepository.add` — gap G1 in the MCP-API), a kickoff brief in `roadmap/`, 4 stories US-005..US-008 (`accepted`), 17 tasks PCA-001..PCA-034. Three API gaps recorded in the Cycle-2 audit: G1 no MCP-API for plan creation, G2 task_create.blocked_by is not persisted into dependency-edges, G3 task_create.story_id and .affects_files are not persisted. Audit report: [audit/2026-05-07-doc-consolidation-cycle-2.md](audit/2026-05-07-doc-consolidation-cycle-2.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 3 (Phase 2-4 + UX + Tooling).** All 15 RFCs from `/proposals/` now have a structured backlog: 11 new stories US-009..US-019, 5 new sections B/C/D/E/F in the DB plan, 26 new tasks PCA-100..PCA-422 + PCA-901..PCA-903. Section F moved out for tooling fixes G1/G2/G3 (PCA-901..PCA-903; PCA-902 `critical` as a blocker of the basic plan_ready/plan_audit/critical_path scenarios). Plan total: **43 tasks** (17/6/7/3/7/3 by sections A..F). Audit report: [audit/2026-05-07-doc-consolidation-cycle-3.md](audit/2026-05-07-doc-consolidation-cycle-3.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 4 (Cross-links & Integrity).** `link_list` showed 39 broken markdown-refs to `docs/system/MASTER` — discovered gap **G4** (link_service does not resolve relative-paths against the source-doc directory) → expanded the scope of PCA-421 in the paperclip-adoption plan. The doc-record `arch/arch/architecture` identified as a fixture relic (commit e51e85f, 2026-04-05). `doc_drift` for root `MASTER` and `docs/system/MASTER` — `stale_export` after edit-in-place (a known state). Cycle-2/3 audit docs registered as doc-records (active). `check_stale_refs` remains 10/10 VALID. Audit report: [audit/2026-05-07-doc-consolidation-cycle-4.md](audit/2026-05-07-doc-consolidation-cycle-4.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 5 (Final Close-out).** Summary across 5 cycles: +44 pending tasks (44 tasks in paperclip-adoption-task-plan), +15 stories (US-005..US-019, 19 total), +5 audit reports, +6 doc-records, +2 roadmap files. Created PCA-911 (low) for cleaning up the `arch/arch/architecture.md` fixture. Memory enriched with two feedback patterns: `mcp_field_persistence_gap` (echo-but-no-persist) and `consolidation_cycle_pattern` (N cycles → N audit reports). Implementation of PCA-001..PCA-911 intentionally not started in this session — it is a separate long front of work. Final audit report: [audit/2026-05-07-doc-consolidation-cycle-5-final.md](audit/2026-05-07-doc-consolidation-cycle-5-final.md). |
| 2026-05-07 | **ADR System capability added.** Created capability [adr-system](capabilities/adr-system.md) (Architecture Decision Records as a first-class entity with auto-numbering, supersede-DAG, a visual editor, and a Mermaid graph in the Web UI). Story US-020 (`accepted`). New plan [adr-system-task-plan](roadmap/adr-system-task-plan.md), 3 sections (Domain & MCP, Web UI, Templates & Migration), 8 tasks `ADR-001`..`ADR-008`. Implementation starts after closing Section F of the paperclip plan. |
| 2026-07-29 | **State-of-the-project audit + roadmap rebuild.** The run confirmed engineering health: 1356 tests, ruff/mypy clean, 106 documents `in_sync`, `plan audit` ×5 without issues, 0 `pragma: no cover`. Track A (`stabilization-2026-06`) closed 12/13; STB-013 closed (ContextService L2/L3 implemented — only an outdated docstring remained). Findings: F2 repo-index was not built on the project itself (fixed: 625 files / 3021 symbols), F3 66 undocumented web-routes, F4 the L0-payload `agent_capabilities` broke the 4 KB ceiling (fixed: 4543 → 3586 bytes), F5 a global config from a test run, F6 no root README. The skill catalog 9 → 12 (`project-onboarding`, `ground-truth-reconcile`, `rfc-authoring`). Added [../adoption-playbook.md](../adoption-playbook.md). [ROADMAP](roadmap/ROADMAP.md) rebuilt: the priority shifted from features to adoption (M1 → M2 → M3). Report: [audit/2026-07-29-state-of-the-project.md](audit/2026-07-29-state-of-the-project.md). |
| 2026-05-07 | **Observability & Indexing capability added (optional).** Capability [observability-and-indexing](capabilities/observability-and-indexing.md) — task-execution metrics, commit→task linkage for work history, code-refs `[label](src/path.py)` in markdown, RepoIndex (.gitignore-aware symbols/imports), DBObjectIndex (FTS5 unified search). 5 stories US-021..US-025 (`accepted`), new plan [observability-and-indexing-task-plan](roadmap/observability-and-indexing-task-plan.md): 5 sections (Metrics/Commits/Code-Refs/Repo-Index/DB-Object-Index), 8 tasks OBI-001..OBI-040. Marked optional — does not block Phase 1 paperclip-adoption. |
| 2026-07-29 | **Cloud Agent Plane designed.** Capability [cloud-agent-plane](capabilities/cloud-agent-plane.md): COD-DOC as a cloud documentation control plane; AI maintains docs entirely via MCP; agents are decentralized remote workers; SoT = Postgres; markdown projection optional. RFC [proposals/23](../../proposals/23-cloud-decentralized-agent-plane.md), kickoff + execution plan (18 tasks CAP-001..CAP-033). ARCHITECTURE §8 extended with the `cloud` profile. Non-goals: SaaS multi-tenant, P2P federation. |

## 7. Formatting conventions

- **Language standard (DOC-LO-4):** prose is in English; identifiers (fields,
  types, `status` values, entity names, tables, commands, files, and task ids) are
  in English, in `code` framing. Concept-terms (drift, checkout, transclusion)
  are allowed as terms, but are not translated back and forth within the same
  document — one form is chosen.
- Section headings are `## N. Title`; links are markdown-relative from the document's location.
- **Restate references (DOC-LO-3):** mentions of internal artifacts of the original manual
  stack (`~/Git/Restate`) are prefixed with "(Restate)", e.g. "(Restate)
  `Documentation Graph.md`", so they are not confused with COD-DOC entities.
- Each document has frontmatter per [standards/frontmatter.md](standards/frontmatter.md).
- With `status: active`, any edit must be accompanied by an entry in the changelog table + a revision (after COD-004 is implemented) — see [standards/revision-history.md](standards/revision-history.md).
