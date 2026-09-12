---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-27
last_updated: 2026-08-27
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
  - ../../adoption-playbook.md
  - ../../../cod_doc/skills/project-onboarding/SKILL.md
  - ../../../cod_doc/skills/audit-cadence/SKILL.md
---

# Sprint 2026-08-27 → 2026-09-10 — "M1 closed, Phase 1 started"

> **Purpose.** A two-week sprint: close milestone M1 "Pilot works"
> (ROADMAP) and lay the foundation of Phase 1 of Symbiosis (hub + findings).
>
> **Not source of truth.** Task statuses are in the DB (plan `adoption-2026-08`);
> priorities are in [ROADMAP.md](ROADMAP.md). This document fixes the sprint
> agreements: goals, contracts, schemas.

## 0. Ground truth at sprint start (reconciled 2026-08-27)

- Plan `adoption-2026-08`: 31 tasks, 12 done, 19 remaining.
- Phase 0 is almost done: SYM-001/002/004, ADO-001/002/010/015/018–022 — done.
  Only **SYM-003** remains open from Phase 0.
- **Migration `20260826_0026_document_type_recoercion` exists on the
  `worktree-swarm-ado022-ado015-sym004` branch, not merged into `main`.**
  Phase 1 migration numbers are shifted: `0027_shared_hub`, `0028_findings`
  (in RFC 22 — 0026/0027). Precondition for SYM-005B: merge the worktree
  branch into main.
- Pilots: ZAIrgRush — `/Users/dakh/Git/_my/ZAIrgRush`; Orakul —
  `/Users/dakh/Git/_my/Mozarella/Orakul`; ai-reviewer —
  `/Users/dakh/Git/_my/ai-reviewer` (not under Mozarella — a divergence from
  RFC 22 §1; the ingest adapter pins to `payload.version`, not to the path).

## 1. Goals

- **G1 — M1 "Pilot works" closed.** SYM-003, ADO-016, ADO-017 → done:
  both pilots are set up per the 5 criteria of `project-onboarding`, the
  cod-doc surface is safe for a foreign repository.
- **G2 — Foundation of Phase 1.** SYM-005 (all subtasks) → done; SYM-006 —
  at minimum SYM-006A/B (ingest registry + CLI).
- **G3 — Feedback loop started.** ADO-005 is open, ≥5 observations from
  live work; ADO-007 — the `doc_drift` routine runs over the ZAIrgRush pilot.

## 2. Task contracts

### Wave 0 (ready now)

| ID | Contract (acceptance) |
|---|---|
| **SYM-003** | `cod-doc serve` listens on 127.0.0.1; `POST /settings` from non-loopback → 403; `COD_DOC_BIND=0.0.0.0` deliberately restores the old behavior. Files: `cod_doc/config.py:152`, `Dockerfile:41`, gate in `cod_doc/api/web/pages/_helpers.py` |
| **ADO-016** | 5/5 onboarding criteria; `doc drift --all`: `edited_in_place==0`, `missing==0`; search by a domain term hits; ZAIrgRush `git status` is clean (except `.cod-doc/`); `module-spec` does not dominate. Runbook — task-doc `plan` |
| **ADO-017** | The same 5 criteria for Orakul. Path: `/Users/dakh/Git/_my/Mozarella/Orakul`. blocked_by ADO-016 |
| **ADO-011** (background) | `cod-doc audit --web-routes` → 0 WR-1 and 0 WR-2 |

### SYM-005 → subtasks (Phase 1, hub + findings)

| ID | Content | Contract | blocked_by |
|---|---|---|---|
| **SYM-005A** | Hub infra: `ProjectEntry.db_url`, `infra/db.db_for_entry()` (consolidate ~14 duplicates of resolve→engine→factory), CLI `cod-doc hub init`, reconciliation of `alembic_version` → `schema_mismatch` | `hub init` is idempotent; hub.db in WAL; unit tests for `db_for_entry`; suite is green (embedded state.db without regressions) | — |
| **SYM-005B** | Migration `0027_shared_hub`: batch-rebuild `task` → `UNIQUE(project_id, task_id)` (fact B9). **Precondition: merge `worktree-swarm-ado022-ado015-sym004`** | upgrade/downgrade are symmetric; a fixture with data survives the rebuild; single-project DBs are behaviorally unchanged | SYM-005A |
| **SYM-005C** | Migration `0028_findings`: `finding` / `finding_source_run` / `external_ref` (SQL §4.2) + ORM `infra/models/findings.py` + `"finding"` in `_VALID_SCOPES` and `reindex_all` | upgrade/downgrade are symmetric; UNIQUE(project_id, source, fingerprint) rejects a duplicate (IntegrityError test); mypy strict | SYM-005B |
| **SYM-005D** | `services/finding_service/`: `fingerprint.py` (pure functions, §4.3), `dedup.py` (upsert, `times_seen++`, `finding_source_run`), `promote.py` (the `on_finding` dictionary from a routine) | a repeat ingest → `created=0, updated=N, times_seen=2`; 8 concurrent ingests without `database is locked`; a property test for fingerprint stability; activity event on promote | SYM-005C |

