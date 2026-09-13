# AGENTS.md — Contributor Guide (people & AI)

> **Who this is for.** Any PR author in this repo — human or AI agent. Read
> before the first commit. Complements `MASTER.md` (what is in the project)
> with the answer to "how to work with it".

> ⚠️ **Cycle-5 implemented (2026-05-15, closed 2026-06-04 by AGN-001).**
> The MCP API is task-centric: the agent profile exposes 6 tools
> (`agent_pick`, `agent_report`, `agent_complete`, `agent_release`,
> `agent_get`, `agent_capabilities`). Bodies are implemented in
> `cod_doc/services/agent_service.py`; MCP wrappers in
> `cod_doc/mcp/tools/agent_tools.py` (a thin wrapper layer, not stubs).
> Coverage: `tests/services/test_agent_pick.py`,
> `tests/services/test_agent_workflow.py`,
> `tests/services/test_agent_profile_contract.py`. The 116-tool CRUD
> surface (`task_*`, `doc_*`, `plan_*`, …) remains for
> `--profile standard|full` (admin / CLI / web). Tracked in plan
> `paperclip-adoption-task-plan` section H. New agent-features → section
> H, not plan_create-style extensions of the internal surface.

## 1. Project goal

COD-DOC — a documentation management system with MCP integration: docs,
tasks, plans, stories, links, revisions — all live in SQLite + Markdown
projections.

The current iteration — Phase 3 paperclip-adoption (Section C — atomic
checkout, routines, 7-state TaskStatus, AGENTS.md). See `MASTER.md` →
Project Status.

## 2. Read first

1. [`MASTER.md`](MASTER.md) — project map, Quick Actions, navigation.
2. [`docs/system/MASTER.md`](docs/system/MASTER.md) — system docs index.
3. [`docs/system/ARCHITECTURE.md`](docs/system/ARCHITECTURE.md) — layers
   and invariants.
4. [`docs/system/DATA_MODEL.md`](docs/system/DATA_MODEL.md) — DB schema.
5. [`cod_doc/skills/orchestrator/SKILL.md`](cod_doc/skills/orchestrator/SKILL.md)
   — the agent heartbeat protocol (proposal 01).
6. If working with RFCs — [`proposals/README.md`](proposals/README.md).

## 3. Repo map

```
cod_doc/
├── agent/         # orchestrator + LLM, prompts, skills runtime
├── api/           # FastAPI + web pages + websocket
├── cli/           # click CLI
├── core/          # domain models and contracts (TaskStatus, EntityKind, …)
├── domain/        # entities (StrEnum + dataclasses)
├── infra/         # SQLAlchemy: models, migrations, repositories, sql helpers
├── mcp/           # MCP server + tools (one file = one tool family)
├── services/      # business logic — written in Python, tests in tests/services/
├── skills/        # YAML-frontmatter Markdown instructions for the agent
└── tui/           # textual TUI (legacy)
proposals/         # RFC drafts (numbered: 01-skills-layer.md, …)
docs/system/       # canonical system docs (audit/, capabilities/, roadmap/)
tests/             # pytest suites: services/ + mcp/ + api/ + agent/ + …
```

## 4. Dev setup

```bash
pip install -e .[dev]
alembic upgrade head            # init/upgrade local SQLite schema
pytest tests/ -v --tb=short     # run the suite
```

On first start set `COD_DOC_DB_URL` or use the default
`sqlite:///./cod-doc.db`.

## 5. Core engineering rules

1. **Hash-verified docs.** Any change to `doc.body` → recompute sha →
   update the `MASTER.md` section with hashes (via
   `update_master_hashes`).
2. **Snowball Protocol.** Load context by levels L0/L1/L2 (see
   `docs/system/capabilities/context-retrieval.md`).
3. **Atomic checkout.** `todo → in_progress` only through
   `task_checkout` (proposal 06, PCA-200). **Enforce is on** (ADO-039,
   Phase 2): a direct `update_status(todo→in_progress)` raises
   `StatusTransitionError` on all surfaces; the web form goes through
   `checkout_service.checkout`.
4. **`run_id` is telemetry, not a contract** (ADR-012, ADO-044). The
   "run-id on all mutations" rule is **lifted**: `run_scope(...)` is
   opened only by the built-in runner (`agent/orchestrator.py`), and work
   goes through MCP, where the scope is not opened. Measurement
   2026-09-06: `revision` 2166/2166 and `activity_event` 1114/1114 with
   `run_id IS NULL`. The column is left nullable — do not write code that
   relies on its non-emptiness. Who made the mutation is carried by
   `author` / `actor_id`, not `run_id`.
5. **Validate transitions.** `task_status_machine.validate_transition`
   is called in `task_service.update_status` — adding a new status →
   update `ALLOWED_TRANSITIONS`.
