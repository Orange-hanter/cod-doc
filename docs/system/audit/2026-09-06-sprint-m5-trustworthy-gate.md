---
type: audit-report
scope: sprint-m5-trustworthy-gate
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-06
last_updated: 2026-09-06
audience: [contributors, agents]
related_docs:
  - ../roadmap/sprint-m5-trustworthy-gate.md
  - ../roadmap/ROADMAP.md
  - 2026-09-02-sprint-m4-proof-of-value.md
  - 2026-09-05-e5c-run-analysis.md
---

# Audit — sprint M5 "A gate you can trust + symbiosis in combat"

## 1. Outcome

The queue of eight tasks is closed in full. The main exit criterion —
**a green CI run on main** — is met on 2026-09-03 and has held since:
seven green runs in a row, including the `Docker build` job that had been
`skipped` for months.

| # | Task | Priority | Commit |
|---|---|---|---|
| 1 | ADO-070 — CI as a gate | critical | `bcb32f2` |
| 2 | ADO-069 — the red test of the v2 case | critical | `3de0fd5` |
| 3 | ADO-068 — tests write to the combat config | critical | `3de0fd5` |
| 4 | ADO-066 — async tools under a live server | critical | `3de0fd5` |
| 5 | ADO-067 — `task_update` on MCP and CLI | high | `1dc97d4` |
| 6 | ADO-065 — review of the E5-C run | high | `8b71ee5` |
| 7 | SYM-010 — a drift gate in the Orakul PR | medium | `a815866` |
| 8 | ADO-044 — mutation provenance (ADR-012) | medium | PR #17 |

Two documents arrived into the sprint out of queue: **RFC 24** (`0044324`) —
the structure/contracts/scenarios outline as an accepted decision, and **ADO-077**
(`a815866`) — a defect found by the live check of SYM-010.

## 2. The sprint story: three mechanisms that looked working

M5 was planned around one defect of this kind — ADO-070, where CI
was not green a single time since 2026-05-06, while the DoD of sprints M1…M4 said
"gates green" by a laptop run. The sprint found three more of the same class.

**`hooks/post-merge` (ADO-075).** The hook gates the hash registry update with
the condition `command -v cod-doc`, but the binary is only in `.venv/bin`. On every
merge "cod-doc not found — hashes not updated" is printed and the hook exits.
Discovered when closing ADO-067: a manual `hash update` recomputed four
entries, of which the task touched one. The hashes of `ci.yml` and `README.md`
had gone stale earlier — on ADO-070 and on the README rewrite, — and no one noticed,
because the warning reads as routine noise.

**`test_activity_write_path.py`.** The anti-drift test guarding the ADO-040 rule
"every write service emits an activity event" enumerates the services **manually**.
It does not see a new write service. Discovered during the audit of draft-PR #6: nine
mutating functions without a single event would have passed the gate silently. The ADO-067
test (`test_task_mutation_surface_parity.py`) is already built on AST-based detection
and verified experimentally: an injected unexposed mutation drops the run
with a pointer to both missing surfaces.

**Provenance contracts (ADO-044).** `audit_log` — 0 rows and 0 writers in all
history while declared in `ARCHITECTURE` §9 and `DATA_MODEL` §3.13. `run_id` —
2166 out of 2166 revision and 1114 out of 1114 activity_event with NULL under the
`AGENTS.md` §5.4 rule "run-id on all mutations".

What all four have in common: **the mechanism is declared, looks working, is not
executed**, and the signal of its failure is indistinguishable from noise. That is exactly the
disease for which M5 was started — and it turned out to be systemic, not
isolated.

## 3. What the re-verification changed in the facts

The sprint refuted its own initial data three times — that is a result, not
an overhead.

| Statement | Verification |
|---|---|
| The E5-C tester ate ~60% wall-time | **67.8%**: 826.0 s out of 1218.0 s per `metrics.jsonl` and `run.jsonl:37-44` |
| `run_id` is dead code | **Wrong.** `run_scope()` has no production calls, but `start_orchestrator_run` is live (`orchestrator.py:151`), `get_current_run_id()` is read by five services. What is dead is not the mechanism, but the promise |
| `run_id` is declared in `DATA_MODEL` | `DATA_MODEL` did not describe it at all; the only declaration is `AGENTS.md` §5.4 |
| The `actor_kind` heuristic duplicate is one | **Eleven** call sites in three variants; the value dictionary diverged from migration 0012 |
| `run_id` in three tables | In **seven**: plus `approval`, `finding`, `routine_run` |

From the last point grew a finding in the data: 58 events of the
`orchestrator-run-kimi-sprint-20260828` run lie with `actor_kind='human'`, and
27 events of the same run — with `'orchestrator'`; four events of the author
`agent:claude-opus-5` are marked `human`. A backfill was consciously not done:
`activity_event` is a journal of observations, to rewrite it retroactively means
that the timeline cannot be trusted.

## 4. Symbiosis in combat

