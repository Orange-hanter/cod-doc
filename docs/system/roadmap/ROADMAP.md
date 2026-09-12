---
type: roadmap-index
scope: cod-doc-roadmap
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-06-05
last_updated: 2026-09-07
audience: [contributors, agents]
related_docs:
  - ../MASTER.md
  - task-graph.md
  - ../../../proposals/README.md
  - ../audit/2026-07-29-state-of-the-project.md
  - ../../adoption-playbook.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
  - ../../../proposals/23-cloud-decentralized-agent-plane.md
  - ../../../proposals/24-structure-contracts-scenarios.md
---

# COD-DOC — Roadmap (canonical index)

> A single prioritization point over all task plans, RFCs, and audits.
> Rebuilt 2026-07-29 after the [state-of-the-project audit](../audit/2026-07-29-state-of-the-project.md):
> Track A (stabilization) is closed 12/13, and the main deficit has shifted
> from code quality to **lack of usage**.

## Source-of-truth rule

1. **The application DB (`.cod-doc/state.db`) is the source of truth for
   tracked tasks.** A task status is determined by a record in the DB, not
   by markdown.
2. **Code is the arbiter on disagreement.** If the DB and markdown
   disagree — look at what is actually implemented (`file:line` + a test)
   and bring both to it.
3. **The markdown Progress Overview is secondary**, updated from the DB/code.

The reconciliation procedure is formalized in the skill
[`ground-truth-reconcile`](../../../cod_doc/skills/ground-truth-reconcile/SKILL.md).

## Navigation

- [System MASTER](../MASTER.md) · [Task graph](task-graph.md)
- [Proposals (RFC 01–24)](../../../proposals/README.md)
- [State-of-the-project audit 2026-07-29](../audit/2026-07-29-state-of-the-project.md)
- [Adoption playbook](../../adoption-playbook.md)

## Where we are

Verified by a run on 2026-07-29, not paraphrased from past reports:

| Measurement | State |
|---|---|
| Tests / lint / types | 1356 passed · ruff clean · mypy clean (287 files) |
| Drift | 106 documents `in_sync`, 0 divergences |
| Plans | 5 plans, 0 issues in `plan audit` |
| Tasks | 179 `done` · 25 `pending` · 5 `cancelled` |
| Surface | 103 MCP tools · 12 skills · 6 ADRs · 25 stories |
| **External users** | **0 projects, besides cod-doc itself** |

The last row is the new priority.

## Ground-truth state of plans

| Plan | Status | Note |
|---|---|---|
| [paperclip-adoption](paperclip-adoption-task-plan.md) (RFC 01–15) | ✅ done | 94 done / 2 cancelled |
| [adr-system](adr-system-task-plan.md) | ✅ done | 8/8 |
| [observability-and-indexing](observability-and-indexing-task-plan.md) | ✅ done | 8/8 |
| [refactor-large-files](refactor-large-files-task-plan.md) | ✅ done | confirmed by STB-020 |
| [cod-doc bootstrap](cod-doc-task-plan.md) | ✅ done | 58/58; COD-042/043/052 closed |
| [web-frontend](web-frontend-task-plan.md) | ✅ done | WEB-031/042 closed (STB-003/004) |
| [audit-followups](audit-followups-task-plan.md) | ✅ done | closed by STB-021 |
| [agent-tools-completion](agent-tools-completion-task-plan.md) | ✅ done | closed by STB-001 |
| [stabilization-2026-06](../audit/2026-07-29-state-of-the-project.md) | 🔄 11 done / 1 cancelled | STB-012 → cancelled (re-scoped as ADO-013); STB-023 open |
| **adoption-2026-08** | 🔄 66/82 *(2026-09-02)* | Tracks C+D+E; pilots reassigned to ZAIrgRush and Orakul ([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md)). Sections: C 23/26, D 26/37, E 17/19 |
| RFC 16–21 (hackathon-track) | ❌ rejected 2026-08-29 | ADO-056: none of them covers the M2 demand; notes in [proposals/README.md](../../../proposals/README.md) |
| **RFC 23–24 (M6-track)** | 🟠 DEFERRED | RFC 23 (Cloud agent plane) and RFC 24 (structure/contracts/scenarios) — producer ready, tasks CAP-*/STR-* deferred to M6 |

## Priority shift: why Adoption ahead of features

The 2026-07-29 reconciliation produced four findings that point the same way:

- **F2** — `repo_file`/`repo_symbol` were empty: the in-house capability
  (OBI-030) was never run on the in-house project.
- **F5** — `~/.cod-doc/config.yaml` contains `model: test/model` and a single
  registered project — a temporary pytest directory.
- **F6** — there is no README at the root: the project cannot be explained in
  30 seconds.