### SYM-006 → subtasks (Phase 1, ingest/ctx surfaces)

| ID | Content | Contract | blocked_by |
|---|---|---|---|
| **SYM-006A** | `services/ingest_service/registry.py`: `INGEST_ADAPTERS: dict[str, Adapter]`, `Adapter.parse(stream) -> list[RawFinding]`; adapters `ai_review.py` (dispatch by `payload.version`), `zairgrush_findings.py`, `zairgrush_tasks.py`. Closes B8 | each adapter parses a golden fixture from a real export; an unknown `payload.version` → an explicit error; registry tests | SYM-005D |
| **SYM-006B** ← sprint minimum G2 | CLI: `cod-doc ingest <adapter> --project SLUG --input FILE\|- [--dry-run] [--json]`; `ingest ai_review --from-pr N` (pull `gh run download`, artifact `pr-review-export-<PR>`); `cod-doc finding stability --project SLUG --sha SHA` | `--dry-run` writes nothing; a repeat `--from-pr` dedups (`created=0`, `times_seen` grows); `finding stability` over 8 runs of the same sha → a Jaccard table (the answer to Orakul P0 #18); cli tests | SYM-006A |
| **SYM-006C** (stretch) | `cod_doc/api/v1/`: router + pydantic; `POST /api/v1/projects/{slug}/findings`, `GET .../context`, `GET /api/v1/search`. Legacy `/api/*` frozen | v1 api tests; legacy routes untouched; `/api/v1` is the only surface of the future Bearer gate (RFC 22 §3.3) | SYM-006B |
| **SYM-006D** (stretch) | MCP `finding_list/get/promote/dismiss` + `ctx_docs/ctx_drift`; profiles `standard`/`full` only | `test_mcp_lists_tools` updated; profiles `minimal`/`agent` are byte-for-byte the same; activity events on write tools (proposal 09) | SYM-006B |

### Track C — feedback loop

| ID | Sprint contract |
|---|---|
| **ADO-005** | Friction log (task-doc `journal`) open from day one of the pilot; ≥5 observations from live work. The full criterion ≥10 — M2, the next sprint |
| **ADO-007** | The `doc_drift` routine is registered in the ZAIrgRush project, runs on cron, findings are not duplicated |

### Out of scope

SYM-007…011 (depend on pilots/SYM-006), ADO-006/012 (M2), STB-023 (keep
closed), Track B (hackathon RFC). ADO-013/014 — opportunistic.

## 3. Pilot onboarding runbook

ADO-016 (ZAIrgRush), per the `project-onboarding` skill:

```bash
cod-doc project add zairgrush --root /Users/dakh/Git/_my/ZAIrgRush
cod-doc project init zairgrush
cod-doc import docs --project zairgrush --dry-run \
  --exclude 'experiments/stand*' --exclude 'experiments/repomap' \
  --exclude '.claude/worktrees'
# decision on archive directories — before removing --dry-run
cod-doc import docs --project zairgrush <the same excludes>
cod-doc doc drift --project zairgrush --all   # edited_in_place==0, missing==0
cod-doc search --project zairgrush "<domain term>"
```

Invariants: only `.cod-doc/`, `MASTER.md`, 3 lines of `.gitignore` are
written to the working tree; the pilot's `git status` is clean; `module-spec`
does not dominate (`select type, count(*) from document`) — ADO-015 lifted
the silent substitution. ADO-017 (Orakul, 371 docs, Diátaxis) — the same
runbook; the exclude list is set after inspecting the repository.

## 4. DB schemas

### 4.1. `0027_shared_hub` — batch-rebuild `task`

```sql
-- SQLite batch rebuild (alembic batch_alter_table):
CREATE TABLE task_new (
    -- all columns as in task, the constraint is replaced:
    CONSTRAINT uq_task_project_taskid UNIQUE (project_id, task_id)
);
INSERT INTO task_new SELECT * FROM task;
DROP TABLE task;
ALTER TABLE task_new RENAME TO task;
-- + recreate indexes/FK; downgrade is mirrored.
```

### 4.2. `0028_findings` — verbatim from RFC 22 §3.2

```sql
CREATE TABLE finding (
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    finding_uid VARCHAR(36) NOT NULL UNIQUE,          -- uuid7
    source VARCHAR(32) NOT NULL,                      -- 'ai_review' | 'zairgrush' | 'routine'
    source_ref VARCHAR(255),                          -- PR#, exp id, routine name
    fingerprint VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,                    -- critical|major|minor|info
    kind VARCHAR(32),
    title TEXT NOT NULL, body TEXT, path TEXT, line INTEGER,
    status VARCHAR(16) NOT NULL DEFAULT 'open',       -- open|resolved|dismissed|promoted
    confidence FLOAT,
    first_seen_at DATETIME NOT NULL, last_seen_at DATETIME NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    promoted_task_id VARCHAR(32),
    payload JSON NOT NULL DEFAULT '{}',
    run_id VARCHAR(36),
    UNIQUE (project_id, source, fingerprint)
);
CREATE TABLE finding_source_run (   -- one row per (finding, external run)
    row_id INTEGER PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES finding(row_id) ON DELETE CASCADE,
    source_run_id VARCHAR(128) NOT NULL,              -- sha+run for ai_review
    ts DATETIME NOT NULL, severity_at_run VARCHAR(16), raw JSON
);
CREATE TABLE external_ref (         -- identity bridge
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    entity_kind VARCHAR(32) NOT NULL, entity_row_id INTEGER NOT NULL,
    system VARCHAR(32) NOT NULL, external_id VARCHAR(128) NOT NULL, url TEXT,
    UNIQUE (project_id, system, external_id)
);
```

Rationale for "not activity_event / not task" (RFC 22): dedup requires an
indexed key and an UPDATE (`times_seen`, `last_seen_at`) — mutating the
audit table is not allowed; most findings will not become tasks, and a
global `task_id` would burn an ID for every recreated finding.

### 4.3. Fingerprints (`finding_service/fingerprint.py`, pure functions — never in adapters)

| source | formula | degradation |
|---|---|---|
| `ai_review` | `sha256(source\|path\|fp\|severity)`; `fp` — the engine's code fingerprint (survives line shifts) | empty `fp` → `normalized_title`, mark `payload.fp_basis="title"` |
| `zairgrush` | `sha256(source\|exp\|variant\|kind)` | `note` (prose) is not part of the key |
| `routine` | `sha256(source\|check_name\|scope_kind\|scope_id)` | — |

### 4.4. Hub config

```python
class ProjectEntry(BaseSettings):
    db_url: str | None = None   # None → embedded <root>/.cod-doc/state.db

def db_for_entry(entry: ProjectEntry) -> tuple[sessionmaker[Session], Engine]: ...
```

Constraints: hub only on a local disk (not iCloud/NFS); `alembic upgrade
head` over the hub — with loops stopped; cod-doc's own DB does **not** move
into the hub (control group).

## 5. Sprint Definition of Done

- [x] G1: SYM-003, ADO-016, ADO-017 → `done` in the DB via `task_complete` with `commit_sha` (2026-08-28: ae5911e / 1a66aaa / 1a66aaa).
- [x] G2: SYM-005A–D → `done`; SYM-006A/B → `done` (2026-08-28: aca5028, 7e532d0, d3f3255, db627a3, 78c9678, d363169).
- [ ] G3: ADO-005 ≥5 observations in the journal; ADO-007 routine runs on the pilot.
- [ ] `ruff check`, `ruff format --check`, `mypy cod_doc/`, `pytest --timeout=120` — green.
- [ ] Activity events on all new write tools (proposal 09).
- [ ] ROADMAP.md + MASTER.md updated (M1 checkboxes, changelog).
- [ ] Audit report `docs/system/audit/2026-09-10-sprint-m1-phase1.md` (skill `audit-cadence`).

## 6. Risks

- **The `worktree-swarm-ado022-ado015-sym004` branch is not merged** —
  SYM-005B starts only after the merge, otherwise two migrations 0026 in
  the tree.
- **Two copies of the ai-review engine** (`ops/pr-review/` in Orakul and
  `/Users/dakh/Git/_my/ai-reviewer`) — the adapter pins to `payload.version`,
  not to the path.
- **SYM-006C/D may not fit** — a deliberate stretch; moving them is not a
  G2 failure if SYM-006A/B are closed.
- **The import will pull in archive markdown** — `--dry-run` is mandatory,
  the decision on archives is made before the import (playbook).
- **Pilots "set up and abandoned"** — G3 is in this sprint, not deferred to M2.
- **ZAIrgRush pace** (110 commits / 2 weeks) — any patches to their loop
  (SYM-008, not in this sprint) — one small PR per session.
