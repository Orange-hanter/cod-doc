# 22 — Symbiosis: cod-doc ↔ ZAIrgRush ↔ ai-review (Orakul)

> Category: 🔵 Architecture · Risk: high · Dependencies: proposal 04 (run-id), proposal 07 (routines / `on_finding`), proposal 09 (activity log); absorbs the external part of proposal 16 (pair-hacker) and 17 (drift detector)

## 1. Context

cod-doc is engineering-healthy (1356 tests, ruff/mypy strict, 103 MCP-tools, 106
documents `in_sync`) and **is not used anywhere except itself** — conclusion of
[audit 2026-07-29](../docs/system/audit/2026-07-29-state-of-the-project.md):
"dogfooding in one evening found what 1356 tests, two LLM-reviews and three
audits did not — because they all looked at the code, not used it".

This RFC replaces the hypothetical "vibecoders" from proposals 16/17 with two
**real** consumers with real pains:

- **ZAIrgRush** (`~/Git/_my/ZAIrgRush`) — a multi-agent dev loop
  (Executor Kimi ↔ Reviewer Claude ↔ Planner), stdlib-only Python, ~30.5k LOC,
  110 commits in two weeks. It has a **named and not started experiment
  E5 "Docs↔code drift"** (`06-knowledge-infra-experiments.md:239`) — exactly the
  niche cod-doc closes — and no document registry, no hashes, no
  `decisions.jsonl` that its own regulation §3 requires.
- **Orakul / ai-review** (`~/Git/_my/Mozarella/Orakul` + the extracted engine
  `Mozarella/ai-reviewer@0.2.0`) — LLM PR-review without a DB: findings live in
  sticky-comments and CI-artifacts and die with them. Their backlog P0 #18
  fixes the irreproducibility of findings (Jaccard 0.00–0.14 on 8 runs of one
  sha), P0 #19 demands moving deterministic defect classes out of the LLM
  into a gate. Orakul **has no** link/anchor integrity check and no
  frontmatter validator — with 371 documents and a strict hierarchy of truths.

The symbiosis is bidirectional: cod-doc gives specs/ADR/context, the loop and
review return findings, commits and measurements. cod-doc stays an
independent project: both consumers connect voluntarily and disconnect
without consequences (fail-open on their side, read-only on ours).

Owner decisions (2026-08-24): all three repositories can be changed; the DB
schema can be touched; ZAIrgRush goes first; `doc export` is closed with a
guard now, full round-trip fix later; the goal for ai-review is a standalone
`Mozarella/ai-reviewer`, not `ops/pr-review/`.

## 2. Current state (verified against code 2026-08-24)

### 2.1. Blockers in cod-doc

| # | Fact | Where |
|---|---|---|
| B1 | `cod-doc project init` **does not create a DB**: `project_service.init_project` (alembic + a `project` row) has one caller — the web-route; CLI calls only the file-based `Project(entry).init()` | `api/web/pages/project.py:180`; `cli/cmd_project.py:64-80,96-109` |
| B2 | A non-working onboarding sequence is prescribed by three documents | `skills/project-onboarding/SKILL.md:36-40`, `docs/adoption-playbook.md:100`, `docs/HANDBOOK.md:425-426` |
| B3 | `doc export` corrupts documents: the view `document_body` glues the preamble to the first heading without a separator, H1 is lost (ADO-010) | migration `20260425_0006_views_and_defaults.py:56` |
| B4 | `DocumentType` does not know 5 of 6 ZAIrgRush types; an unknown type silently → `MODULE_SPEC` | `domain/entities.py:13-26`; `import_service.py:140-147,345` |
| B5 | No authentication; `api_host` defaults to `0.0.0.0`; `POST /settings` writes the LLM-key | `config.py:152`; grep over `cod_doc/api` |
| B6 | Legacy REST writes to `tasks.yaml`, not the DB | `api/routes.py:144-149` |
| B7 | SQLite without WAL (`journal_mode=delete`, `busy_timeout=0`) | `infra/db.py:37-50` |
| B8 | `import docs` is one-shot (`import_markdown`, not `import_or_update_markdown`); no exclude flag | `restate_importer.py:99-117,148-165` |
| B9 | `task.task_id` UNIQUE **globally**, not `(project_id, task_id)` | schema `task`, `infra/models/plans.py:91` |
| B10 | FTS5 migration without a dialect-guard — Postgres will not come up | `20260515_0023_fts5_index.py:33-44` |
| B11 | `~/.cod-doc/config.yaml` — one dead project-tmpdir; compose mounts a non-existent `/Users/dakh/Git/cod-doc` | direct read |
| B12 | Chroma L3 leaks between projects: `_enrich_l3_semantic` does not pass `project_root` | `services/context_service.py:456-464` |