6. **Activity events on every mutation.** Any new MCP-write-tool emits
   `activity_service.emit(...)` in the same transaction (proposal 09).
   Currently uncovered tools — Section F backlog (PCA-912).
   `actor_kind` is **always** derived through
   `domain.entities.actor_kind_for_author(author)` — the single point of
   derivation (ADR-012); own heuristics at the call site
   (`author.startswith("agent")`, `"run" in agent`) are forbidden and
   caught by `tests/test_actor_kind_single_source.py`. The canonical
   format of `author` / `actor_id` is `<kind>:<id>` (`human:dakh`,
   `agent:claude-opus-5`, `routine:doc_drift_daily`); the role dictionary
   is `domain.entities.ActorKind`.
7. **MCP-tool contracts.** Registration in `cod_doc/mcp/server.py` is
   in sync with the implementation; the docstring goes into `tools/list`.
   For tests — `tests/test_mcp.py::test_mcp_lists_tools` smoke-checks the
   names.
8. **MCP echo-without-persist gap.** When adding new fields to
   `task_create` / `doc_create` — check that they **persist** in the
   related tables (dependency / story_link / affected_file), not just
   get echoed back. See `tests/services/test_task_create.py` (full
   coverage after PCA-936).
9. **MCP server profiles** (PCA-951, cycle-4 default-switch). Run:
   ```
   cod-doc-mcp                                # agent (default)
   cod-doc-mcp --profile minimal              # 21-tool cold-start
   cod-doc-mcp --profile full                 # all 120, including legacy
   COD_DOC_PROFILE=full cod-doc-mcp           # via env
   ```
   - ``agent`` — **default**: 6 task-centric tools for AI agents
     (`agent_pick`, `agent_report`, `agent_complete`, `agent_release`,
     `agent_get`, `agent_capabilities`).
   - ``minimal`` — 21-tool cold-start surface for fresh integrations.
   - ``standard`` — 116 DB-backed tools without legacy YAML.
   - ``full`` — all 120 tools, including legacy. Only for admin scenarios
     and backward compatibility with pre-cycle-3 integrations.
   Counts are pinned by the test
   `tests/test_server_profiles.py::test_profile_counts_match_documented_values` —
   when adding/removing a tool, update the numbers there and here.
   See `cod_doc/mcp/profiles.py`.

## 6. DB schema change workflow

1. Edit the model in `cod_doc/infra/models/<file>.py`.
2. Create a migration: `alembic revision -m "<name>"` →
   `cod_doc/infra/migrations/versions/<rev>.py`.
3. Fill `upgrade()` + `downgrade()` (mandatory symmetric).
4. `alembic upgrade head` locally + `alembic downgrade -1` smoke-test.
5. If an enum / domain changes — update `cod_doc/domain/entities.py`.
6. Tests: `tests/services/conftest.py::engine_with_schema` will
   automatically pick up the new migration.

## 7. Verification before hand-off

```bash
ruff check cod_doc/ tests/
ruff format --check cod_doc/ tests/
mypy cod_doc/
pytest tests/ --tb=short --timeout=120
```

All green → the PR is ready. If something was not run — explicitly note
in the PR description "not run, because <reason>".

The quality policy (ban on bare `Any`, magic numbers, complexity
ceiling, ratchet of existing debt) — in
[`docs/system/standards/code-quality.md`](docs/system/standards/code-quality.md);
the source-of-truth config is `pyproject.toml`.

## 8. Validation pattern

From the memory project (validation_pattern.md):

- **Structural** — `validate_*()` raise. FM-002, FM-003 escalate via
  `approval_request(approval_type='fm_escalation', ...)` (PCA-121).
- **Advisory** — `audit_*()` collect issues. FM-004, FM-005 are written
  to an audit-report or activity_log, do not block.

## 9. Audit cadence

- **A closed plan section** (e.g. Section A → Section B → Section C) →
  `docs/system/audit/<date>-section-<X>-<name>.md` with TL;DR / deliverables
  / findings / acceptance / next step.
- **Opening a new phase** → a kickoff brief in `docs/system/roadmap/`.
- **N audit cycles in a row** on one direction → exactly N audit-reports
  with findings F1/F2/...; findings → backlog (Section F) in the next
  cycle.

## 10. PR requirements

The template is `.github/PULL_REQUEST_TEMPLATE.md`. Mandatory fields:

- **What changed** — bullet list
- **Why** — motivation (link to a task / RFC)
- **How to verify** — steps
- **Risks** — what can break
- **Model used** — the model / author (or `human-authored`)
- **Checklist** — all Definition of Done items

## 11. Definition of Done

- [ ] The behavior matches the acceptance criterion of the task or RFC.
- [ ] `ruff`, `mypy`, `pytest` are green locally.
- [ ] Contracts are synchronized (model ↔ migration ↔ MCP ↔ docs).
- [ ] If the change is visible in the UI — a screenshot / description
  is attached.
- [ ] Activity events are emitted on write operations (proposal 09 /
  PCA-912).
- [ ] Closing the task in the DB via `task_complete` or
  `task_update_status`.
- [ ] If a plan section is closed — an audit-report in
  `docs/system/audit/`.

## 12. Skills for the agent

Files under `cod_doc/skills/<name>/SKILL.md` — the single source of
instructions for the LLM. The frontmatter `name: …` + `description: …`
determines when the skill is activated by the matcher (see
`skill_matcher.py`). Writing new agent behavior → a new skill (or update
an existing one), do not edit the system prompt.
