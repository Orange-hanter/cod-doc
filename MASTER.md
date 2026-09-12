# 🧭 Project Navigator: cod-doc

> 📊 Meta: `{"version": "2.12", "last_updated": "2026-09-07", "context_depth": "L0", "repo": "/Users/dakh/Git/_my/cod-doc"}`

> **This file is a thin L0 navigator for the agent and a new contributor.**
> The source of truth for the target system state is [`docs/system/MASTER.md`](docs/system/MASTER.md).
> Priorities and milestones — [`docs/system/roadmap/ROADMAP.md`](docs/system/roadmap/ROADMAP.md).
> Catalog of RFCs and borrowings — [`proposals/README.md`](proposals/README.md).

## 1. 🎯 Executive Summary

- **Project:** COD-DOC (Context Orchestrator for Documentation) — an autonomous
  agent and MCP server for managing project documentation with a DB backend
  and markdown projections.
- **Architecture:** multi-layer modular (Presentation → Application →
  Domain ← Infrastructure) with DIP inversion.
- **Current status:** 🟢 ACTIVE — **M5 "A gate you can trust + symbiosis in combat" closed 2026-09-06**.
  Run 2026-09-07: 1639 tests green, ruff/mypy clean, ~137 documents
  (`stale_export`=0 after reconcile of migrations 0026–0029). Surface:
  ~110 MCP tools (profile `agent` — 6), 12 skills, 6 ADRs, 25 stories.
  CI on main green for the first time (`bcb32f2`, [run 33765619088](https://github.com/Orange-hanter/cod-doc/actions/runs/33765619088)).
- **Current priority: adoption through symbiosis.** Pilots reassigned to
  **ZAIrgRush** (multi-agent loop) and **Orakul/ai-review** (LLM PR review) —
  [RFC 22](proposals/22-symbiosis-zairgrush-orakul.md), decision 2026-08-25.
  cod-doc provides specs/ADR/context, the pilots return findings and measurements.
- **Preparation for M6 "Hub + cross-projectness":** M1–M5 are closed, preparation
  for M6 is underway. **M6 goals:** (1) cross-project search through a hub DB
  (`[[doc:slug:key]]`), (2) fix ChromaDB L3 mode for multi-project support,
  (3) extend `agent_pick --projects` to work with multiple projects,
  (4) launch RFC 23 (Cloud decentralized agent plane) and RFC 24 (a unified
  structure/contracts/scenarios loop). **Progress:** SYM-011 (cross-project
  search, low priority) is open and waiting to start; RFC 23 and RFC 24 are
  designed (status 🟠 DEFERRED), tasks CAP-*/STR-* not started; tracks D/E (STO-* Postgres
  parity, ADO-071..095 friction) run in the background. The full roadmap is at
  [ROADMAP](docs/system/roadmap/ROADMAP.md).
- **Open:** plan `adoption-2026-08` — M1–M5 are closed.
  **Remaining in Phase 5:** SYM-011 (cross-project search, `[[doc:slug:key]]`, low).
  **Tracks D/E:** STO-* (Postgres parity, 25 tasks), ADO-071..095 (friction from live work).
  STB-023 (SSE, low) — kept closed until an event-driven scenario.
  STB-012 closed `cancelled` (re-scoped into ADO-013).

## 2. 🗺️ Context Map

```mermaid
graph TD
    Root["MASTER.md (L0 navigator)"] --> Sys["docs/system/MASTER.md (system-of-truth)"]
    Root --> Prop["proposals/README.md (RFC backlog)"]
    Root --> Legacy["L0 bootstrap docs"]

    Sys --> Vision["docs/system/VISION.md"]
    Sys --> ArchSys["docs/system/ARCHITECTURE.md"]
    Sys --> Data["docs/system/DATA_MODEL.md"]
    Sys --> Caps["docs/system/capabilities/*"]
    Sys --> Stand["docs/system/standards/*"]
    Sys --> Audit["docs/system/audit/*"]
    Sys --> Road["docs/system/roadmap/*"]
    Sys --> Migr["docs/system/migration/from-restate.md"]

    Legacy --> Arch["arch/architecture.md"]
    Legacy --> Specs["specs/modules.md"]
    Legacy --> Models["models/domain.md"]

    Root --> Readme["README.md (showcase, EN)"]
    Root --> Hand["docs/HANDBOOK.md"]
    Root --> Guide["docs/cod-doc-guide.md"]
    Root --> Play["docs/adoption-playbook.md"]
    Root --> MCP["docs/mcp-integration.md"]
    Root --> CI[".github/workflows/ci.yml"]
    Root --> CD[".github/workflows/cd.yml"]
```

## 3. 🧩 Modular Sections

> Each section is a link to a single file. For the agent: `@Orchestrator: open section "..."`.
> Hashes verified by `check_stale_refs(cod-doc)` 2026-09-07 → **16/16 VALID**.

### System Documentation Index (canonical) ⭐
- **Description:** The target description package for COD-DOC: VISION, ARCHITECTURE, DATA_MODEL,
  capabilities/*, standards/*, audit/*, roadmap/*, migration/. This is the source of
  truth for system behavior and the single entry point for a contributor.
- **Reference:** `📁 /docs/system/MASTER.md | 🗃️ doc:docs_system_MASTER_md | 🔑 sha:0586937897d3`
- **Status:** `🟢 VERIFIED`

### Proposals (RFC backlog)
- **Description:** 24 RFCs in four tracks:
  - **01–15 (paperclip-track):** 🟢 Implemented — adaptation of paperclipai/paperclip patterns
    (skills, heartbeat, wake-payload, run-id, issue docs, checkout, routines,
    status taxonomy, activity log, adapter pattern, AGENTS.md, approvals,
    import UX, legacy migration, link system). The plan is closed.
  - **16–21 (hackathon-track):** 🔴 Rejected 2026-08-29 — AI-Pair-Hacker,
    Living Specification, Vibecoder's Diary, Context-Scout, Multi-Agent Standup,
    Degraded-Path Auditability. See [proposals/README.md](proposals/README.md) § "Rejection 2026-08-29".
  - **22 (symbiosis-track):** 🟢 Active — Symbiosis: ZAIrgRush + Orakul/ai-review
    (hub DB, findings-ingest, doc context for the external loop and AI review).
    Decomposition — section E of the `adoption-2026-08` plan.
  - **23 (cloud-track):** 🟠 DEFERRED — Cloud decentralized agent plane
    (team node in the cloud, AI workers via remote MCP, SoT = Postgres).
    Tasks CAP-001…CAP-033 are designed, deferred until M6.
  - **24 (structure-track):** 🟠 DEFERRED — A unified structure/contracts/scenarios loop
    (docs↔code boundary, obligations_export, structure_facts, scenario assessment).
    Absorbs the external part of RFC 17, depends on RFC 22. The producer is ready (phases 1–2),
    tasks STR-001…STR-004 are deferred until M6.
- **Reference:** `📁 /proposals/README.md | 🗃️ doc:proposals_README_md | 🔑 sha:333c10180256`
- **Status:** `🟢 VERIFIED`

### CI Pipeline (GitHub Actions)
- **Description:** Continuous integration: ruff linting (blocking), mypy strict
  (blocking), pytest matrix Python 3.11/3.12/3.13, Docker build + smoke test.
- **Reference:** `📁 /.github/workflows/ci.yml | 🗃️ doc:github_workflows_ci_yml | 🔑 sha:d9c7a1a33f0e`
- **Status:** `🟢 VERIFIED`
- **Responsible agent:** `@Orchestrator`

### CD Pipeline (GitHub Actions)
- **Description:** Delivery: building a Docker image and publishing it to GHCR
  when tagging v* (semantic versioning: v1.2.3).
- **Reference:** `📁 /.github/workflows/cd.yml | 🗃️ doc:github_workflows_cd_yml | 🔑 sha:bec2cea789cd`
- **Status:** `🟢 VERIFIED`
- **Responsible agent:** `@Orchestrator`

### Architecture (L0 bootstrap, overview) — legacy
- **Description:** Multi-layer architecture Presentation/Application/Domain/
  Infrastructure, ADRs, non-functional requirements. A condensed overview for the agent.
  The canonical source is [`docs/system/ARCHITECTURE.md`](docs/system/ARCHITECTURE.md).
- **Reference:** `📁 /arch/architecture.md | 🗃️ doc:arch_architecture_md | 🔑 sha:5858a830b798`
- **Status:** `🟡 LEGACY` — overview, see canonical for current details.
- **Responsible agent:** `@Orchestrator`

### Module Specification (L0 bootstrap) — legacy
- **Description:** api/app/domain/infra contracts, the dependency scheme. A
  bootstrap contract map. The canonical breakdown of functionality is in
  [`docs/system/capabilities/`](docs/system/capabilities/).
- **Reference:** `📁 /specs/modules.md | 🗃️ doc:specs_modules_md | 🔑 sha:445a291f6927`
- **Status:** `🟡 LEGACY`
- **Responsible agent:** `@Orchestrator`

### Domain Models (L0 bootstrap) — legacy
- **Description:** Aggregates (Project, Task, Document), Value Objects, domain
  events, repository ports. The canonical schema description is in
  [`docs/system/DATA_MODEL.md`](docs/system/DATA_MODEL.md).
- **Reference:** `📁 /models/domain.md | 🗃️ doc:models_domain_md | 🔑 sha:8ce613932ac9`
- **Status:** `🟡 LEGACY`
- **Responsible agent:** `@Orchestrator`

### README (project showcase, English) ⭐
- **Description:** Entry point for an outside GitHub/PyPI reader: what it is, why
  a DB instead of bare markdown, a 5-line quick start, the four surfaces,
  a table of links to the rest of the docs. Substituted as the package's
  `long_description` (`pyproject.toml → readme`).
- **Reference:** `📁 /README.md | 🗃️ doc:README_md | 🔑 sha:cdb02d871cd1`
- **Status:** `🟢 VERIFIED`

### Handbook (user reference)
- **Description:** Full guide: installation, Quick Start, Web UI tour, CLI,
  configuration, MCP, AI agent, ChromaDB, troubleshooting.
- **Reference:** `📁 /docs/HANDBOOK.md | 🗃️ doc:docs_HANDBOOK_md | 🔑 sha:c62a2ef1dd9b`
- **Status:** `🟢 VERIFIED`

### Documentation guide (tutorial)
- **Description:** A step-by-step guide to creating project documentation from scratch
  via COD-DOC (~30 minutes, weather-cli example).
- **Reference:** `📁 /docs/cod-doc-guide.md | 🗃️ doc:docs_cod-doc-guide_md | 🔑 sha:fe0d62964f49`
- **Status:** `🟢 VERIFIED`

### Adoption Playbook (how to roll out on your projects) ⭐
- **Description:** Scenarios for rolling COD-DOC out on **existing** repositories
  with accumulated markdown: fixing the config, picking a pilot, 4 project
  archetypes, the daily cycle, known rough edges. Unlike the tutorial, this is
  about live repositories, not a from-scratch example.
- **Reference:** `📁 /docs/adoption-playbook.md | 🗃️ doc:docs_adoption-playbook_md | 🔑 sha:d48789a446d6`
- **Status:** `🟢 VERIFIED`

### MCP integration (catalog)
- **Description:** Connecting hosts via `cod-doc connect install` (absolute
  `cod-doc-mcp` command). Cursor, VS Code Copilot, Claude Desktop, Claude Code,
  Codex. Tool catalog.
- **Reference:** `📁 /docs/mcp-integration.md | 🗃️ doc:docs_mcp-integration_md | 🔑 sha:2ea2686b2180`
- **Status:** `🟢 VERIFIED`

### ROADMAP (milestones and priorities) ⭐
- **Description:** Milestones M1–M6, phase statuses, plan decomposition. M1–M5 are closed,
  M6 (hub + cross-projectness) is in preparation.
- **Reference:** `📁 /docs/system/roadmap/ROADMAP.md | 🗃️ doc:docs_system_roadmap_ROADMAP_md | 🔑 sha:a2f0a4bb7055`
- **Status:** `🟢 VERIFIED`

### RFC 22: Symbiosis (ZAIrgRush + Orakul)
- **Description:** Proposal of the symbiosis program: cod-doc provides specs/ADR/context,
  the pilots return findings and measurements. The 2026-08-25 decision on reassigning
  the pilots.
- **Reference:** `📁 /proposals/22-symbiosis-zairgrush-orakul.md | 🗃️ doc:proposals_22-symbiosis-zairgrush-orakul_md | 🔑 sha:24ad14b78de6`
- **Status:** `🟢 VERIFIED`

### RFC 23: Cloud Decentralized Agent Plane
- **Description:** Proposal of a cloud decentralized agent plane:
  a team node in the cloud, AI workers via remote MCP, source of truth = Postgres.
  Tasks CAP-001…CAP-033 are designed, deferred until M6.
- **Reference:** `📁 /proposals/23-cloud-decentralized-agent-plane.md | 🗃️ doc:proposals_23-cloud-decentralized-agent-plane_md | 🔑 sha:da4f73317a9b`
- **Status:** `🟠 DEFERRED`

### RFC 24: Structure/Contracts/Scenarios (unified loop)
- **Description:** Proposal of a unified structure/contracts/scenarios loop:
  the docs↔code boundary, obligations_export, structure_facts, scenario assessment.
  Absorbs the external part of RFC 17, depends on RFC 22. The producer is ready (phases 1–2),
  tasks STR-001…STR-004 are deferred until M6.
- **Reference:** `📁 /proposals/24-structure-contracts-scenarios.md | 🗃️ doc:proposals_24-structure-contracts-scenarios_md | 🔑 sha:0bc3ac7eec30`
- **Status:** `🟠 DEFERRED`

## 4. ⚡ Quick Actions & Handoffs
```json
{
  "quick_actions": {
    "lint": [
      {"cmd": "ruff check cod_doc/ tests/", "desc": "Style and error check (pycodestyle, pyflakes, isort, bugbear)"},
      {"cmd": "ruff format --check cod_doc/ tests/", "desc": "Format check (no write)"},
      {"cmd": "mypy cod_doc/", "desc": "Static typing (strict mode)"}
    ],
    "test": [
      {"cmd": "pip install -e .[dev]", "desc": "Install dev dependencies (pytest, ruff, mypy, hypothesis)"},
      {"cmd": "pytest tests/ -v --tb=short", "desc": "Run all tests"},
      {"cmd": "pytest tests/ -v --tb=short --timeout=120", "desc": "Tests with a 120s timeout (as in CI)"}
    ],
    "docker": [
      {"cmd": "docker build -t cod-doc .", "desc": "Local image build (python:3.12-slim)"},
      {"cmd": "docker compose up -d", "desc": "Start the service (port 8765, healthcheck after 15s)"},
      {"cmd": "docker compose down", "desc": "Stop and remove the container"}
    ],
    "docs": [
      {"cmd": "open docs/system/MASTER.md", "desc": "Open the system-of-truth"},
      {"cmd": "open proposals/README.md", "desc": "RFC backlog (paperclip adoption)"},
      {"cmd": "cod-doc doc drift --project cod-doc --all", "desc": "Check DB↔markdown drift without rewriting files"}
    ],
    "health": [
      {"cmd": "curl http://localhost:8765/api/projects/cod-doc/health", "desc": "JSON summary of DB health: doc drift, unresolved links, doc_drift routine"}
    ]
  },
  "handoffs": {
    "ci": {
      "workflow": "📁 /.github/workflows/ci.yml | 🗃️ doc:github_workflows_ci_yml | 🔑 sha:d9c7a1a33f0e",
      "trigger": "push / pull_request to main and develop",
      "pipeline": "ruff → mypy → pytest (matrix 3.11/3.12/3.13) → docker build + smoke test"
    },
    "cd": {
      "workflow": "📁 /.github/workflows/cd.yml | 🗃️ doc:github_workflows_cd_yml | 🔑 sha:bec2cea789cd",
      "trigger": "push of a v* tag (semantic versioning: v1.2.3)",
      "pipeline": "docker build → push to GHCR (tags: version, major.minor, major, sha)"
    }
  },
  "handoff_rules": {
    "on_missing_file": "Look for the file on disk → if missing, raise a task via task_create",
    "on_hash_mismatch": "Recompute the hash via hash_file → update the reference in MASTER.md → status 🔴 STALE until synchronization",
    "on_broken_section": "Mark 🔴 BROKEN, request restoration via task_create",
    "on_legacy_doc": "L0 bootstrap documents (arch/specs/models) give an overview; for details go to docs/system/",
    "context_gate": "L0 (this file) — session start; L1 — on explicit section request; L2 — only for dependency analysis"
  }
}
```

## 5. ✅ Validation & Changelog

### 5.1 📋 Validation Table

| # | Document | 🗃️ doc-id | 🔑 Hash (sha:12) | 📅 Verified | Status |
|---|----------|-----------|-----------------|-------------|--------|
| 1 | MASTER.md (this file) | `doc:MASTER_md` | regen-on-write | 2026-07-29 | 🟢 VERIFIED |
| 2 | CI Pipeline | `doc:github_workflows_ci_yml` | `d9c7a1a33f0e` | 2026-09-07 | 🟢 VERIFIED |
| 3 | CD Pipeline | `doc:github_workflows_cd_yml` | `bec2cea789cd` | 2026-07-29 | 🟢 VERIFIED |
| 4 | Architecture (legacy) | `doc:arch_architecture_md` | `7d32687d9139` | 2026-07-29 | 🟡 LEGACY |
| 5 | Module Specification (legacy) | `doc:specs_modules_md` | `5c335c97fd99` | 2026-07-29 | 🟡 LEGACY |
| 6 | Domain Models (legacy) | `doc:models_domain_md` | `0a25ddfd9b0c` | 2026-09-07 | 🟡 LEGACY |
| 7 | Handbook | `doc:docs_HANDBOOK_md` | `c62a2ef1dd9b` | 2026-09-11 | 🟢 VERIFIED |
| 8 | Documentation guide | `doc:docs_cod-doc-guide_md` | `562c1f392f47` | 2026-07-29 | 🟢 VERIFIED |
| 9 | MCP integration | `doc:docs_mcp-integration_md` | `2ea2686b2180` | 2026-09-11 | 🟢 VERIFIED |
| 10 | Adoption Playbook | `doc:docs_adoption-playbook_md` | `d48789a446d6` | 2026-09-11 | 🟢 VERIFIED |
| 11 | README (showcase) | `doc:README_md` | `cdb02d871cd1` | 2026-09-07 | 🟢 VERIFIED |

> **Total:** 11 documents | 🟢 VERIFIED: 8 | 🟡 LEGACY: 3 | 🔴 STALE: 0 | 🔴 BROKEN: 0
>
> **Recount 2026-07-29:** 4 hashes were STALE (`arch/architecture.md`,
> `HANDBOOK.md`, `cod-doc-guide.md`, `mcp-integration.md`) — the files were
> edited by legitimate commits (`3d2b329`, `27f3d6f`, `c310503`, `b4ad388`,
> `a73dcbb`), but the registry lagged behind. The content was verified
> against git history before
> updating (skill `drift-handling`: do not update hashes blindly).
>
> **Canonical package** (`docs/system/`) — a separate document registry, see
> [`docs/system/MASTER.md §5`](docs/system/MASTER.md).

### 5.2 🤖 Agent Self-Check
```json
{
  "self_check": {
    "links_verified": true,
    "hashes_match": true,
    "no_hallucinations": true,
    "context_depth": "L0",
    "missing_info": [
      "capabilities/project-bootstrap.md describes 'cod-doc project new'; the CLI gives 'project add' + 'project init' (task D-4)",
      "66 live web routes are missing from capabilities/web-frontend.md §3 (F3, task D-1)"
    ]
  }
}
```

### 5.3 📝 Changelog
```json
{
  "changelog": [
    {
      "date": "2026-09-07",
      "version": "2.12",
      "action": "Restored sections removed by the autonomous daemon on 2026-09-07 (commits 03b3c60, 0522c0f): §4 Quick Actions & Handoffs, §5 Validation & Changelog (registry of 11 documents + self-check), Snowball Protocol. Removed changelog entries with future dates (2026-09-12, 2026-09-13) — the daemon took the model cutoff date as the current date. Registry §5.1 recounted: 5 hashes updated (ci.yml, models/domain.md, HANDBOOK.md, mcp-integration.md, README.md), the files changed via legitimate commits. Hybrid references in §3 re-verified: 16/16 VALID. Daemon disabled (agent_enabled=false).",
      "author": "claude-opus-5",
      "scope": "master"
    },
    {
      "date": "2026-09-07",
      "version": "2.11",
      "action": "Updated the status of RFC 23 and RFC 24 (DRAFT → DEFERRED): tasks CAP-001…CAP-033 and STR-001…STR-004 deferred until M6 \"Hub + cross-projectness\". Updated Executive Summary (§1), Proposals (§3), ROADMAP (M6 section). §3 now has links to ROADMAP, RFC 22, RFC 23, RFC 24 — the hybrid reference registry grew 10 → 16.",
      "author": "claude-opus-5",
      "scope": "master"
    },
    {
      "date": "2026-08-28",
      "version": "2.4",
      "action": "M1 \"Pilot works\" closed: ZAIrgRush (31 docs, ADO-016) and Orakul (405 docs, ADO-017) set up; SYM-003 — loopback bind by default + gate on POST /settings (ae5911e); the worktree-swarm branch merged (ADO-015 types, SYM-004 --exclude, ADO-022 fidelity, 1a66aaa). Phase 1 foundation: hub infrastructure (ProjectEntry.db_url, db_for_entry, cod-doc hub init — SYM-005A), migrations 0027_shared_hub (UNIQUE(project_id, task_id)) and 0028_findings (finding/finding_source_run/external_ref + FTS scope), finding_service (fingerprint/dedup/promote — SYM-005D), ingest adapters ai_review/zairgrush (SYM-006A), CLI ingest + finding stability (SYM-006B). Pilot friction log: 14 entries; ADO-023 raised (import does not set projection_hash).",
      "author": "Sprint 2026-08-27 M1+Phase1",
      "scope": "master",
      "rfc": "proposals/22-symbiosis-zairgrush-orakul.md"
    },
    {
      "date": "2026-08-25",
      "version": "2.3",
      "action": "SYM-002 + ADO-002. File SQLite switched to WAL + busy_timeout=5000 + synchronous=NORMAL (the shared listener is reused in alembic-env), migration 0023_fts5_index got a dialect-guard — findings B7/B10 of RFC 22. A README.md (English showcase) appeared in the root, pyproject.readme switched to it — closed finding F6 of the 2026-07-29 audit, registry 10 → 11 documents.",
      "author": "SYM-002 / ADO-002",
      "scope": "master",
      "rfc": "proposals/22-symbiosis-zairgrush-orakul.md"
    },
    {
      "date": "2026-08-25",
      "version": "2.2",
      "action": "Symbiosis program (RFC 22): pilots reassigned Mushrooms/yana → ZAIrgRush/Orakul (ADO-003/004 cancelled, ADO-016/017 created); section E (SYM-001…011, Phases 0–5) added to the adoption-2026-08 plan; STB-012 cancelled (re-scoped into ADO-013); ADO-010 split into 2 stages (guard → byte-identical); ADO-015 expanded for pilot types. ROADMAP rebuilt, RFC catalog 21 → 22.",
      "author": "Symbiosis Reorg 2026-08-25",
      "scope": "master",
      "rfc": "proposals/22-symbiosis-zairgrush-orakul.md"
    },
    {
      "date": "2026-07-29",
      "version": "2.1",
      "action": "State-of-the-project refresh. Run: 1356 tests passed, ruff/mypy clean, 106 docs in_sync, plan audit ×5 without issues. Closed STB-013 (ContextService L2/L3 implemented — removed an outdated docstring). Built a repo-index (625 files / 3021 symbols). Skill catalog 9 → 12: extracted project-onboarding, ground-truth-reconcile, rfc-authoring. L0-payload agent_capabilities compressed 4543 → 3586 bytes (trigger lists cut). Recounted 4 STALE registry hashes. Added docs/adoption-playbook.md. ROADMAP rebuilt: priority shifted from features to adoption (M1/M2/M3).",
      "author": "State Refresh 2026-07-29",
      "scope": "master",
      "audit": "docs/system/audit/2026-07-29-state-of-the-project.md"
    },
    {
      "date": "2026-05-07",
      "version": "2.0",
      "action": "Cycle-1 consolidation: rewrote root MASTER as thin L0 navigator → docs/system + proposals; removed integration-test fixture leak; legacy L0 bootstrap (arch/specs/models) marked 🟡 LEGACY with canonical pointers; verified hash registry (10/10 VALID)",
      "author": "Cod-Doc Consolidation Cycle 1",
      "scope": "master",
      "audit": "docs/system/audit/2026-05-07-doc-consolidation-cycle-1.md"
    },
    {
      "date": "2026-04-05",
      "version": "1.0",
      "action": "Bootstrap MASTER.md (see prior history in git log MASTER.md)",
      "author": "COD-DOC Orchestrator",
      "scope": "master"
    }
  ]
}
```

---

## 📖 Snowball Protocol

| Level | Loaded | When |
|---------|-----------|-------|
| `L0` | Only `MASTER.md` (this file) | Session start (default) |
| `L1` | MASTER.md + 1 target file | Explicit section request |
| `L2` | L1 + dependencies | Dependency analysis request |

**Hybrid reference format:** `📁 {path} | 🗃️ doc:{id} | 🔑 sha:{12hex}`
**Statuses:** `🟢 VERIFIED` | `🟡 LEGACY` | `🟡 DRAFT` | `🔴 STALE` | `🔴 BROKEN`

**Where to look:**
- Priorities, milestones, what to do next → [`docs/system/roadmap/ROADMAP.md`](docs/system/roadmap/ROADMAP.md)
- How to roll cod-doc out on your project → [`docs/adoption-playbook.md`](docs/adoption-playbook.md)
- Skill catalog (12) → [`cod_doc/skills/`](cod_doc/skills/)
- Target architecture and DATA_MODEL → [`docs/system/`](docs/system/)
- Capability descriptions (one capability = one file) → [`docs/system/capabilities/`](docs/system/capabilities/)
- Standards for frontmatter / task-plan / link / sensitive-data → [`docs/system/standards/`](docs/system/standards/)
- Audit reports per section → [`docs/system/audit/`](docs/system/audit/)
- Execution plans → [`docs/system/roadmap/`](docs/system/roadmap/)
- Borrowings and ideas to adopt → [`proposals/`](proposals/)