### 2.2. Five facts that defined the design

1. **bm25 is relative to the corpus.** `search_service` ranks
   `bm25(db_search_idx) ORDER BY score` (`search_service.py:220,228`), and
   `db_search_idx` already carries `project_id UNINDEXED`. In one DB cross-project
   search = `WHERE project_id IN (...)`; merging outputs of three independent
   FTS5-tables gives a fake ranking. → a shared hub-DB, not federation.
2. **Orakul already publishes the export as a CI artifact** —
   `pr-review-export-<PR>`, `if: always()`, retention 30 days
   (`.github/workflows/pr-review.yml:215-226`). → ingest — pull via
   `gh run download`, zero changes in Orakul, zero network exposure of cod-doc.
3. **Export loses the deduplication field.** `slimFinding`
   (`ai-reviewer/lib/export.mjs:45-65`) does not emit `fp` (code-fingerprint,
   `lib/findings.mjs:166`), `verifierStatus`, `actionabilityScore`. Fixable
   with ~10 lines upstream.
4. **ZAIrgRush commits have no task IDs at all** (`gitops.py:270-273`:
   `git commit -qm <one phrase from LLM>`; confirmed by `git log`). Extending
   the `commit_link` regex is pointless — a git-trailer is needed.
5. **"Stdlib-only" = no pip-deps, not no subprocesses.** `mempg.pg()`
   (`mempg.py:26-40`) — the only door to `psql` via subprocess with a guard.
   → transport for the loop is a cod-doc CLI call, not HTTP/MCP
   (MCP in loop agents is off intentionally: `engines.py:140-144`
   `--strict-mcp-config`; ADR-008 already rejected a navigational MCP).

### 2.3. What we reuse (do not invent)

- `routine.on_finding ∈ {create_task, update_existing_task, comment_only}` +
  `routine_run.findings_count` (`infra/models/routines.py:35,58,90`) — the
  findings-promotion dictionary already exists.
- `activity_event` (UUIDv7, `actor_kind/run_id/scope/payload`) — audit of
  external actors; `approval` + `agent_report(kind='approval_request')` —
  human-in-the-loop.
- `commit_link` (`_TASK_REF_RE = \b([A-Z]{2,5}-\d{3}[A-Z]?)\b`), `dependency`,
  `affected_file`, `repo_file/repo_symbol`, `trace_service`, `run_scope()`.
- The MCP profile `agent` (6 tools, self-sufficient card) — the external
  agent contract.
- `ProjectEntry` is already `extra="allow"` (`config.py:33`) — the `db_url`
  field lands without breaking the format; `resolve_db_url` already accepts
  `override` (`infra/db.py:19`).

## 3. Proposal

### 3.1. Hub-DB (hybrid)

`~/.cod-doc/hub.db` — one SQLite with three `project` rows
(`zairgrush`, `orakul`, optionally other pilots). cod-doc's own DB in the hub
**does not move** (the only working install is the control group).
Each repository keeps its own `.cod-doc/state.db` as the default.

```python
# cod_doc/config.py
class ProjectEntry(BaseSettings):
    ...
    db_url: str | None = None   # None → embedded <root>/.cod-doc/state.db

# cod_doc/infra/db.py — consolidate ~14 duplicate resolve→engine→factory
def db_for_entry(entry: ProjectEntry) -> tuple[sessionmaker[Session], Engine]: ...
```

`make_engine` for SQLite additionally: `PRAGMA journal_mode=WAL`,
`busy_timeout=5000`, `synchronous=NORMAL` (except `:memory:`). On opening a
hub session — check `alembic_version`; a mismatch → `{ok: false,
code: "schema_mismatch"}`, not half-work. Limitations fixed in docs:
hub only on local disk (not iCloud/NFS); `alembic upgrade head` on the hub —
with loops stopped.

### 3.2. Findings table (migration `0027_findings`)

