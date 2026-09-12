---
type: audit-report
scope: state-of-the-project
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-07-29
last_updated: 2026-07-29
related_docs:
  - ../roadmap/ROADMAP.md
  - 2026-06-05-doc-drift-source-of-truth.md
  - 2026-06-04-self-improvement-compared.md
  - ../../adoption-playbook.md
audience: [contributors, agents]
---

# Audit — State of the Project (2026-07-29)

> **Context.** The last full reconciliation — [2026-06-05](2026-06-05-doc-drift-source-of-truth.md),
> produced the `stabilization-2026-06` plan (Track A). Since then, 12 commits
> landed, closing almost the entire track, but the roadmap was not rewritten.
> This report captures the actual state as of 2026-07-29 and serves as the
> basis for a new [ROADMAP.md](../roadmap/ROADMAP.md).

## TL;DR

**Track A (stabilization) is closed: 12 of 13 tasks.** The project moved from
"has code-confirmed holes" to "engineering-sound but unused". The main deficit
shifted from code quality to **lack of external users**: cod-doc is not used
anywhere except by itself.

Key takeaway for prioritization: the next front is **not** another feature
from `proposals/`, but adoption. See [ROADMAP.md](../roadmap/ROADMAP.md).

## 1. Engineering health — verified, not declared

All numbers were obtained by a run on 2026-07-29, not copied from previous
reports.

| Check | Command | Result |
|---|---|---|
| Tests | `pytest tests/` | **1356 passed**, 0 failed (408 s) |
| Lint | `ruff check cod_doc/ tests/` | All checks passed |
| Types | `mypy cod_doc/` | Success, 287 source files, 0 issues |
| Projection drift | `doc drift -p cod-doc --all` | 106 in_sync, 0 stale, 0 edited, 0 missing |
| Frontmatter | `audit -p cod-doc` | 0 findings (0 errors, 0 warnings) |
| Plan integrity | `plan audit <scope>` ×5 | 0 issues, 0 cycles, 0 done-with-blockers |
| `pragma: no cover` | `grep -rn` | **0** (was 8; closed by STB-010) |

Volume: 43 356 lines in `cod_doc/`, 29 271 lines of tests — ratio 1:0.68.

## 2. DB contents (source of truth)

| Entity | Count |
|---|---|
| Documents | 106 |
| Tasks | 183 (179 `done`, 2 `pending`, 2 `cancelled`) |
| Plans | 5 |
| User stories | 25 (20 accepted, 4 delivered, 1 draft) |
| ADR | 6 |
| Links | 607 |
| Revisions | 1479 |
| Routines | 1 |
| MCP tools | **103** (`agent` profile — 6) |
| Skills | **12** (was 9) |

## 3. What closed since 2026-06-05

The `stabilization-2026-06` plan — **12/13 done**:

| ID | Task | Commit |
|---|---|---|
| STB-001 | Section H: agent-tools docstring + 4 integration tests | `9967b82` |
| STB-002 | Removed 5 legacy YAML modules (863 lines of dead code) | `c310503` |
| STB-003 | WEB-031: import progress via WebSocket | `196bde4` |
| STB-004 | WEB-042: `cod-doc audit --web-routes` | `65c83f9` |
| STB-010 | 8 degraded paths covered by tests, `pragma: no cover` lifted | `91f9a97` |
| STB-011 | `Config.load()` cache (mtime+size keyed) | `72863f9` |
| STB-013 | COD-042/043: ContextService L2/L3 | *(see §4)* |
| STB-014 | COD-052: freeze/rollback projection | `b4ad388` |
| STB-020 | Tail of refactor-large-files confirmed | — |
| STB-021 | audit-followups: 10 doc tasks | `53ce40c` |
| STB-022 | Audit micro-cleanups (typing, event_bus leak, skill loader) | `823d6a1` |

## 4. Findings of this reconciliation

### F1 — STB-013 was hanging `pending` with a ready implementation *(closed)*

`ContextService` L2/L3 were listed as "stubs". Reconciliation with the code
showed otherwise:

- **L2** — `_enrich_l2_task()` (dependency chains via `plan_service.forward_chain` /
  `reverse_chain`) and `_enrich_l2_document()` (cross-doc links) in
  `cod_doc/services/context_service.py`.
- **L3** — `_enrich_l3_semantic()` in the same file: ChromaDB search with a
  graceful skip when the embedding backend is not configured.
- **Coverage** — `tests/services/test_context_service.py`: depth cases for `L2`
  (task + document) and `L3`.

