# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

COD-DOC — an MCP server + autonomous agent for managing project
documentation. Documents, tasks, plans, stories, links and revisions live
**in the DB** (`<project>/.cod-doc/state.db`); markdown is a secondary
projection.

Required reading before the first commit: [`AGENTS.md`](AGENTS.md) —
contribution rules (DB-workflow, Definition of Done, PR requirements,
validation pattern, audit cadence). This file does not duplicate them.
Navigation: [`MASTER.md`](MASTER.md) (L0) →
[`docs/system/MASTER.md`](docs/system/MASTER.md) (canonical) →
[`docs/system/roadmap/ROADMAP.md`](docs/system/roadmap/ROADMAP.md)
(priorities).

## Commands

The virtual environment is `.venv/` at the root (tests themselves look for
`.venv/bin/alembic`).

```bash
pip install -e '.[dev]'
alembic upgrade head                     # local SQLite schema

.venv/bin/pytest tests/ -q --tb=short                       # full run (~1639 tests)
.venv/bin/pytest tests/services/test_task_create.py -q      # one module
.venv/bin/pytest tests/services/test_task_create.py::test_create_auto_generates_task_id -v   # one test
.venv/bin/pytest tests/ -k "checkout" -q                    # by substring
```

Gate before hand-off — exactly what CI runs (`.github/workflows/ci.yml`),
all blocking:

```bash
.venv/bin/ruff check cod_doc/ tests/
.venv/bin/ruff format --check cod_doc/ tests/
.venv/bin/mypy cod_doc/                  # strict
.venv/bin/pytest tests/ --tb=short --timeout=120
```

**"Green gate" = green CI, not a local run** (ADO-070). Gates diverge in
both directions: CI was not green once from 2026-05-06 through 2026-09-03,
while the DoD of sprints M1…M4 said "gates green" by a laptop run. Check
`gh run list --branch main`. Environment-dependent fixes (subprocess,
paths, library versions) run on a fresh venv **before** pushing:

```bash
uv venv --python 3.12 /tmp/ci-repro
uv pip install --python /tmp/ci-repro/bin/python '.[dev]'
uvx ruff@latest check cod_doc/ tests/   # CI installs a fresh ruff, the local venv lags
```

Running the surfaces:

```bash
cod-doc --help                           # CLI (click); groups: task/plan/story/doc/link/revision/adr/project
cod-doc serve                            # REST API + web UI on :8765
cod-doc-mcp                              # MCP stdio; default profile agent (--profile / COD_DOC_PROFILE)
docker compose up -d                     # cod-doc container, healthcheck /api/health
cod-doc doc drift --project cod-doc --all # DB ↔ markdown drift without rewriting
cod-doc ctx docs|drift|search --json     # prompt context in JSON (ctx docs --include-body — with the body)
cod-doc ingest ai_review -p cod-doc --from-pr 123   # findings from a PR artifact via gh; then finding_promote
cod-doc ctx drift -p orakul --pr 562 --comment      # drift gate PR: findings → idempotent comment (--dry-run to check)
```

Migrations: `alembic revision -m "<name>"` → fill the symmetric
`upgrade()`/`downgrade()` → `alembic upgrade head` + `alembic downgrade -1`
as a smoke test.

Git hooks (checking the hybrid reference format
`📁 … | 🗃️ … | 🔑 sha:…`): `bash hooks/install.sh`.

## Architecture

Layers are strictly unidirectional, none references the layer above:

```
cli/ tui/ api/ mcp/   → services/   → domain/   ← infra/
(presentation)          (business logic)  (dataclass+StrEnum)  (SQLAlchemy, migrations, repositories)
```

Things not visible from a single file:

- **Four equal surfaces.** New functionality in `services/` must appear
  both in CLI and in MCP — the agent and a human must have an identical
  interface. There are no direct SQL queries from presentation. For task
  mutations this rule is machine-checked (ADO-067):
  `tests/services/test_task_mutation_surface_parity.py` finds write
  functions of `task_service` by AST and requires a call from
  `cod_doc/mcp/` and `cod_doc/cli/`.