- **F7** — **`doc export` corrupts the document** (gluing the preamble with
  the first heading, losing H1, substituting `type`). It never surfaced
  because no one ever walked this path.

F7 is the decisive argument. "Markdown is only a projection, generated
from the DB on export" is the central promise of [VISION §2](../VISION.md),
and it currently does not work. Dogfooding found in an evening what 1356
tests, two LLM reviews, and three audits did not find: they all looked at
the code, they did not use it.

A tool not set up on any real project **is not verified where it breaks**.
Therefore:

> **Decision: Track C (Adoption) is higher priority than Track B
> (hackathon features).**
> **ADO-010 (export) went ahead of both** — export could not be used on
> the pilots while it corrupted documents. Closed 2026-08-25.

## Pilot change: the Symbiosis program (2026-08-25)

By owner decision the pilots were reassigned from "quiet" repositories to
two live ones that produce a data flow
([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md)):

- **ZAIrgRush** (pilot №1, ADO-016) — a multi-agent development loop with a
  free experimental slot E5 "Docs↔code drift"; cod-doc enters there as
  variant C of this experiment.
- **Orakul / ai-review** (pilot №2, ADO-017) — LLM review of PRs without a
  DB; cod-doc becomes the store of findings (dedup, stability) and a
  deterministic drift gate they do not have.

The symbiosis is bidirectional: cod-doc gives specs/ADR/context, the pilots
return findings, commits, and measurements. ADO-003/ADO-004 are closed as
`cancelled` with a reason; STB-012 is closed as `cancelled`
(re-scoped into ADO-013). ADO-010 is split into two stages (a guard now,
byte-identical round-trip — before the first `doc export` outward).
The program decomposition is section E of the `adoption-2026-08` plan
(SYM-001…011).

## Tracks

All tasks are entered in the DB as the plan **`adoption-2026-08`** (as of
2026-09-02 — 82 tasks: C — 26, D — 37, E — 19; the tables below list not the
full composition but the anchor points of the tracks):
`cod-doc plan ready adoption-2026-08 -p cod-doc`.

### Track C — Adoption (priority)

Goal: cod-doc is used on ≥ 2 of the owner's real projects, and feedback
from there steers the backlog. Scenarios by project types —
[adoption-playbook.md](../../adoption-playbook.md).

| ID | Task | Priority | Blocked by |
|---|---|---|---|
| **ADO-001** | Fix `~/.cod-doc/config.yaml`: drop `integration-test`, set a real model and key (F5) | critical | — |
| **ADO-002** | Root `README.md`: what it is, for whom, quick start, a screenshot of the Web UI (F6) | high | — |
| ~~ADO-003~~ | ~~Pilot №1: Mushrooms Shuchin~~ — cancelled, pilot reassigned (RFC 22) | — | — |
| ~~ADO-004~~ | ~~Pilot №2: yana-reconciliation~~ — cancelled, pilot reassigned (RFC 22) | — | — |
| **ADO-016** | Pilot №1: `ZAIrgRush` (agent loop, 28 root docs, slot E5) | high | SYM-001, SYM-002, SYM-004, ADO-015, ADO-010 |
| **ADO-017** | Pilot №2: `Orakul` (371 docs, Diátaxis, ai-review) | high | ADO-016 |
| **ADO-005** | Friction log: a week in the pilots, ≥ 10 observations from live work | high | ADO-016, ADO-017 |
| **ADO-006** | Close the top-3 friction-log findings | high | ADO-005 |
| **ADO-007** | Routine `doc_drift` on the pilots — auto-checks outside cod-doc | medium | ADO-016 |

### Track D — Residual debt (background)

