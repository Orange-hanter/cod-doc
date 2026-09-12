---
type: audit-report
scope: contract-audit
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-29
last_updated: 2026-08-29
related_docs:
  - ../ARCHITECTURE.md
  - ../DATA_MODEL.md
  - ../standards/code-quality.md
  - ../../../AGENTS.md
audience: [contributors, agents]
---

# Audit — Codebase contracts (ADO-034)

> **Context.** Investigation by owner's decision 2026-08-29: contract
> violations, bad/stale/missing contracts. Analysis only — no code changed.
> Scope: `services/`, `mcp/`, `infra/`, `api/`, `core/`, `domain/`. Method:
> deterministic checks (pyan3 call graph 7143 edges, runtime dump of 111
> MCP tools, signature sigdiff), 4 explore-audits per module, ai-reviewer
> pilot (offline `--diff`). Each finding verified by orchestrator reading
> the code.

## TL;DR

The codebase generally follows the declared contracts, but the audit
uncovered **3 critical violations** (remote overwrite of the LLM key via
`PATCH /api/config`; loss of `frontmatter_raw`/`title_in_body` on
round-trip through `DocumentRepository`; legacy `/api/projects/*` write
endpoints via the YAML path), **18 major** (status machine bypassed via
`complete()` and `task_update_status`; ~10 write-services without activity
events; inverted services→mcp dependency; SQL in the MCP layer; dead
`audit_log`; DATA_MODEL.md and profile docs out of sync) and **12 minor**.
Total 21 findings → 21 tasks in the DB (ADO-035…ADO-055, section D of the
`adoption-2026-08` plan). No code changed; all findings verified by reading.

The ai-reviewer pilot happened: offline `--diff` works, the jury produced
~40 findings, after verification 3 new confirmed ones (C4/M19/M20) and
low FP noise on architectural violations.

## 1. Contract inventory (sources of truth)