- **DB resolve** (`infra/db.py::resolve_db_url`): explicit override → env
  `COD_DOC_DB_URL` → embedded `<project_root>/.cod-doc/state.db`. The
  project registry is `~/.cod-doc/config.yaml` (overridden by
  `COD_DOC_HOME`), parsing is cached by (mtime, size).
- **MCP: one file = one tool family.** `mcp/tools/*_tools.py` export
  `register(mcp)`; `mcp/server.py` calls them in a loop, then
  `apply_profile()` **filters the already-registered** catalog
  (`mcp/profiles.py`). The `agent` profile is the **default**, 6
  task-centric tools, each returns a self-sufficient payload; then
  `minimal` 20 / `standard` 110 / `full` 114. The counters are pinned
  by the test `test_server_profiles.py` and duplicated in FIVE places:
  `mcp/profiles.py` (docstring), `server.py --profile`, `AGENTS.md` §5.9,
  this file and `docs/mcp-integration.md` (the family row + TOTAL) —
  changing the tool set, edit all five. New agent-features go into
  `agent_*`, not into extending the internal CRUD.
- **`mcp/tools/_db.py`** — the common DB entry for tools:
  `session_factory(project)` resolves the slug (or workspace-default) →
  Config → engine. `project=None` fails with a hint, not with a None key.
- **The services layer does not look up.** Not a single import of
  `cod_doc.mcp/api/cli/tui` from `services/` — shared code travels down
  (task serializers live in `services/serializers.py`, `mcp/tools/_db.py`
  only re-exports them). Guarded by the AST gate
  `tests/services/test_services_layering.py` (analog of
  `tests/api/test_web_layer_imports.py`).
- **Atomic checkout is a protocol rule (ADO-039).** The
  `todo→in_progress` transition is allowed only through
  `task_checkout`: a direct `update_status` fails unless
  `via_checkout=True` is passed. `complete()` does the checkout leg
  itself (ADO-038), so closing a task from `todo` is still possible.
- **The write path must leave a trace (ADO-040).** Mutating services write
  a revision and an activity event with one atomic call to
  `activity_service.write_revision_and_emit_event` (or `emit_for_write`
  where there is no revision) inside the mutation transaction; errors
  are not swallowed, `actor_kind` is derived from `author`. A new
  write-service without an event is a regression, caught by
  `tests/services/test_activity_write_path.py`.
- **run_id is telemetry, not a contract** (ADR-012). `run_scope`
  (`services/run_context.py`) is opened only by the built-in orchestrator,
  which is unused: on the live DB `revision` 2166/2166 and
  `activity_event` 1114/1114 have `run_id IS NULL`. The column is left
  nullable; do not write code relying on its non-emptiness. Provenance
  is carried by `author` / `actor_id`, and the role is derived **only**
  through `domain.entities.actor_kind_for_author` — the single point of
  derivation. The `audit_log` table is removed (migration 0029), the
  write-operation log is `activity_event`.
- **Task statuses** — 7 canonical buckets plus legacy aliases
  (`pending`≡`todo`, `in-progress`≡`in_progress`), normalization and
  `ALLOWED_TRANSITIONS` in `services/task_status_machine.py`. The single
  source of truth; docstrings and skills are checked by a test for
  consistency.
- **Context snowball** (`services/context_service.py`): L0 = MASTER only,
  L1 = + direct links, L2 = + dependency chains. Returns JSON + markdown
  excerpts under a token budget.
- **Skills** — `cod_doc/skills/<name>/SKILL.md` (YAML-frontmatter +
  Markdown), matched by `agent/skill_matcher.py`. New agent behavior →
  a new/edited skill, **not** a system-prompt edit.