```sql
CREATE TABLE finding (
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    finding_uid VARCHAR(36) NOT NULL UNIQUE,          -- uuid7
    source VARCHAR(32) NOT NULL,                      -- 'ai_review' | 'zairgrush' | 'routine'
    source_ref VARCHAR(255),                          -- PR#, exp id, routine name
    fingerprint VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,                    -- normalized: critical|major|minor|info
    kind VARCHAR(32),                                 -- category / kind of the source, as is
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
CREATE TABLE finding_source_run (                     -- one row per (finding, external run)
    row_id INTEGER PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES finding(row_id) ON DELETE CASCADE,
    source_run_id VARCHAR(128) NOT NULL,              -- sha+run for ai_review, loop run-id
    ts DATETIME NOT NULL, severity_at_run VARCHAR(16), raw JSON
);
CREATE TABLE external_ref (                           -- identity bridge
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    entity_kind VARCHAR(32) NOT NULL, entity_row_id INTEGER NOT NULL,
    system VARCHAR(32) NOT NULL, external_id VARCHAR(128) NOT NULL, url TEXT,
    UNIQUE (project_id, system, external_id)
);
```

Why not `activity_event`: it is append-only and without uniqueness —
deduplication requires an indexed key and an UPDATE (`times_seen`,
`last_seen_at`); mutating an audit table = making the audit untrustworthy. Why
not `task`: most findings will not become tasks (Jaccard 0.00–0.14), and a
globally unique `task_id` would burn an identifier per recreated finding.

Fingerprints — pure functions in `services/finding_service/fingerprint.py`,
**never in adapters**:

- `ai_review`: `sha256(source|path|fp|severity)`, where `fp` is the engine
  code-fingerprint (survives line shifts by construction). On empty `fp` —
  degradation to `normalized_title` with a mark `payload.fp_basis="title"`.
- `zairgrush`: `sha256(source|exp|variant|kind)` (`note` is prose, not in the key).
- `routine`: `sha256(source|check_name|scope_kind|scope_id)`.

`finding_source_run` makes Jaccard-stability computable inside cod-doc:
`cod-doc finding stability --project orakul --sha <sha>` answers their P0 #18
with saved data instead of repeat runs.

### 3.3. Contracts: ingest, ctx, api/v1

```
cod-doc ingest <adapter> --project SLUG --input FILE|- [--dry-run] [--json]
cod-doc ingest ai_review --project orakul --from-pr N      # gh run download → ingest
cod-doc ctx docs   --project SLUG --paths p1,p2 --budget-tokens N --json
cod-doc ctx drift  --project SLUG --changed-files f1,f2 --json
cod-doc ctx search --project SLUG "query" --json
cod-doc hub init                                            # create/migrate hub.db
cod-doc finding stability --project SLUG --sha SHA
```

- Adapter registry: `services/ingest_service/registry.py` (`INGEST_ADAPTERS:
  dict[str, Adapter]`, protocol `Adapter.parse(stream) -> list[RawFinding]`).
  Adapters: `ai_review.py` (dispatch by `payload.version` — works both with
  `ops/pr-review/` and the extracted engine), `zairgrush_findings.py`,
  `zairgrush_tasks.py`. This also closes B8: the importer registry finally exists.
- A new package `cod_doc/api/v1/` (router + pydantic-schemas) over the same
  services: `POST /api/v1/projects/{slug}/findings`, `GET .../context`,
  `GET /api/v1/search`. Legacy `/api/*` is **frozen** — do not extend.
- MCP: the `finding_*` family (`finding_list/get/promote/dismiss`) and `ctx_*`
  (`ctx_docs/ctx_drift`) into the `standard`/`full` profiles — an interactive
  session needs them, but the integration does not depend on them.
- Security now = **close the open**: bind defaults to `127.0.0.1`
  (opt-out `COD_DOC_BIND`), loopback-gate on `POST /settings`. Bearer-token —
  described here as a contract (`COD_DOC_API_TOKEN`, ASGI-middleware only on
  `/api/v1`, constant-time compare, 401 JSON), enabled when the first remote
  caller appears. `/api/v1` — the only surface that ever gets it.

### 3.4. ZAIrgRush side (E5 variant C)

| File | Change |
|---|---|
| `tools/swarm/swarm/docctx.py` | a new flat module: one door `codctx(config, args, timeout) -> tuple[bool, str]`, subprocess `["cod-doc", "ctx", ...]`, own guard — a copy of `mempg.pg()` |
| `tools/swarm/swarm/promptbuilder.py:136` | `docs_block(agents, task)` next to `memory_block`, the same byte-stable cache `agents.docs_cache = (tid, block)` |
| `tools/swarm/swarm/promptbuilder.py:162` | `handoff(...)` receives `docs`, render after `unc`, before `mp` |
| `tools/swarm/swarm/cli.py:81-84` | `KNOWN_EXPERIMENT_KEYS += {"doc_context"}`; `DOC_CONTEXT_MODES = {off, executor, reviewer, all}`, validation like `MEMORY_MODES` |
| `tools/swarm/swarm/gitops.py:270-273` | git-trailers after an empty line: `Swarm-Task: r1cf`, `Cod-Doc-Task: ZRG-014`; the subject is not touched |

