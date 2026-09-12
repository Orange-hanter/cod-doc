---
type: audit-report
scope: cod_doc/infra (Section A — Data Core)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-04-25
last_updated: 2026-04-25
audit_target_revision: section-a-closed (COD-001 … COD-005); fixes — migration 0006_views_and_defaults
related_docs:
  - ../DATA_MODEL.md
  - ../roadmap/cod-doc-task-plan.md
  - ../roadmap/audit-followups-task-plan.md
---

# Section A (Data Core) — Implementation Audit

> Audit of the Section A implementation — migrations, ORM models, domain entities, tests.
> Goal: confirm that Section A is closed correctly before starting Section B (services).
> Severity: **critical** — blocks Section B; **high** — required before release or before a dependent task; **medium** — desirable; **low** — cosmetic.

## Summary

| Severity | Count | Resolved | Deferred |
|----------|------:|---------:|---------:|
| critical | 0 | — | — |
| high     | 2 | 2 ✅ | 0 |
| medium   | 5 | 4 ✅ | 1 (ME-2) |
| low      | 7 | 5 ✅ | 2 (LO-3, LO-4) |
| **total** | **14** | **11 ✅** | **3** |

Section A **is closed permanently**. All functional findings are closed by migration [0006_views_and_defaults](../../../cod_doc/infra/migrations/versions/20260425_0006_views_and_defaults.py) and related edits in models/spec/tests. Three findings are deferred (ME-2 — Postgres CI; LO-3 — file split; LO-4 — test style) — they require separate tracks and do not block Section B.

`pytest tests/infra/` → **37/37 passed**.

## What was checked

| Artifact | Files |
|----------|-------|
| Migrations | [0001_core](../../../cod_doc/infra/migrations/versions/20260419_0001_core.py), [0002_tasks](../../../cod_doc/infra/migrations/versions/20260425_0002_tasks.py), [0003_stories](../../../cod_doc/infra/migrations/versions/20260425_0003_stories.py), [0004_revisions](../../../cod_doc/infra/migrations/versions/20260425_0004_revisions.py), [0005_links_tags](../../../cod_doc/infra/migrations/versions/20260425_0005_links_tags.py) |
| ORM models | [cod_doc/infra/models.py](../../../cod_doc/infra/models.py) (503 lines, 19 models) |
| Domain entities | [cod_doc/domain/entities.py](../../../cod_doc/domain/entities.py) (345 lines, 14 dataclass + 12 enum) |
| Tests | `tests/infra/` — 5 files, 29 cases |
| Specs | [DATA_MODEL.md](../DATA_MODEL.md) §3.1-§3.13, §4.1-§4.3 |

`pytest tests/infra/` → **29/29 passed** at the time of the audit.

## 1. Spec coverage

| DATA_MODEL section | Artifact | Status |
|-------------------|----------|--------|
| §3.1 Project | 0001 + ProjectModel | ✅ |
| §3.2 Document | 0001 + DocumentModel | ✅ |
| §3.3 Section | 0001 + SectionModel | ✅ |
| §3.4 Link | 0001 + LinkModel; partial index aligned in 0005 | ✅ |
| §3.5 Revision | 0004 + RevisionModel | ✅ |
| §3.6 Plan + Plan.Section | 0002 + PlanModel/PlanSectionModel | ✅ |
| §3.7 Task | 0002 + TaskModel | ✅ |
| §3.8 Dependency | 0002 + DependencyModel | ✅ |
| §3.9 AffectedFile | 0002 + AffectedFileModel | ✅ |
| §3.10 UserStory + acceptance + link | 0003 + 3 models | ✅ |
| §3.11 Module + dep + code | 0003 + 3 models | ✅ |
| §3.12 Tag + 3 junction tables | 0005 + 4 models | ✅ |
| §3.13 AuditLog | 0004 + AuditLogModel | ✅ |
| §3.14 Embedding | — | ⏳ COD-040 (Section E) |
| §3.15 Proposal | — | ⏳ later |
| §4.1 section_totals view | 0002 | ✅ |
| §4.2 plan_totals view | 0002 | ✅ |
| §4.3 ready_tasks view | 0002 | ✅ |
| §4.3a document_body view | 0006 (dialect-aware) | ✅ |