- **Markdown projection** — an artifact, not a source:
  `Document.projection_hash` catches edit-in-place (`cod-doc doc drift`),
  and `MASTER.md` keeps a separate registry of file hashes — recompute
  via `cod-doc hash update` (`core/hash_calc.py::update_hashes`). Edited
  `doc.body` — update the registry.

## Anti-drift tests

In `tests/` there are meta-tests that fail on code/documentation
desynchronization — if you edit one side, edit both:

| Test | What it guards |
|---|---|
| `test_mcp.py::test_mcp_lists_tools` | tool names in the catalog |
| `test_tool_naming_style.py` | only `name="snake_case"`, no `name="doc.list"` |
| `test_task_status_docstring_alignment.py` | task-tool docstrings and the `task-standard` skill list all 7 statuses |
| `test_orchestrator_skill_refs.py` | orchestrator SKILL.md does not call non-existent tools |
| `test_mcp_integration_doc.py` | numbers in `docs/mcp-integration.md` = the real `len(list_tools())` |
| `test_web_routes_audit.py` | live web routes are documented |
| `test_server_profiles.py` | profile counts (6/20/106/110) match in code and docs |
| `test_actor_kind_single_source.py` | `actor_kind` is derived only through `domain.entities.actor_kind_for_author` (ADR-012) |
| `services/test_services_layering.py`, `api/test_web_layer_imports.py` | layers do not import upward |
| `services/test_activity_write_path.py` | every write-service emits an activity event |
| `services/test_task_mutation_surface_parity.py` | task mutation in `task_service` is exposed both in MCP and CLI (allowlist with rationales inside) |

## Test fixtures

- `tests/conftest.py` — autouse isolation: replaces `COD_DOC_HOME` with
  tmp, suppresses workspace-discovery, resets process-wide API state
  between cases.
- `tests/services/conftest.py::engine_with_schema` — runs
  `alembic upgrade head` in tmp SQLite, so a new migration is picked up
  automatically, without editing fixtures.
- `asyncio_mode = "auto"` — async tests do not need a marker.

## Session tooling

- The `cod-doc` MCP server (native stdio, `.mcp.json` explicitly sets the
  `standard` profile, not the default `agent`) — 110 tools
  `task_*`/`doc_*`/`plan_*`/…; prefer them to ad-hoc Python scripts.
- `/gate` — the full CI gate in one command.
- Project skills `.claude/skills/`: `task-flow` (checkout → complete with
  sha, creating tasks/sections, service-fallback), `doc-sync` (markdown ↔
  DB, hash registry, drift semantics).
- The PostToolUse hook reminds about `doc import` after editing `.md` —
  this is not noise, it is the law of the repository.

## Conventions

- **English is the standard for documentation and code comments.**
  `RUF001/002/003` and `E501` are disabled for `cod_doc/**` and `tests/**`
  for historical reasons (legacy Cyrillic docstrings); do not add new
  non-English text. Existing Cyrillic docstrings are being migrated to
  English.
- ruff: line-length 100, `select = E,W,F,I,UP,B,SIM,TCH,RUF` + the quality
  policy `ANN,C901,PLR2004,RET,PERF,PTH` (bare `Any` is forbidden, magic
  numbers are forbidden, complexity ≤15); mypy `strict` +
  `warn_unreachable` + `disallow_any_unimported`. Existing debt is in the
  ratchet list `per-file-ignores` (can only shrink); rules and rationales
  — `docs/system/standards/code-quality.md`.
  FastAPI/Pydantic/SQLAlchemy types intentionally live outside
  `TYPE_CHECKING` (see `runtime-evaluated-*` in `pyproject.toml`).
- Running tests from a colored terminal — `env -u FORCE_COLOR`: rich
  colors the CLI output in `CliRunner`, string asserts fail on ANSI codes.
- Commits — conventional + task ID: `feat(flow): STB-014 (COD-052) — …`.
- Closing a task — by a record in the DB (`task_complete` /
  `task_update_status`), not only by editing markdown. Closing a plan
  section → an audit-report in `docs/system/audit/`.