SYM-010 for the first time brought cod-doc into **a foreign repository for writing**. A live run
on `Orange-hanter/Orakul#562`: comment
[5554494333](https://github.com/Orange-hanter/Orakul/pull/562#issuecomment-5554494333),
the second run reused the same id and **did not change `updated_at`** — the body
matched byte-for-byte, there was no write at all. The Orakul working tree is untouched.

The very first live run uncovered ADO-077: a bare mention of `ADR-001` in prose
was marked `LINK-BROKEN` severity major, because the project has zero registered
ADRs. The comment with a false finding was not published. For a mechanism
that sells the thesis "not the model's opinion, but a verifiable fact", one false
finding in a foreign PR is more expensive than ten missed real ones.

**Acceptance №4 of SYM-010 is closed with a caveat.** All four open Orakul PRs
give zero findings: the documents they touch are in order. The project does have
real problems — 22 documents out of 405 are in the `edited_in_place` state, —
but no open PR touches them. The ability to catch the real thing is shown
outside PRs. The caveat is closed by the first PR that touches a drifting document;
it does not require separate work.

## 5. Opened in the sprint

Seven tasks, each with a measurement, not with a "seems broken" formulation:

| ID | What | Priority | From |
|---|---|---|---|
| ADO-071 | the kimi shoulder does not write cost/tokens and turn timestamps | high | ADO-065, F3 |
| ADO-072 | a hardcode `project='zairgrush'` in promptbuilder | medium | ADO-065, F2 |
| ADO-073 | `cod_doc_bin` outside `KNOWN_CONFIG_KEYS` | low | ADO-065, F1 |
| ADO-074 | the model config: tester back to sonnet | medium | ADO-065 |
| ADO-075 | `hooks/post-merge` does not find `cod-doc` in PATH | medium | ADO-067 |
| ADO-077 | false ADR findings in projects without a registry | high | SYM-010 (closed) |
| STR-000 | RFC 24 in main | medium | out of queue (closed) |

Findings F4 and F5 of the E5-C run are closed with a reason, not carried over: F4 —
the budget semantics is a design decision of ZAIrgRush; F5 is subordinate to F3,
to explain 826 s timestamps are needed, not text of reasoning.

## 6. Exit criterion

| # | Criterion | Status |
|---|---|---|
| 1 | A green CI run on main with a link to the run id | ✅ [33765619088](https://github.com/Orange-hanter/cod-doc/actions/runs/33765619088), then seven in a row |
| 2 | `capabilities` answers through a live MCP server | ✅ ADO-066 |
| 3 | The real `~/.cod-doc/config.yaml` is not changed by a run | ✅ ADO-068 |
| 4 | Backlog grooming via MCP and CLI without scripts | ✅ ADO-067; applied in combat to ADO-075 and ADO-077 |
| 5 | A review of E5-C with a table by role, F1–F5 separated | ✅ [report](2026-09-05-e5c-run-analysis.md) |
| 6 | The M5 audit report, ROADMAP updated | ✅ this document |

## 7. Definition of Done

- [x] Each queue task went through `task_checkout` → `task_complete` with `commit_sha`.
- [x] A green CI run on main — a run id, not a local run.
- [x] Edits of tracked `.md` are imported; the final `doc drift --all` — **136 in_sync, 0 divergences**.
- [x] `ruff` + `ruff format` + `mypy` strict + `pytest` green; the ratchet did not grow.
- [x] The M5 audit report — status active, in the DB.

A caveat on the item "every bug is closed with an attached red run BEFORE the fix":
for ADO-077 the role of the red run was played by the live launch of the gate on the Orakul PR,
which gave a false finding; for ADO-075 — by a manual `hash update` that recomputed
four entries instead of one.

## 8. Side results

- **OpenRouter economics.** From the per-generation `cost_details` it is derived that
  the Anthropic-skin at OpenRouter is cheaper than first-party **exactly on cache-write**:
  $2.50 vs $4.00 per 1M, with the same cache-read ($0.20) and output ($10.00).
  For a run where 57.6% of the reviewer's bill went to a one-time dump
  of a 51 605-token prompt into the cache, this is a direct saving.
- **ADR-011 is superseded.** Found while working on ADO-044: it was accepted before implementation and
  referenced a non-existent migration number and paragraph. Replaced by ADR-012 via the standard
  `adr_supersede`.
- **Section F of the plan** is opened under RFC 24; the cod-doc side (phases 3–6)
  is decomposed into STR-001..004 from draft-PR #6.

## 9. What remained open

- **draft-PR #6** (structure platform, +5621) — not merged. The audit found two
  gate blockers and five violations of the law that the gate does not catch. It is split by
  phases of RFC 24 into STR-001..004.
- **ADO-071..075** — opened, not started.
- **SYM-011** — cross-projectness, on demand.
- **An inaccuracy in ADR-012**: the profile counters are given as 108→105 / 112→109,
  the actual ones — 109→106 / 113→110. The delta is correct, the base is off by
  one due to SYM-010, which arrived between the decision and the reconciliation. The fix
  is impossible: the body of an accepted-ADR is frozen by the system. The code, docs and tests
  contain the correct numbers.