The only real leftover was a stale module docstring ("L2/L3 deferred to
COD-042"). Removed; the task closed with a reason-evidence. A classic case
from the `ground-truth-reconcile` skill § "stub-leftover".

### F2 — repo-index was never built for cod-doc itself *(closed)*

`repo_file` / `repo_symbol` contained **0** records: the
`observability-and-indexing` capability is implemented (OBI-030), but was
never run on the project itself. After `cod-doc reindex files -p cod-doc`:
**625 files, 3021 symbols, 3967 imports**, 57 583 skipped per `.gitignore`.

The symptom is wider than one table: **the project does not dogfood its own
capabilities.**

### F3 — route drift: 66 undocumented web routes *(open)*

`cod-doc audit --web-routes` (the very tool from STB-004) on the live app
yields **67 discrepancies**: 1 documented-but-missing (WR-1) and **66**
undocumented (WR-2) — including `POST /settings` and
`POST /p/{slug}/tasks/{task_id}/fields/{field}/improve`.

The route table in `capabilities/web-frontend.md §3` lags behind the code by
a whole feature section. The check is advisory and CI does not fail on it —
which is why the drift accumulated. → a task in the new roadmap.

### F4 — the agent's L0 payload hit its own ceiling *(closed)*

`agent_capabilities()` keeps the budget under 4 KB (test
`test_agent_capabilities_payload_under_4kb`). The skill catalog was inlined
into the payload in full, including the `Triggers: ...` tails — at 12 skills
that came out to 4543 bytes.

Trigger lists are needed by the **server-side** matcher
(`skill_service.match`), not by the agent. Removed from L0, the signature
was trimmed to a single clause (120 → 60 characters): **3586 bytes, 510 of
headroom** (≈ 4 skills). The payload stopped growing linearly with the
catalog.

### F5 — global config is unfit for real work *(closed 2026-08-25, ADO-001)*

`~/.cod-doc/config.yaml` contains artifacts from a test run:

```yaml
model: test/model
api_key: sk-test-key
projects:
- name: integration-test
  path: /private/var/folders/.../pytest-57/test_agent_run_full_cycle0/my-repo
```

The only registered project is a temporary pytest directory that no longer
exists. Not a single real user project is registered.

Direct consequence: `cod-doc project list` shows garbage, and the agent
cycle (`agent run`) cannot be launched — the model is not configured.

This is **not** a code bug, it is an unfilled config. But it is exactly the
first thing a new user sees.

**Closed 2026-08-25 (ADO-001):** `integration-test` was removed from the
registry (`project remove`, playbook §0), a working OpenRouter key and the
model `anthropic/claude-sonnet-4-6` were written into the config, and the
first real project (`cod-doc`) was registered. The key was verified against
`/api/v1/key`; `cod-doc agent run cod-doc --no-autonomous` passes the
config gate ("No tasks in queue", exit 0).

### F6 — no root README *(open)*

There is no `README.md` at the repository root. `pyproject.toml` substitutes
`docs/cod-doc-guide.md` as the readme — a 30-minute tutorial rather than a
showcase. For a project positioned in a hackathon track (RFC 16–20) that
must explain itself in 30 seconds, this is a hole in the funnel.

### F7 — `doc export` corrupts the document *(closed 2026-08-25, ADO-010)*

Found while trying to bring 7 newly imported documents into `in_sync`.
`doc export` was run on `docs/system/capabilities/backup-and-export.md`;
the file **was restored from a copy immediately**, there is no corruption
in the repository.

Three round-trip defects:

1. **Preamble glued to the first heading.** The `document_body` view does
   `d.preamble || <sections>` without a separator, and `preamble` in the DB
   does not end with a newline. The output:
   `> …the audits were agreed in advance.## 1. Why` — the blockquote and
   the H2 heading merge into a single line. This is **content corruption**,
   not a formatting issue.
2. **Loss of H1.** The document title is parsed into `document.title` and on
   render is re-emitted only as the frontmatter field `title:`.
   `render_markdown` = frontmatter + body, and H1 is in neither preamble nor
   sections. Any export loses the document title.
3. **Substitution of `type`.** `capability` and `audit-report` are missing
   from the `DocumentType` enum, although `standards/frontmatter.md §2`
   lists `audit-report` as valid and ~14 documents use
   `type: capability`. The importer silently substitutes `MODULE_SPEC` —
   and export writes back the wrong type. Split out separately as ADO-015.

Plus cosmetics: frontmatter keys get reordered alphabetically, dates are
quoted, the final newline is lost.

**Why it did not surface earlier.** `grep` across the repository finds no
trace of the gluing, and all capability docs kept their H1 — which means
**`doc export` was never run on this project's documents**. The 106
documents are listed as `in_sync` because their `projection_hash` was set
on the write-path, not by export.

**Why this is critical.** "Markdown is only a projection, generated from
the DB on export" is the central promise of [VISION §2](../VISION.md). The
DB → markdown direction is currently unusable, i.e. **a core premise of
the system is unverified**. This also undermines the playbook advice "do
not export right after import": it is correct, but for a different reason
than assumed.

→ ADO-010 (critical), ADO-015 (medium).

**Closed 2026-08-25 (ADO-010).** Migration `0025_projection_fidelity`: a
`\n\n` separator between `preamble` and the first section in the
`document_body` view; H1 is restored from `document.title` (a new
`title_in_body` column remembers whether it was in the source); frontmatter
is re-emitted **verbatim** from the new `frontmatter_raw` column —
re-serialization from JSON broke key order, quoted dates, and rewrote flow
lists (40 of 71 docs) as block lists. `type: capability` / `audit-report`
is no longer rewritten to `module-spec` on export — the silent
substitution in the DB remains the subject of ADO-015.

Verified on the live corpus: **71 of 71** documents in `docs/` pass
`import → export` byte-for-byte (the regression test
`tests/services/test_projection_roundtrip.py` pins 8 of different types
plus a fixed point). Of the 118 markdown files in the repository, 4 service
ones (`.github/`) diverge: an empty line is appended after `---` or after a
section heading — whitespace normalization, not content loss.

Additionally (stage 1, blocker of pilots): `doc export --dry-run` prints a
unified diff and writes nothing, and the export itself refuses to overwrite
a file that matches neither the last export nor the last accepted import,
and — on CLI/MCP — to write into a foreign checkout. Both guards are lifted
by `--force-write`.

## 5. Skill catalog: what was extracted

Before the reconciliation — 9 skills covering the **internal** agent cycle
(task setup, validation, drift, audit). Analysis of 215 commits revealed
three recurring work cycles **without** formalization:

| New skill | Recurred | Why it was not covered by the old ones |
|---|---|---|
| `project-onboarding` | every "set up a project" entry | `task-standard` and `plan-to-tasks` start **after** import; the "repository → DB" step was in no skill |
| `ground-truth-reconcile` | 2026-06-05, 2026-07-29 | `drift-handling` is about document hash vs file; here it is task status vs implementation in code. Different subject, different arbiter, different outcome |
| `rfc-authoring` | 21 RFCs | the format evolved by precedent (meta-line, "Current state", non-goals), but was never written down — every new RFC reinvented the structure |

Result: **12 skills**. Split in `orchestrator/SKILL.md` into four groups —
working with tasks, integrity, closing phases, project onboarding.

Observation on the skew: **21 RFCs vs 5 plans**. Four times more proposals
were written than work fronts based on them. This is recorded as an
anti-pattern in `rfc-authoring` itself ("RFC instead of a task").

## 6. Open backlog after the reconciliation

`stabilization-2026-06` — 2 `pending`:

| ID | Task | Priority | Comment |
|---|---|---|---|
| STB-012 | RFC #21: degraded-path auditability | high | The motivation is partly stale: 8 `pragma: no cover` are already closed by STB-010. What remains relevant is Tier-2 (`error_audit` for hard exceptions); Tier-1 (ring buffer) lost its basis → revisited in ADO-013 |
| STB-023 | PCA-947: `activity_subscribe` (SSE) | low | Only needed for event-driven orchestration; there is no demand |

The findings of this report were registered in the DB as the plan
**`adoption-2026-08`** (13 tasks, 2 sections) — the DB remains the single
tracker:

| Section | Tasks | Source |
|---|---|---|
| C — Adoption | ADO-001 … ADO-007 | F5, F6 + playbook pilots |
| D — Residual debt | ADO-010 … ADO-015 | F7, F3, F2, STB-012 |

Critical: **ADO-001** (config unfit) and **ADO-010** (export corrupts the
document).

## 7. Conclusion

Track A did its job: technical debt is closed, the suite is green, the
sources of truth converge. But **F2, F5, F6 and F7 all point the same way** —
cod-doc is built and not used, and where it was not used, it does not work.

F7 is the strongest confirmation: the central promise of the system
("markdown is a projection from the DB") is broken precisely because no
one ever walked that path. Dogfooding found in a single evening what 1356
tests, two LLM reviews, and three audits did not — because all of them
looked at the code, but did not use it.

Hence the prioritization of the new ROADMAP: Track C (Adoption) is above
Track B (features), and ADO-010 goes ahead of both — export cannot be used
on pilots while it corrupts documents.

Rationale and milestones — [ROADMAP.md](../roadmap/ROADMAP.md).

## Changelog

| Date | Event |
|------|---------|
| 2026-07-29 | **State-of-the-project audit.** Run of 1356 tests / ruff / mypy / drift / plan audit ×5 — all green. Closed STB-013 (F1: L2/L3 implemented, stale docstring removed). Built repo-index (F2: 625 files / 3021 symbols). Trimmed the `agent_capabilities` L0 payload (F4: 4543 → 3586 bytes). Extracted 3 skills: `project-onboarding`, `ground-truth-reconcile`, `rfc-authoring` (9 → 12). Recomputed 4 STALE hashes in the root `MASTER.md`. Registered 7 untracked documents in the DB. Found **F7: `doc export` corrupts the document** (preamble glued to the heading, loss of H1, substitution of `type`) — the file was restored, there is no corruption in the repository. F3 (66 undocumented routes), F5 (config from a test run), F6 (no root README), F7 recorded as open. Created the `adoption-2026-08` plan — 13 tasks ADO-001…ADO-015. |
| 2026-08-25 | **ADO-001 closed — F5 lifted.** `~/.cod-doc/config.yaml` fixed: `integration-test` removed from the registry (`project remove`), a working OpenRouter key (verified against `/api/v1/key`) and the model `anthropic/claude-sonnet-4-6` were written in, the first real project `cod-doc` was registered (idempotent bootstrap SYM-001). Smoke: `agent run cod-doc --no-autonomous` → "No tasks in queue", exit 0. |