The default is `off`; with the flag off `handoff` gives a **byte-identical**
prompt (a separate test) — otherwise their E8/E10/E13 stop being comparable.
Framing per their process law: variant C of experiment E5 (a machine map
supported by the tool, vs a hand-written `docmap.toml`), E5 metrics unchanged
+ two new (delay per round, share of guard discoveries), rows in
`experiments/findings.jsonl` (`exp:"E5", variant:"C-coddoc"`), a closing
`experiments/adr/NNN-doc-context-source.md`.

Identity: ZAIrgRush tasks are mirrored as `ZRG-###` (the existing
`_TASK_REF_RE` catches without edits), the native `r1cf` lives in
`external_ref(system='zairgrush')`. The mirror is **read-only**: writing to
`.swarm/tasks.json` means competing with the planner's validated plan-diff.

### 3.5. Orakul / ai-reviewer side

- **Ingest — pull-model** (fact 2.2.2): `--from-pr` downloads the artifact
  `pr-review-export-<PR>`, a local poller `scripts/ingest-orakul-reviews.sh`
  lives in cod-doc. Zero changes in Orakul.
- **One upstream-PR in `ai-reviewer`** (~10 lines): `fp`, `verifierStatus`,
  `actionabilityScore` into `slimFinding`, bump `EXPORT_VERSION`. Justification
  in their terms: export is strictly weaker than the sticky-footer, the
  consumer is forced to recompute stability the engine already computed
  (their P0 #18).
- **Sink at the profile level rejected**: `lib/profile.mjs` normalizes only
  strings/regex/paths; a hook-command field would let a profile execute
  arbitrary code in an engine that `git archive`-s from the base-branch —
  a supply-chain regression.
- **Reverse direction (drift-gate)**: `cod-doc ctx drift` returns findings
  in the engine form (`prescan: true`, `model: "cod-doc/drift"`); v1 is
  delivered by a separate PR-comment from cod-doc under its own marker-namespace.
  Unique value: Orakul has no link/anchor integrity gate and no frontmatter
  validator — but `link_service.verify_section` + `doc_drift` is exactly the
  deterministic class their P0 #19 says to move out of the LLM.
  Criterion: on a PR with a renamed anchor referenced in 3 places,
  cod-doc names all three on 8 runs of one sha (Jaccard 1.00 vs
  0.00–0.14 for the LLM).
- **Deep docs↔code/scenario contour** (structure, coverage, obligations):
  see [proposal 24](24-structure-contracts-scenarios.md) — after SYM-005..009;
  does not duplicate slimFinding/ctx drift, complements `structure_facts` /
  `structure_assessment` and `structure_context` for the garage executor.

### 3.6. Cross-project (last phase)

- `search_service.search(..., project_ids: list[int])` → `IN (...)`;
  `cod-doc search --projects a,b` and `GET /api/v1/search`.
- `link_service`: `[[doc:<slug>:<key>]]` with resolution within projects
  sharing `db_url` — the deferred note `link_service/__init__.py:22-28`
  becomes an implementation.
- B12: `_enrich_l3_semantic` passes the project filter — the Chroma leak
  turns from a bug into an intentional cross-project mode.
- `agent_pick`/`agent_capabilities` get an optional `projects`;
  `task_checkout` locks are re-checked under the hub.

## 4. Migration / backward compatibility

- **`0026_shared_hub`**: batch-rebuild `task` — `UNIQUE(task_id)` →
  `UNIQUE(project_id, task_id)`. For existing single-project DBs
  behaviorally nothing changes. **`0027_findings`**: three tables §3.2 +
  `"finding"` into `_VALID_SCOPES` and `reindex_all` (FTS).
- `DocumentType` — a StrEnum over VARCHAR, extension (`design, audit, journal,
  plan, analysis, research, capability, audit-report`) needs no DB migration;
  `import_service` changes the silent substitution to a warning in payload.
- Dialect-guard in `20260515_0023_fts5_index.py`: non-SQLite → explicit
  `NotImplementedError`, not a mysterious crash.
- `doc export`: `--dry-run` (unified diff) + refuse to write into a project
  with `root_path` ≠ cod-doc root without `--force-write`. Full byte-identical
  round-trip (ADO-010) stays in the backlog and becomes mandatory the day
  writing outside is needed.
- Legacy `/api/*` is not touched and not extended; existing MCP-profiles do not
  change (new tools only in `standard`/`full`).
- cod-doc **does not write** into the ZAIrgRush/Orakul work trees (except
  `.cod-doc/`, `MASTER.md`, 3 lines of `.gitignore` on init and a deliberate
  PR with trailers in `gitops.py`).

## 5. Risks and what we do not do

**Non-goals:**

1. **Postgres** — weeks of work for a task WAL solves. Only a guard.
2. **Writing into `docs/` of Orakul — never.** `check-doc-version-bump.sh`
   without a bypass is intentional; the generator must count `Version:`
   itself (`gen-api-routes-doc.mjs:10-19`), otherwise main turns red unfixably.
3. **cod-doc reachable from GitHub Actions** — the pull-model makes exposure
   unnecessary.
4. **MCP-exception in ZAIrgRush** — do not spend their strongest architectural
   invariant (`--strict-mcp-config`) on convenience.
5. **Pluginization of `CHECK_CATALOG` now** — there is no second consumer, the
   API would be designed from a sample of one.
6. **Two-way task sync with ZAIrgRush** — only a read-only mirror.
7. **One agent loop for three repositories** — common *context* yes,
   execution stays per-repository (three gates, three languages, three review cultures).

**Risks:**

- ZAIrgRush pace (110 commits/2 weeks, 5 worktrees): a loop patch is one
  small PR in one sitting, a long-lived branch will not survive.
- The experiment slot is taken (E9-EXEC inconclusive, E14 active): the E5-C
  measurement queues up; code is written in parallel, "one factor per run"
  is respected.
- Two copies of the ai-review engine: we pin to `payload.version`, not to the path.
- One-shot `import docs` (B8): updates via `doc import <file>` /
  web-scan are baked into the daily cycle from day one.
- "Pilots started and abandoned" — M2 ROADMAP is not closed without a
  friction-log from live work.

## 6. Estimate

Phases (detailed plan: `~/.claude/plans/our-goal-is-prepate-stateless-frost.md`):

| Phase | Content | Size |
|---|---|---|
| 0 | Self-fix of cod-doc: B1/B2/B4/B5/B7/B8/B11 + export guard + dialect-guard | 4–6 days, ~10 tasks |
| 1 | Hub, `finding`-tables, adapter registry, CLI/api-v1/MCP, ADR-bridge ZAIrgRush | 5–8 days, ~10 tasks |
| 2 | `ctx docs` + ZAIrgRush loop patch (E5-C) + trailers | 6–10 days of code, ~6 tasks |
| 3 | Ingest ai-review pull-model + upstream-PR + `finding stability` | 4–6 days, ~5 tasks |
| 4 | `ctx drift` → PR-comment (link/frontmatter gate for Orakul) | 6–10 days, ~5 tasks |
| 5 | Cross-project search, `[[doc:slug:key]]`, B12 fix, `agent_pick --projects` | 8–15 days, ~6 tasks |
| 6 | Reassignment of the adoption plan, rewriting the onboarding skill | 2–3 days, ~3 tasks |

Total: **~45 tasks, 6–10 weeks** of implementation + calendar time of
measurements (E5-queue) that cannot be compressed. Phases 0–1 give no visible
result — this is the price of safe entry into someone else's hot repository.

Decomposition: section C of the `adoption-2026-08` plan is reassigned to the
new pilots (Phase 0 ≈ ADO-001/002/015 + new), for Phases 1–5 — a new section **E
"Symbiosis"** with its own ID range (via `plan_section_create`, not into someone
else's sections). Closing each phase — an audit-report per the `audit-cadence`
skill.

## 7. Sources

- ZAIrgRush: `05-agent-swarm.md` (v0.55), `06-knowledge-infra-experiments.md`
  (E5 §239, status-table §8), `tools/swarm/swarm/{engines,helpers,mempg,promptbuilder,gitops,cli,verdicts}.py`, `experiments/adr/008-hybrid-code-intelligence.md`.
- Orakul: `.github/workflows/pr-review.yml`, `docs/08-technical/37-ai-review-backlog.md`
  (P0 #18/#19), `ops/gen-api-routes-doc.mjs`, `ops/check-doc-version-bump.sh`.
- ai-reviewer: `lib/export.mjs` (`slimFinding`), `lib/findings.mjs`
  (`findingFingerprint`), `lib/profile.mjs`, `docs/integration.md`, `docs/profiles.md`.
- cod-doc: [audit 2026-07-29](../docs/system/audit/2026-07-29-state-of-the-project.md),
  [ROADMAP](../docs/system/roadmap/ROADMAP.md), proposals 04/07/09/16/17.