| Invariant | Source | Verification method |
|---|---|---|
| Layering presentation → services → domain ← infra | ARCHITECTURE.md §1/§6 | pyan3 graph, edges between layers |
| "No direct SQL in MCP" | ARCHITECTURE.md §6 | grep `select(` in mcp/tools |
| Activity event on every mutation | AGENTS.md §5.6, proposal 09 | grep emit × write-services |
| Atomic checkout todo→in_progress | proposal 06, AGENTS.md §5.3 | grep enforce_checkout / via_checkout |
| Validate transitions | AGENTS.md §5.5, task_status_machine.py | reading status write-paths |
| Run-id on every mutation | proposal 04, AGENTS.md §5.4 | grep run_scope across layers |
| Echo-without-persist | AGENTS.md §5.8 | sigdiff MCP×service×DB |
| Model ↔ migrations ↔ enum ↔ docs | AGENTS.md §6, DATA_MODEL.md | cross-check enums/migrations/docs |
| MCP profiles | AGENTS.md §5.9, profiles.py | runtime counts |
| Hash-verified docs | AGENTS.md §5.1 | grep update_master_hashes |
| Error model + mapping | ARCHITECTURE.md §10 | reading surface code |
| Optimistic locking parent_revision_id | ARCHITECTURE.md §11 | grep expected_parent_revision_id |
| AuditLog for write-operations | ARCHITECTURE.md §9, DATA_MODEL §3.13 | grep AuditLogModel( |
| Bearer gate /api/v1 (deferred until the 1st remote caller) | ARCHITECTURE.md §9, RFC 22 §3.3 | reading server.py |

## 2. Findings

### Critical

- **C1. [violation] `PATCH /api/config` overwrites the LLM key without a
  loopback guard.** `cod_doc/api/routes.py:49-55` — `setattr(cfg, field,
  value); cfg.save()` without `ensure_loopback_client`, while the web form
  `POST /settings` has it (`api/web/pages/_helpers.py:17-22`). With
  `COD_DOC_BIND=0.0.0.0` the key is read/changed remotely. Asymmetry of the
  bind-hygiene contract (SYM-003): the gate exists on one surface but not
  on the other.
- **C2. [violation] `DocumentRepository` round-trip loses
  `frontmatter_raw` / `title_in_body` / `content_sha256_head`.**
  `infra/repositories/document_repo.py:23-66` does not map the fields added
  by migration 0025 (ADO-010) and PCA-928; `domain/entities.py:224-241`
  does not contain them. Any document update through the repository will
  null the verbatim-frontmatter → risk of re-corrupting projections, which
  ADO-010 was closing.
- **C3. [violation] Legacy `POST/PATCH /api/projects/{name}/tasks` writes
  through the YAML path and breaks on migrated projects.**
  `api/routes.py:144-158` → `core/project.py` `add_task` (`RuntimeError`
  after archiving tasks.yaml). Bypasses services: no Revision, no activity
  event, no status machine. Meanwhile v1 docs declare legacy "frozen"
  (`api/v1/__init__.py:11-15`).

### Major

- **M1. [stale] Default MCP profile is `agent`, documentation says
  `standard`.** `mcp/server.py:122` (`default=os.environ.get("COD_DOC_PROFILE",
  "agent")`) vs AGENTS.md §5.9 and `mcp/profiles.py:11` ("standard (current
  default)"). Counts are also stale: minimal ~18→**20**, standard ~80→**107**,
  full ~102→**111** (measured via runtime dump 2026-08-29).
- **M2. [violation] `task_service.complete()` does not call
  `validate_transition`.** `services/task_service.py:516-550` — transition
  to `done` from any status (e.g. `backlog`/`cancelled` → done) bypassing
  ALLOWED_TRANSITIONS; used by MCP `task_complete`, `agent_complete` and
  the web button (`api/web/fragments/tasks_status.py:115-121`).
- **M3. [violation] `todo → in_progress` is reachable bypassing
  `task_checkout`.** `task_status_machine.py:95` — `enforce_checkout=False`
  by default and not a single call with `True` in the repo; the
  `task_update_status` MCP docstring declares MUST via checkout but does
  not implement it; the web fragment
  (`api/web/fragments/tasks_status.py:52-59`) sends `strict=False,
  via_checkout=False`. AGENTS.md §5.3 is not enforced.
- **M4. [violation] Dozens of write-paths without activity events.** The
  service layer has not a single emit: `adr_service` (16 write-sites),
  `approval_service`, `task_doc_service`, `story_service/crud`+`links`,
  `comment_service`, `checkout_service`, `commit_link_service`,
  `link_service/resolver`, `repo_index_service`, `run_context`; `doc_service`
  — 1 emit per 10 write-sites. MCP layer without emit: `plan_create`,
  `story_*`, `routine_*`, `adr_*`, `link_sync`, `revision_revert`.
  Quantitative expression of the known PCA-912 gap.
- **M5. [violation] Emit errors are swallowed.** `task_service.py:405-420`,
  `574-589` — `except Exception: pass` around `activity_service.emit`:
  audit record is best-effort, failure is invisible.
- **M6. [violation] Layering: services → mcp import.**
  `services/agent_service.py:135,276` imports `task_to_dict` from
  `cod_doc.mcp.tools._db` — the dependency arrow is inverted.
- **M7. [violation] SQL directly in the MCP layer.** 7 modules in
  `mcp/tools/` (`link_tools`, `plan_tools`, `revision_tools`, `task_tools`,
  `_db`, `task_doc_tools`, `run_tools`) import `sqlalchemy.select` and
  build queries against models — against the direct prohibition in
  ARCHITECTURE.md §6.
- **M8. [violation] `audit_log` is a dead table.** Not a single writer in
  `services/`, `mcp/`, `api/` (grep `AuditLogModel(` — only the model
  definition `infra/models/revisions.py:48`); only `run_tools` reads it.
  ARCHITECTURE.md §9 / DATA_MODEL §3.13 declare it as the audit of
  write-operations.
- **M9. [bad contract] run-id "on every mutation" is not implemented
  outside the orchestrator.** `run_scope` is opened only by the agent
  runner; API/CLI mutations go with `run_id=NULL` (allowed by
  `run_context.py:7-8`). The proposal 04 contract contradicts its own NULL
  assumption — unverifiable.
- **M10. [stale] DATA_MODEL.md does not match the schema and domain.**
  Task.status described as 3-state (`DATA_MODEL.md:223`) vs 7-state proposal
  08; view `ready_tasks` without `d.kind='blocks'` (`:475-488`) vs all
  migrations with the filter; `EntityKind` without `task_doc`/`adr`
  (`:172`).
- **M11. [stale] Legacy `TaskStatus` in `core/project.py:23-28`**
  (5 values) vs canonical 7-state; `next_pending_task` (:225-230) silently
  ignores `todo` tasks (catches ValueError).
- **M12. [violation] Migrations set `server_default=current_timestamp()`**
  on timestamp fields (migrations 0010–0014, 0018) against DATA_MODEL §5
  (source of time is Python `_utcnow`, without server_default).
- **M13. [violation] FTS5 migration is SQLite-only.**
  `0023_fts5_index.py:33-45` raises `NotImplementedError` on Postgres →
  `alembic upgrade head` is impossible on the server profile, although
  ARCHITECTURE §4.1 declares a common schema.
- **M14. [missing] The domain does not see checkout-lock and
  projection-hash metadata.** `Task` dataclass without `checked_out_by/at`
  (`models/plans.py:114-119` vs `entities.py:295-313`);
  `Document` without `content_sha256_head` (see C2).
- **M15. [missing] No single write-path wrapper.** Each service manually
  combines `rev.write` + emit + run_id; the guarantee "mutation =
  revision + activity + run-tag" does not exist — held by discipline.
- **M16. [violation] Web task mutations without optimistic locking.**
  `tasks_status.py`, `tasks_fields.py` do not pass
  `expected_parent_revision_id` (supported in `task_service`), while the
  section editor does. Partial application of the §11 contract.
- **M17. [missing] Document lifecycle is not validated.**
  `doc_accept`/web accept any `DocumentStatus`
  (`api/web/pages/docs.py:62-68`, `doc_service.py:377-413`) — an analogue
  of the status machine for documents is missing.
- **M18. [bad] `actor_kind` by heuristic.** `author.startswith("agent")`
  (`task_service.py:412` and others) — the author format is not fixed
  anywhere; `mcp:…`, `orchestrator-run-…`, `human:…` are classified
  incorrectly.

### Minor

- **m1.** Raw SQL in services bypassing repositories (`doc_service.py:229`,
  `plan_service/reads.py:93-104`, `search_service.py:281`,
  `routine_service.py:329`).
- **m2.** Our own ROADMAP/sprint documents use types that are not in
  `DocumentType` (`roadmap-index`, `sprint-plan`) → coercion + warnings on
  import (`projection_service/_frontmatter.py:99-110`).
- **m3.** AGENTS.md §5.1 (update_master_hashes) lives only in the legacy
  agent tools (`agent/tools.py:125`); MASTER.md contains legacy
  `doc:*`-links from the YAML era.
- **m4.** `api/web/pages/routines.py:26` uses the private
  `routine_service._cron_next_fire` — encapsulation.
- **m5.** Inconsistent active-profile fallbacks: `agent_tools` —
  `"agent"`, `context_tools` — `"full"`, `server.py` — `"full"`.
- **m6.** `module.module_id` is globally UNIQUE after the shared-hub
  migration (`models/modules.py:25`) — two projects cannot have
  `M1-auth`.
- **m7.** `Section.content_hash` NOT NULL without default — invariant
  held by service discipline.
- **m8.** `link_suggestion.from_section_id` without FK.
- **m9.** `EntityKind` without a DB CHECK (`models/revisions.py:36`).
- **m10.** Migration 0010 comment confuses UUID7/ULID length.
- **m11.** `task_update_status` does not accept `run_id` — tagging only
  via contextvar.
- **m12.** ARCHITECTURE.md: status `draft` with `source_of_truth: true`;
  section numbering is off (10 → 11.1 → 11 → 12.1 → 12).

### Critical (ai-reviewer, verified)

- **C4. [bad contract] Export with `audience` overwrites the canonical
  file with redacted content.** `projection_service/export.py:226-272` —
  `_safe_target(root_path, model.path)` does not depend on audience: the
  redacted body is written to the canonical path, `projection_hash` is
  intentionally not updated (:271). After such an export, drift shows
  edited_in_place, and a subsequent `doc import` pulls the redacted body
  into the DB — corruption of canonical content.
  → **ADO-053**.

## 3. ai-reviewer pilot

The engine lives at `/Users/dakh/Git/_my/ai-reviewer`, offline mode
`bin/pr-review.mjs --diff <patch> --dry-run`, the cod-doc profile in
`/tmp/ai-review-cod-doc/` (nothing was written into the repo). Two runs:

- **A — sprint M2 retrospective** (`git diff cb9178f^..30c43f7`), MoA jury
  (deepseek-v4-flash / kimi-k2.7-code / glm-5.2, aggregator minimax-m3), ~8.5
  min: 0 critical / 8 major / 7 minor / 5 nit.
- **B — the services/ module** (4 families of synthetic diffs), consensus
  kimi-k2.7-code: ~22 findings.

**Newly confirmed after verification (did not overlap with stages 2/4):**

- C4 (audience-export, above).
- **M19. [bad contract] `on_finding='create_task'` is validated but not
  implemented.** `routine_service.py:52` (VALID_ON_FINDING), `:376`
  (validation), `:535` (run_now handles only `update_existing_task` —
  create_task silently no-op). → **ADO-054**.
- **M20. [violation] The import update-path silently loses sections.**
  `import_service.py:461-488` — a double nested `except Exception: pass`:
  failure of `patch_section` → attempt `add_section` → failure → `pass`. The
  import "succeeds", the section is lost. Nearby: FTS-upsert (:444, :495) is
  not isolated by a savepoint — an FTS failure rolls back the entire import.
  → **ADO-055**.

**Minor (into the report, no tasks):** `_check_stale_refs` without a path
containment check from MASTER.md (`routine_service.py:183-197`); race during
`task_id` generation (`task_service.py:91`); the frontmatter field `title` is
not applied on import (`_frontmatter.py:191-204`); `assert d.row_id is not
None` is stripped under `-O` (`doc_service.py:591`); contradictions inside
web-frontend.md (metrics :411, §6/§7 vs :121/:122/:165, private
`_call_lite_raw` in :119/:138); docstring `update_status` vs signature
(`task_service.py:333`).

**FP assessment:** low on architectural violations (direct infra imports in
services, ORM update in `agent_service.release` :594 — confirmed); clear
FPs — "doc_service does not emit activity" (project rule about the MCP
layer) and `PurePath.match **` (pyproject requires 3.13+); "services imports
infra repositories" — a legitimate pattern, severity overstated.

**Conclusions for the ai-reviewer engine (not cod-doc tasks):** the router
hardcodes TS/JS extensions (Python needs `REVIEW_ROUTER=0`); `--dry-run` does
not write JSON/SARIF (`pipeline.mjs:64`), a single export-dir overwrites
files across runs; prescan is JS/TS only; the minimax-m3 aggregator is
unstable on timeouts.

## 4. Method limitations

- pyan3 is an approximate static graph (dynamic dispatch, FastAPI
  decorators give partial edges); it was used for layering, not for
  coverage completeness.
- pycg rejected: unmaintained, crashes on Python 3.13/3.14
  (`ImportManagerError`).
- sigdiff is a heuristic by parameter names; all echo-without-persist
  candidates require manual verification (most are resolve-parameters like
  `task_id`/`plan_scope`, not subject to persistence).

## 5. Next step

21 tasks filed in plan `adoption-2026-08`, section D (one per
critical/major finding; M4+M5+M15 merged into ADO-040, M7+m1 — into
ADO-042, M10+M11 — into ADO-045):

| Task | Finding | Priority |
|---|---|---|
| ADO-035 | C1 loopback guard for PATCH /api/config | critical |
| ADO-036 | C2 DocumentRepository round-trip | critical |
| ADO-037 | C3 legacy /api/projects tasks | critical |
| ADO-038 | M2 complete() without validate_transition | high |
| ADO-039 | M3 enforce atomic checkout | high |
| ADO-040 | M4+M5+M15 activity events / write-path | high |
| ADO-041 | M6 services→mcp import | high |
| ADO-052 | M1 MCP profiles: default + counts | high |
| ADO-053 | C4 audience-export to canonical path | high |
| ADO-055 | M20 import loses sections | high |
| ADO-042 | M7+m1 SQL in mcp/tools and services | medium |
| ADO-043 | M8 dead audit_log | medium |
| ADO-044 | M9 run_id for API/CLI | medium |
| ADO-045 | M10+M11 DATA_MODEL + legacy TaskStatus | medium |
| ADO-046 | M12 server_default=current_timestamp | medium |
| ADO-047 | M13 FTS5 SQLite-only | medium |
| ADO-048 | M14 checkout fields in the domain | medium |
| ADO-049 | M16 optimistic locking web | medium |
| ADO-054 | M19 on_finding=create_task | medium |
| ADO-050 | M17 DocumentStatus state machine | low |
| ADO-051 | M18 actor_kind heuristic | low |

Minor findings (m1…m12 + minor from ai-reviewer) — in §2/§3 of the report,
without tasks. Prioritization and execution order — at the next sprint
planning (M3 per ROADMAP may be displaced by this backlog — owner's
decision).