| ID | Task | Priority | Blocked by |
|---|---|---|---|
| ~~**ADO-010**~~ | ~~`doc export` corrupts the document (F7)~~ — **done 2026-08-25**: both stages. Guard (`--dry-run`, refuse to overwrite manual edits and someone else's checkout, `--force-write`) + byte-identical round-trip (71/71 docs in `docs/`), migration `0025_projection_fidelity` | — | — |
| **ADO-011** | Route drift: synchronize `capabilities/web-frontend.md §3` — 66 undocumented (F3) | medium | — |
| **ADO-012** | Put `audit --web-routes` in CI as an advisory step so drift does not accumulate | medium | ADO-011 |
| **ADO-013** | Revisit RFC #21: Tier-1 lost its basis after STB-010 (STB-012 closed `cancelled`, the remainder lives here) | medium | — |
| **ADO-015** | Enum `DocumentType`: + `design/audit/journal/plan/analysis/research/capability/audit-report` — 5 of 6 ZAIrgRush types silently become `module-spec`; silent substitution → warning | medium | — |
| **ADO-014** | Update `capabilities/project-bootstrap.md`: `project new` vs `project add` + `init` | low | — |
| **STB-023** | `activity_subscribe` (SSE). Keep closed until there is a real event-driven scenario | low | — |

Findings of the 2026-09-02 reconciliation (M5 planning) — sprint queue,
contracts in the DB (`task_doc key="contract"`):

| ID | Task | Priority | Blocked by |
|---|---|---|---|
| ~~**ADO-070**~~ | ~~CI on main was never green since 2026-05-06~~ — **done 2026-09-03** (`bcb32f2`): three layers of causes (alembic via `sys.executable -m`, environment hermeticity + 25-min timeout, hardcoded `.venv/bin/python` and FastAPI 0.141 wrappers). [Run 33765619088](https://github.com/Orange-hanter/cod-doc/actions/runs/33765619088) — success, all 7 jobs | — | — |
| ~~**ADO-069**~~ | ~~Red gate locally: v2 case lagged behind the adapter~~ — **done 2026-09-03** (`3de0fd5`): a negative case on a version outside `_KNOWN_VERSIONS` + a positive acceptance test for v2 via API v1 | — | — |
| ~~**ADO-068**~~ | ~~Tests write to the real `~/.cod-doc/config.yaml`~~ — **done 2026-09-03** (`3de0fd5`): `config_dir()`/`config_file()` instead of import-time constants, `adapters.json` respects `COD_DOC_HOME`; the registry cleaned up, `cod-doc` registered | — | — |
| ~~**ADO-066**~~ | ~~`capabilities`/`tool_search`/`tools_diff` fail under a live MCP server~~ — **done 2026-09-03** (`3de0fd5`): tools moved to async; the same defect in `snapshot_tools.py`; an AST gate on `asyncio.run` under `cod_doc/mcp/` | — | — |
| **ADO-067** | `task_update`: description/acceptance/priority are unavailable in MCP and CLI — the agent cannot groom the backlog | high | — |
| **ADO-044** | Mutation provenance: run_id + audit_log + actor_kind — implement or lift the contract (consolidation of ADO-043 + ADO-051) | medium | — |
| ~~ADO-043~~ | ~~audit_log is dead~~ — `cancelled` 2026-09-02, folded into ADO-044 | — | — |
| ~~ADO-051~~ | ~~actor_kind startswith heuristic~~ — `cancelled` 2026-09-02, folded into ADO-044 | — | — |

### Track E — Symbiosis ([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md))

Phase 0 (self-repair, blocks the pilots) → Phases 1–5 (hub, findings, loop,
ai-review, cross-project). Full plan:
`cod-doc plan ready adoption-2026-08 -p cod-doc`.

| ID | Task | Priority | Blocked by |
|---|---|---|---|
| **SYM-001** | B1: `project add/init` creates the DB + fix the onboarding prescript in 3 docs | **critical** | — |
| **SYM-002** | SQLite hardening: WAL + busy_timeout + synchronous; dialect-guard FTS5 | high | — |
| **SYM-003** | Security: bind 127.0.0.1 by default + loopback gate for `POST /settings` | high | — |
| **SYM-004** | `import docs --exclude` | medium | — |
| **SYM-005** | Phase 1: hub.db + migrations 0026/0027 + finding_service | high | SYM-002 |
| **SYM-006** | Phase 1: ingest-adapter registry + CLI ingest/ctx + api/v1 + MCP finding_*/ctx_* | high | SYM-005 |
| **SYM-007** | ADR bridge for ZAIrgRush: 13 ADRs → adr-system + decisions.jsonl | medium | ADO-016 |
| **SYM-008** | Phase 2: ctx docs + a patch to the ZAIrgRush loop (E5 variant C) + git trailers | medium | SYM-006, ADO-016 |
| **SYM-009** | Phase 3: ingest ai_review via a pull model + upstream-PR slimFinding + finding stability | medium | SYM-006 |
| **SYM-010** | Phase 4a: ctx drift → PR comment (gate of links/frontmatter for Orakul) | medium | SYM-009, ADO-017 |
| **SYM-011** | Phase 5: cross-project search + `[[doc:slug:key]]` + fix Chroma L3 + `agent_pick --projects` | low | SYM-005 |
| **STR-001..004** | [RFC 24](../../../proposals/24-structure-contracts-scenarios.md): a structure/contracts/scenarios contour — phases 3–6 (cod-doc side); producer merged into ai-reviewer 2026-09-03 | medium | — (SYM-005..009 done) |

### Track B — Feature track (hackathon RFC, after C)

❌ **Rejected entirely 2026-08-29 (ADO-056, M3 kickoff).** Reconciliation of
the "Current state" sections of RFC 16–21 against the code + comparison
with the M2 friction log (ADO-005, entries #8/#10/#11/#14) showed: none of
the RFCs covers the recorded demand. Notes are in
[proposals/README.md](../../../proposals/README.md). The B-MVP/B-Scale
order below is historical context.

## Three nearest milestones

> **Active sprint — M5 "A gate you can trust + symbiosis in the field":**
> [sprint-m5-trustworthy-gate.md](sprint-m5-trustworthy-gate.md) — a queue
> without dates: 1) ADO-070 CI not green once since 2026-05-06, 2) ADO-069 the
> only red test locally, 3) ADO-068 tests write to the real config (regression
> F5), 4) ADO-066 `capabilities`/`tool_search`/`tools_diff` fail under a live
> server, 5) ADO-067 `task_update` in MCP/CLI, 6) ADO-065 E5-C analysis,
> 7) SYM-010 Orakul drift gate, 8) ADO-044 mutation provenance. The contract
> of each task is in the DB, `task_doc key="contract"`.
> **M4 "Proof of value" closed 2026-09-02:**
> [sprint-m4-proof-of-value.md](sprint-m4-proof-of-value.md) — audit
> [2026-09-02-sprint-m4-proof-of-value.md](../audit/2026-09-02-sprint-m4-proof-of-value.md);
> all four queue items done (ADO-062/064, ADO-063, ADO-040, SYM-009),
> E5-C proven by a field run for $0.98 with the verdict "scale up".
> **M3 "friction-log leftovers" closed early 2026-08-30:**
> [sprint-2026-08-30-m3-friction-log.md](sprint-2026-08-30-m3-friction-log.md)
> — audit [2026-09-06-sprint-m3-friction.md](../audit/2026-09-06-sprint-m3-friction.md);
> the friction log reset to zero (#8/#10/#11/#14 → ADO-058…061), the stretches
> ADO-039 and SYM-008 done.
> Sprint H1 closed early 2026-08-29:
> [sprint-2026-08-29-hardening-m3-kickoff.md](sprint-2026-08-29-hardening-m3-kickoff.md)
> — audit [2026-09-05-sprint-h1-hardening.md](../audit/2026-09-05-sprint-h1-hardening.md).
> M2 closed early 2026-08-29: [sprint-2026-08-28-m2-feedback-loop.md](sprint-2026-08-28-m2-feedback-loop.md)
> — audit [2026-09-11-sprint-m2-feedback-loop.md](../audit/2026-09-11-sprint-m2-feedback-loop.md).
> Previous sprints:
> [sprint-2026-08-27-m1-phase1.md](sprint-2026-08-27-m1-phase1.md)
> (closed early 2026-08-28, audit in `docs/system/audit/`).

### M1 — "Pilot works" *(SYM-001..004, ADO-010 stage 1, 001, 002, 015, 016, 017; ~2–3 weeks)*

Cod-doc is set up on two real projects and gives context, and export does
not corrupt documents.

**Done, when:**
- [x] **ADO-010 closed entirely** (2026-08-25, not in two passes): guard (`--dry-run`, `--force-write`, refuse to overwrite manual edits and someone else's checkout) **and** byte-identical round-trip — 71/71 docs in `docs/` (migration `0025_projection_fidelity`).
- [x] **SYM-001 closed:** `project add/init` creates the DB — onboarding works per the docs.
- [x] `cod-doc project list` does not contain `integration-test`; the config points at a working model *(ADO-001, 2026-08-25)*.
- [x] There is a `README.md` at the root explaining the project without reading `docs/` (2026-08-25).
- [x] Two projects pass all 5 "project set up" criteria from the `project-onboarding` skill — ZAIrgRush (ADO-016, 2026-08-28) and Orakul (ADO-017, 2026-08-28).
- [x] `cod-doc search` on each pilot finds documents by a domain term ("swarm" in ZAIrgRush, "pulse" → 18 hits in Orakul; both — after `--reindex`, friction #5).

**Risk:** the import pulls in archive/vendor markdown → noise in FTS.
**Mitigation:** `--dry-run` is mandatory; the decision on the `Archive/` directory is made before the import (fixed in the skill and the playbook).

**Why ADO-010 first:** the pilots are foreign working repositories. Putting
a tool that can corrupt markdown there is not allowed.

### M2 — "Feedback embedded" *(ADO-005..007, 011, 012; ~3 weeks)*

The pilots are lived through, what was found is fixed, drift does not
accumulate.

**Done, when:**
- [x] There is a friction log ≥ 10 observations from real work, not from reading code. *(ADO-005, 2026-08-28: 14 entries)*
- [x] The top-3 findings are closed by tasks in the DB, not by notes. *(ADO-006, 2026-08-28: top-1 ADO-023 + ADO-030/031/032/033)*
- [x] `capabilities/web-frontend.md §3` matches the live routes; `audit --web-routes` in CI. *(ADO-011 `635b004`, ADO-012 `cf328e7`, advisory-job)*
- [x] The `doc_drift` routine runs on the pilot on schedule. *(ADO-007/ADO-024: OS-cron `*/15` tick, a real cron-fire confirmed)*
- [x] An audit report is written from the pilot results (skill `audit-cadence`). *([2026-09-11-sprint-m2-feedback-loop.md](../audit/2026-09-11-sprint-m2-feedback-loop.md), 2026-08-29)*

**Risk:** the pilots are "set up and abandoned" — tracking moves back into the head.
**Mitigation:** M2 is not closed without a friction log from live work.

### M3 — "First feature by demand" *(friction-log leftovers of M2; ~2–3 weeks)*

One RFC from the hackathon track is implemented — **chosen by the M2
friction log**, not by the order from README. *(2026-08-29: no RFC chosen —
see the resolution; M3 retargeted by owner decision.)*

> **Kickoff resolution 2026-08-29 (ADO-056): "all rejected".** An analysis
> of RFC 16–21 against the M2 friction log (ADO-005 #8/#10/#11/#14) and the
> findings of the contract audit (2026-08-29-contract-audit.md) found no RFC
> that covers the recorded demand; the "Current state" sections of all six
> were re-verified against the code 2026-08-29 (details — task-doc 'acceptance'
> in ADO-056). Rejection notes are in `proposals/README.md` (ADO-013 closed).
> `plan_create` for Track B is not run.
>
> **Owner decision 2026-08-29: M3 retargeted to the friction-log leftovers
> of M2** — #8 dry-run limit of 50 lines, #10 broken path for `*.txt`, #11
> undocumented skip of hidden directories, #14 mapping of `diataxis`/`quadrant`
> to DocumentType. This is confirmed demand instead of the hackathon track.

**Done, when:**
- [x] The RFC choice is justified by links to specific M2 observations. *(outcome: "all rejected", justification via ADO-005 #8/#10/#11/#14 + contract-audit)*
- [x] The "Current state" section of the chosen RFC is re-verified against the code. *(none chosen; all six reconciled, 2026-08-29)*
- [x] The friction-log leftovers of M2 (#8/#10/#11/#14) are entered as tasks and closed. *(ADO-058…061, 2026-08-30; friction log reset to zero)*
- [x] Each fix is verified on the Orakul pilot corpus (405 docs), not only in tests. *(ADO-062, 2026-08-30: orakul re-registered; dry-run `--limit 0` — 53 candidates in a full list, hidden-dirs counter "(4): .claude, .cursor, .github, .graphify", foreign `type:` produce a warning. Side finding: 405 stale_export — Orakul projections were not re-exported after migrations, a separate owner decision.)*
- [x] The remaining RFC 16–20 are revisited: stale ones are marked in `proposals/README.md`. *(16–21, 2026-08-29)*

**Risk:** the temptation to start with RFC 18 just because it is first in the list.
**Mitigation:** the first checkpoint is a justification of the choice by M2 data.

### M4 — "Proof of value + debt triage" *(queue without dates)*

The infrastructure is ready — the demand has to be confirmed by an artifact,
not by tests. *(2026-08-30: owner decision — code is written by an AI agent,
so windows/deadlines are not planned; a sprint is an ordered queue with an
exit criterion.)*

**Queue (order = priority):**
1. Re-register Orakul + verify the M3 fixes on the corpus of 405 docs
   (ADO-062, high) — closes the open M3 checkbox.
2. E5-C in the field: `doc_context=executor` on a real ZAIrgRush task +
   .py→doc-path mapping + measurement per RFC 22 §3.4 + a decision
   "scale up/turn off" by an artifact (ADO-063, critical).
3. ADO-040 — a unified write-path wrapper + emit in all write services
   (high, the only high of section D).
4. SYM-009 — ingest ai_review via a pull model + upstream-PR slimFinding
   (medium; external risk — a PR into ai-reviewer).

**Done, when:**
- [x] Orakul is registered; the M3 checkbox "verified on the Orakul corpus" is closed. *(ADO-062, 405/405 in_sync)*
- [x] The E5-C decision is recorded as an artifact (ADR/findings + metrics). *(ADO-063: verdict "scale up", $0.98, s5dc done in 1 iteration)*
- [x] ADO-040 done: write-path wrapper + 9 services emit; an emit error is visible. *(`3b2662b`)*
- [x] M4 audit report (active, in the DB), ROADMAP updated. *([2026-09-02-sprint-m4-proof-of-value.md](../audit/2026-09-02-sprint-m4-proof-of-value.md))*

**Risk:** E5-C shows empty blocks without a path mapping; the upstream PR
can stall (SYM-009 is moved, the sprint is not blocked). *(Did not happen:
the path mapping is included in ADO-063, PR ai-reviewer#5 opened.)*

### M5 — "A gate you can trust + symbiosis in the field" *(queue without dates)*

The sprint is defined not by the backlog remainder but by six findings of the
2026-09-02 reconciliation (F1–F6 of the M4 audit report). The common story:
**the test environment and production diverged in both directions** — tests
do not see production defects and at the same time write to production state,
and the gate that was supposed to catch this does not work and is not checked.

**Sprint closed 2026-09-06** — [audit report](../audit/2026-09-06-sprint-m5-trustworthy-gate.md).
The queue was walked through completely; RFC 24 (`0044324`) and ADO-077
(`a815866`) entered outside the queue. Seven tasks with measurements were
created: ADO-071..075, ADO-077, STR-000.

**Queue (order = priority).** Items 1–4 closed 2026-09-03
(`3de0fd5`, `087f00a`, `bcb32f2`), 5–8 — 2026-09-05/06.
1. ✅ ADO-070 (critical) — CI on main was never green since 2026-05-06
   (`alembic` not on PATH); the "gates green" item in the DoD of M1…M4 was
   checked only by a local run.
2. ✅ ADO-069 (critical) — the only red test locally: the v2 case lagged
   behind the adapter that arrived in the same commit (SYM-009). Together
   with item 1 gives the first green run.
3. ✅ ADO-068 (critical) — tests write to the real `~/.cod-doc/config.yaml`;
   `CONFIG_DIR` frozen at import; regression of F5 (ADO-001 lived 5 days).
4. ✅ ADO-066 (critical) — `capabilities`/`tool_search`/`tools_diff` fail
   under a live MCP server; the tests call them synchronously and so do not
   see it.
5. ✅ ADO-067 (high) — `task_update` in MCP and CLI (`1dc97d4`); the
   anti-drift on surface parity is built on AST detection, not on a manual list.
6. ✅ ADO-065 (high) — E5-C analysis (`8b71ee5`): the tester took 67.8%
   wall-time, not the declared ~60%; F1–F5 resolved.
7. ✅ SYM-010 (medium) — a drift gate in the Orakul PR (`a815866`); closed
   with a caveat on acceptance №4, a live run surfaced ADO-077.
8. ✅ ADO-044 (medium) — mutation provenance, ADR-012: `audit_log` and
   `run_id` lifted, `actor_kind` reduced to a single point of derivation.

**Done, when:**
- [x] A green CI run on main — with a link to the run id (main criterion). *(ADO-070, `bcb32f2`: [run 33765619088](https://github.com/Orange-hanter/cod-doc/actions/runs/33765619088) — success, all 7 jobs, including `Docker build`, `skipped` for months)*
- [x] `capabilities` responds through a live MCP server. *(ADO-066, `3de0fd5`: tools moved to async; an AST gate on `asyncio.run` under `cod_doc/mcp/`)*
- [x] A test run does not change the real `~/.cod-doc/config.yaml`; `cod-doc` is registered. *(ADO-068, `3de0fd5`: `config_dir()`/`config_file()` instead of import-time constants; mtime after a full run did not change)*
- [x] Backlog grooming is doable via MCP and CLI without scripts in the service layer. *(ADO-067, `1dc97d4`; applied in the field to ADO-075 and ADO-077)*
- [x] E5-C analysis — a table by role, findings F1–F5 resolved. *(ADO-065, `8b71ee5`: [report](../audit/2026-09-05-e5c-run-analysis.md))*
- [x] M5 audit report (active, in the DB), ROADMAP updated. *([report](../audit/2026-09-06-sprint-m5-trustworthy-gate.md))*

**Risk:** a green CI will reveal a layer of defects invisible locally (CI
installs ruff without a pin). This is not a reason to postpone — this is
exactly what the gate is supposed to catch.

## M6 — "Hub + cross-project" *(planned after Track C/E closes)*

**Goals of M6:**
1. Cross-project search via the hub DB (`[[doc:slug:key]]`) — SYM-011.
2. Fix the ChromaDB L3 mode for multi-project.
3. Extend `agent_pick --projects` to work with several projects.
4. Launch **RFC 23** (Cloud decentralized agent plane) — tasks CAP-001…CAP-033.
5. Launch **RFC 24** (a unified structure/contracts/scenarios contour) — tasks STR-001…STR-004.

**Status:** SYM-011 is open (low priority); RFC 23 and RFC 24 are designed
(status 🟠 DEFERRED), the producer for RFC 24 is ready (phases 1–2 in
ai-reviewer), the prerequisite SYM-005..009 is done. Tasks CAP-*/STR-* are
not started, awaiting prioritization in the M6 plan.

**Done, when:**
- [ ] SYM-011 closed: cross-project search works via the hub DB.
- [ ] ChromaDB L3 mode fixed for multi-project.
- [ ] `agent_pick --projects` supports working with several projects.
- [ ] RFC 23: the plan CAP-001…CAP-033 is launched (cloud agent plane).
- [ ] RFC 24: the plan STR-001…STR-004 is launched (structure/contracts/scenarios).

**Risk:** a premature start of M6 before adoption (Track C/E) closes will
diffuse focus. **Mitigation:** M6 does not start until SYM-010 closes and
the ZAIrgRush/Orakul pilots are verified.

## Execution order

1. **Phase 0** (SYM-001..004 + ADO-001/010-stage-1/015) → cod-doc is safe for a foreign repository.
2. **M1** → ZAIrgRush and Orakul are set up (ADO-016/017), nothing extra is written to their trees.
3. **Phases 1–4** (SYM-005..010) → hub, findings, the E5-C loop, the drift gate; in parallel M2 (friction log).
4. **M3 / Phase 5** → cross-project and a feature chosen by demand.

Track D runs in the background; ADO-011/012 are tied to M2, ADO-013 — to M3,
ADO-014/015 are opportunistic (ADO-015 is convenient to close together with
ADO-010 — a common root cause).

## Reconciliation history

- **2026-06-04** — [self-improvement audit](../audit/2026-06-04-self-improvement-compared.md): a double LLM review, P0/P1/P2 backlog, RFC #21.
- **2026-06-05** — [a three-way reconciliation](../audit/2026-06-05-doc-drift-source-of-truth.md) DB↔markdown↔code; Track A is entered in the DB (`stabilization-2026-06`).
- **2026-07-29** — [state-of-the-project audit](../audit/2026-07-29-state-of-the-project.md): Track A closed 12/13; 3 skills extracted (9 → 12); F7 found (`doc export` corrupts the document); priority shifted to adoption; the plan `adoption-2026-08` is entered (13 tasks); this roadmap is rebuilt.
- **2026-08-25** — the Symbiosis program ([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md)): pilots reassigned to ZAIrgRush/Orakul (ADO-003/004 → cancelled, ADO-016/017); section E (SYM-001…011); STB-012 → cancelled (re-scoped into ADO-013); ADO-010 split into 2 stages; ADO-015 extended for the pilot types.
- **2026-08-27** — the sprint [sprint-2026-08-27-m1-phase1.md](sprint-2026-08-27-m1-phase1.md) is entered (M1 + Phase 1): SYM-005/006 decomposed into SYM-005A–D / SYM-006A–D (section E, contracts in the DB); ground-truth divergences found — migration 0026 on the unmerged branch `worktree-swarm-ado022-ado015-sym004` (Phase 1 numbers shifted to 0027/0028), ai-reviewer lives in `/Users/dakh/Git/_my/ai-reviewer`, not under Mozarella.
- **2026-08-28** — **M1 "Pilot works" closed.** ADO-016 (ZAIrgRush, 31 docs) and ADO-017 (Orakul, 405 docs) → done; SYM-003 (bind-hygiene + loopback gate) → done (ae5911e); the branch `worktree-swarm-ado022-ado015-sym004` is merged (1a66aaa — ADO-015 types, SYM-004 `--exclude`, ADO-022 fidelity, migration 0026). Friction log ADO-005: 14 entries (≥10 for M2). Caveats: ADR ZAIrgRush without types → SYM-007; stale_export after import (friction #7/#12) — a candidate for M2.
- **2026-08-28 (2)** — the sprint [sprint-2026-08-28-m2-feedback-loop.md](sprint-2026-08-28-m2-feedback-loop.md) is entered (M2): friction slots ADO-030…033 (log entries #5/#6/#9/#13, owner decision — all four), audit tails F1/F2/F4 (ADO-027/028/029), route drift ADO-011/012, stretch SYM-007.
- **2026-08-29** — **M2 "Feedback embedded" closed early** ([audit](../audit/2026-09-11-sprint-m2-feedback-loop.md)): all goals + the stretch SYM-007 (13/13 ADR ZAIrgRush, 2 supersede). Commits `17b7137`…`cf328e7`. Suite 1562 passed, drift 123/123. The friction-log remainder (#8/#10/#11/#14) → backlog, prioritization at the M3 planning.
- **2026-08-29 (2)** — **M3 kickoff (ADO-056): Track B rejected entirely.** Reconciliation of RFC 16–21 against the code + M2 friction log (#8/#10/#11/#14) + contract audit: the demand is not covered by any RFC → decision "all rejected", notes in `proposals/README.md` (ADO-013 closed). Owner decision: M3 retargeted to the M2 friction-log leftovers (#8/#10/#11/#14). In parallel sprint H1: hardening per the contract audit (ADO-035/036/037/038/052/053/055 done).
- **2026-08-30** — **M3 "friction-log leftovers" closed early** (same day as the start; [audit](../audit/2026-09-06-sprint-m3-friction.md)): #8/#10/#11/#14 → ADO-058…061, the friction log ADO-005 reset to zero (0 open of 14); the stretches ADO-039 (enforce atomic checkout, `a2f3cfb`) and SYM-008 + ADO-057 (`cod-doc ctx docs|drift|search --json`, `0282835`; the E5-C loop in ZAIrgRush, `4110cc6`) are done. Drift 130/130 (also the previously untracked `sprint-2026-08-27-m1-phase1.md` is registered), ratchet 6 entries without growth. Open M3 checkbox: verifying the fixes on the Orakul corpus — the project is not registered in this environment; the fixes are verified on the live ZAIrgRush corpus (dry-run shows warnings for unknown `type:`).
- **2026-09-02** — **M4 "Proof of value" closed** ([audit](../audit/2026-09-02-sprint-m4-proof-of-value.md)): all four queue items (ADO-062/064 Orakul 405/405, ADO-063 E5-C `7bc156d` — verdict "scale up" for $0.98, ADO-040 write-path `3b2662b`, SYM-009 ingest ai_review `2ac0631`). **The main finding of the closure: CI on main was never green since 2026-05-06** — 10 runs out of 10 failure, cause `FileNotFoundError: 'alembic'` in fixtures; meaning the "gates green" item in the DoD of M1…M4 was checked only by a local run. Five more reconciliation findings: the only red test locally (v2 case lagged behind SYM-009), three MCP tools fail under a live server (`asyncio.run` in a running loop), tests write to the real `~/.cod-doc/config.yaml` (regression F5), backlog grooming is unavailable to the agent (`task_update` only in web), three declared contracts without implementation (run_id 2004/2004 NULL, audit_log 0 rows / 0 writers). ADO-066…070 are entered; ADO-043 and ADO-051 are folded into ADO-044. The sprint [M5 "A gate you can trust"](sprint-m5-trustworthy-gate.md) is entered with task contracts in the DB (new task_doc key — `contract`).
- **2026-09-03** — **M5: queue items 1–4 closed, the project has a green CI for the first time.** [Run 33765619088](https://github.com/Orange-hanter/cod-doc/actions/runs/33765619088) — `conclusion: success`, all 7 jobs, including `Docker build`, `skipped` for months (main history before this: 10 failure + 2 cancelled, zero green). ADO-070 (`bcb32f2`), ADO-069/ADO-066/ADO-068 (`3de0fd5`) → done; evidence "red before / green after" — in the task_doc `verification` of each task. The gate was treated in three layers, and each next one was invisible while the previous held: (1) `alembic` — 24 places called it via `.venv/bin` with env substitution to `PATH=/usr/bin:/bin`, i.e. the binary was never found → a single `tests/_alembic.py` on `sys.executable -m alembic`; (2) the run was not hermetic (the workflow itself sets `COD_DOC_API_KEY`, and `Config` reads env by the `COD_DOC_` prefix) and `timeout-minutes: 10` killed the job at 64% of tests before printing tracebacks; (3) hardcoded `.venv/bin/python` in three tests + FastAPI 0.141 wrapped routes in `_IncludedRouter` without `.path`, so `_real_web_routes()` silently returned an empty set — **the advisory job "Web routes drift" was green in vain**, a false green over a red gate. Also closed the gate divergence: `ruff` pinned `>=0.16.5,<0.17` (CI installed the latest and brought 10×RUF036), `mypy python_version=3.12` (under 3.11 it crashed on numpy 2.5 stubs and aborted the check entirely). The run is 1639 passed locally and in the CI repro environment. The remainder of the M5 queue: ADO-067, ADO-065, SYM-010, ADO-044.
- **2026-09-07** — **recovery after the autonomous daemon.** The cod-doc daemon (`agent_enabled=true`, 60 s tick) ran 104 self-generated tasks over MASTER.md in four repositories in a day and committed the result under the owner's signature. In this file commits `fa47278`…`0522c0f` removed ~200 lines: "Three nearest milestones" with M1–M5, "Execution order", "Reconciliation history"; in the root `MASTER.md` — §4 Quick Actions & Handoffs, §5 Validation & Changelog (registry of 11 documents) and the Snowball Protocol. The sections are restored from `fa47278~1`; useful additions of the daemon (section M6, the RFC 23–24 row, links to proposals 23/24) are kept. Dates from the future (2026-09-12/13 in MASTER, 2026-09-14 in the working copy) are removed — the daemon took the model cutoff for the current date. The daemon is disabled.