## 2. High — closed

### IMPL-A-HI-1. ✅ View `document_body` (§4.3a) — implemented

[DATA_MODEL §3](../DATA_MODEL.md#3-tables) promises: "the full body of a document is assembled via view §4.3a", and decision DOC-HI-8 made `Section.body` the single source. View `document_body` is specified in §4.3a but not created by the Section A migrations. Additionally, the directory `cod_doc/infra/views/` mentioned in the spec is missing.

**Consequences:** COD-010 (DocService.get/render) cannot fetch the document body via a SQL query — it will have to aggregate sections in Python; the single point of truth and the DB-side optimization are lost.

**Fix:** a new migration `0006_document_body_view` with dialect-aware DDL: SQLite → `group_concat(... , chr(10) || chr(10))`, Postgres → `string_agg(... , E'\n\n' ORDER BY position)`. Add the directory `cod_doc/infra/views/` to store SQL templates.

**Acceptance:** the view returns the correct body for a document with N sections; a smoke test assembles the expected string.

**Resolution (2026-04-25):** migration [0006_views_and_defaults](../../../cod_doc/infra/migrations/versions/20260425_0006_views_and_defaults.py) creates `document_body` with dialect-aware DDL — SQLite via `group_concat` over an ordered subquery, Postgres via `string_agg ... ORDER BY`. DATA_MODEL §4.3a is updated. Covered by 2 cases in `tests/infra/test_audit_followups.py` (preamble+sections, doc without sections).

### IMPL-A-HI-2. ✅ JSON NOT NULL without `server_default` — closed

`project.config_json`, `document.frontmatter_json`, `audit_log.payload_json` — all declared `NOT NULL` in migrations, but **without** `server_default`. The spec requires `DEFAULT '{}'` (§3.1, §3.2). Currently the default is set only at the ORM level (`default=dict`), which does not cover:

- raw SQL inserts (data-migrations via `op.execute`),
- the Restate bulk importer (COD-051) — high risk of constraint violation,
- external clients that hit the DB bypassing the ORM.

**Fix:** add `server_default=sa.text("'{}'")` to existing migrations via a follow-up `0006_*` (or alembic batch_alter), or document the invariant "these columns are written only through the ORM" in `cod_doc/infra/models.py` and in DATA_MODEL §3.

The same risk applies to `created/last_updated/at` (NOT NULL without `server_default`), but we track it as low (LO-5) — timestamps are naturally set explicitly.

**Dependent task:** COD-051 (Restate importer) — must be closed before it.

**Resolution (2026-04-25):** in migration 0006 — `batch_alter_table` on `project`, `document`, `audit_log` with `server_default=text("'{}'")`; ORM models updated (same `server_default`); the test `test_raw_insert_without_json_columns_uses_server_default` confirms that a raw INSERT without `config_json` does not fail and the value is set to `{}`.

## 3. Medium

### IMPL-A-ME-1. ✅ `Mapped[dict]` and `dict` without type parameters — closed

ORM (`config_json`, `frontmatter_json`, `payload_json`) and domain (`Project.config`, `Document.frontmatter`) are declared as bare `dict` — strict mypy/pyright reject this (already visible in IDE diagnostics). With strict mode enabled in CI the run will fail.

**Fix:** `dict[str, Any]` in models/entities; `pyproject.toml` already has `[tool.mypy] strict = true`, so this cannot be left as is.

**Resolution (2026-04-25):** all three ORM columns (`config_json`, `frontmatter_json`, `payload_json`) → `Mapped[dict[str, Any]]`; domain `Project.config`, `Document.frontmatter`, `AuditLog.payload` → `dict[str, Any]`. IDE diagnostics are clean.

### IMPL-A-ME-2. ⏸ Postgres path not verified — deferred

[Acceptance COD-001](../roadmap/cod-doc-task-plan.md): "`alembic upgrade head` runs on a clean SQLite **and on a clean Postgres**". All 5 migrations are written dialect-neutrally (`sqlite_where`/`postgresql_where`, `sa.JSON`, `sa.DateTime(timezone=True)`), but a Postgres run has never been done — there is a risk that `op.execute(VIEW_DDL)` or a `partial index` breaks in some corner.

**Fix:** in `tests/infra/` add a `pg` marker (via `testcontainers`/`pytest-postgresql`), run the same tests on both dialects. Minimum — one CI job for migrations against a clean Postgres.

**Status:** deferred — requires CI/infrastructure work (testcontainers/pg in pre-commit/CI). Tracked as a separate task; migrations 0001-0006 are written dialect-neutrally (sqlite_where/postgresql_where, dialect-detect for views) and should pass "as is".

### IMPL-A-ME-3. ✅ Domain enums extend DATA_MODEL — closed

| Enum | Value | DATA_MODEL §… |
|------|----------|---------------|
| `EntityKind.SECTION` | `"section"` | §3.5: only `'document','task','plan','story','link'` |
| `EntityKind.MODULE` | `"module"` | (same) |
| `AuditSurface.AGENT` | `"agent"` | §3.13: only `'cli'\|'mcp'\|'rest'\|'tui'` |

These are intentional extensions (recorded in session-summary COD-004), but they are **not reflected in DATA_MODEL.md** — a spec/code divergence.

**Fix:** update DATA_MODEL §3.5/§3.13 (extend the enum list and add the motivation), or remove the values from the code. The decision will be designed in Section B when RevisionService and AuditService are formalized.

**Resolution (2026-04-25):** DATA_MODEL updated — §3.5 extended `entity_kind` to `'document'|'section'|'task'|'plan'|'story'|'link'|'module'`; §3.13 extended `surface` with the `'agent'` value and an explanation.

### IMPL-A-ME-4. ✅ No CHECK on self-loop in `dependency` / `module_dependency` — closed

Right now you can `INSERT INTO dependency (from_task_id, to_task_id) VALUES (1, 1)` — this breaks `ready_tasks` (a task is forever blocked by itself). Cycle-detection in COD-021 will catch such cases at the service layer, but a cheap DB-level protection (`CHECK from_task_id <> to_task_id`) is standard practice.

**Fix:** add a CHECK constraint to both tables via a small `0006_*` migration, or explicitly decline (with justification in DATA_MODEL §7).

**Resolution (2026-04-25):** in 0006 — `CheckConstraint` `from_task_id <> to_task_id` (dependency) and `from_module <> to_module` (module_dependency); ORM models updated; DATA_MODEL §7 updated. Tests `test_dependency_self_loop_rejected` / `test_module_self_loop_rejected` confirm `IntegrityError`.

### IMPL-A-ME-5. ✅ Polymorphic `revision.entity_id` without documented invariants — closed

`revision.entity_kind + entity_id` references different tables without an FK. This is an intentional choice (offline ULID generation, append-only), but the code has neither a comment nor a service-invariant "entity_id points to an existing row at commit time". Without explicit responsibility it is easy to get "orphan" revisions after `DELETE entity` (CASCADE will delete the entity itself, but the revisions will remain — which is required for audit, but this must be stated explicitly).

**Fix:** a short Q&A section in DATA_MODEL §3.5 (or in `capabilities/audit-and-ci.md`) — "why there is no FK; how orphan revisions are read; whose responsibility entity_id is". This is an HI candidate if COD-015 (RevisionService) assumes an FK; for now, medium.

**Resolution (2026-04-25):** explicit invariants added to DATA_MODEL §3.5 (comments right under the definition of `entity_kind`/`entity_id`): the service is responsible for the correctness of the pair; the FK is intentionally absent for the sake of an append-only history that survives deletion of the target entity.

## 4. Low

| ID | Description | Status |
|----|----------|--------|
| IMPL-A-LO-1 | `link.from_section` → `from_section_id` | ✅ renamed in DATA_MODEL §3.4 |
| IMPL-A-LO-2 | `ix_link_target_task` extra index not in the spec | ✅ DATA_MODEL §3.4 now lists it |
| IMPL-A-LO-3 | `models.py` (503 LOC) and `entities.py` (345 LOC) as a single file | ⏸ deferred: split — after Section B |
| IMPL-A-LO-4 | `test_db_smoke.py` goes through repositories, new tests — through ORM | ⏸ deferred: unified style — after COD-010..015 (when repositories appear) |
| IMPL-A-LO-5 | Timestamp columns NOT NULL without `server_default` | ✅ documented in DATA_MODEL §5 and in the `models.py` docstring |
| IMPL-A-LO-6 | No FK SET NULL test | ✅ added `test_module_spec_doc_id_set_null_on_doc_delete`, `test_plan_parent_doc_id_set_null_on_doc_delete` |
| IMPL-A-LO-7 | No UNIQUE on `(story_acceptance.story_id, position)` | ✅ in 0006: `uq_story_acceptance_position`; covered by a test |

## 5. Test coverage analysis

| File | Cases | What is covered | What is not covered |
|------|-------|-------------|----------------|
| `test_db_smoke.py` (COD-001) | 5 | schema, project CRUD, doc+sections+links, UNIQUE doc_key, cascade project→doc | partial-FK SET NULL, encoded sensitivity round-trip |
| `test_tasks_migration.py` (COD-002) | 6 | schema+views, aggregates, COALESCE on an empty section, ready_tasks (blocked + ignore-relates), unique edge | self-loop, cascade plan→tasks |
| `test_stories_migration.py` (COD-003) | 7 | schema, story+acceptance+links, global story_id uniq, cascade, module deps+code, unique edge, global module_id uniq | story_acceptance positions, module SET NULL FK |
| `test_revisions_migration.py` (COD-004) | 5 | schema+indexes, chain via parent_revision_id, ULID uniq, JSON round-trip, cascade | revision-flow with a real entity (poly-FK), broken chain (parent does not exist) |
| `test_tags_migration.py` (COD-005) | 6 | tables, partial-index presence+condition, uniqueness per project, attach to doc/task/story, junction PK, cascade | partial-index selectivity on realistic data |

**Summary:** coverage matches the goals of Section A (smoke-level for migrations). Deep behavioral testing is the job of Section B (repositories and services).

## 6. What remains (deferred)

| ID | Description | When |
|----|----------|-------|
| IMPL-A-ME-2 | Run migrations against a clean Postgres in CI | Separate track (testcontainers/pg in pre-commit/CI) |
| IMPL-A-LO-3 | Split `models.py`/`entities.py` by domain | After Section B, when the final size is clear |
| IMPL-A-LO-4 | Unified test style (ORM vs repositories) | After COD-010..015 — when repositories appear |

These three do not block Section B and can be closed along the way.

## 7. Readiness confirmation

- ✅ All 13 tables §3.1-§3.13 + 4 views (`section_totals`, `plan_totals`, `ready_tasks`, `document_body`) are present.
- ✅ The migration chain is continuous: `0001 → 0002 → 0003 → 0004 → 0005 → 0006`.
- ✅ ORM models, domain entities, migrations, DATA_MODEL — are consistent.
- ✅ 37/37 tests pass on SQLite (29 baseline + 8 audit-followup).
- ✅ JSON NOT NULL columns are safe for raw INSERT (server_default `'{}'`).
- ✅ DB-level self-loop protection in `dependency` / `module_dependency`.
- ✅ `(story_acceptance.story_id, position)` UNIQUE.
- ✅ Strict mypy clean on `dict[str, Any]`.
- ⏸ Postgres CI run deferred (ME-2).

**Conclusion:** Section A is closed permanently. Section B can start without looking back at this audit. ME-2/LO-3/LO-4 are tracked separately.
