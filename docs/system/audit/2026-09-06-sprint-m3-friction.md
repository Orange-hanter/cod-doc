---
type: audit-report
scope: sprint-m3-friction-log
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-30
last_updated: 2026-08-30
related_docs:
  - ../roadmap/ROADMAP.md
  - ../roadmap/sprint-2026-08-30-m3-friction-log.md
  - 2026-09-11-sprint-m2-feedback-loop.md
audience: [contributors, agents]
---

# Audit — Sprint 2026-08-30 → 2026-09-06 "M3: friction-log leftovers"

> **Context.** The third sprint per [ROADMAP.md](../roadmap/ROADMAP.md) (plan
> `adoption-2026-08`). Goal — close the remainder of the friction log ADO-005:
> entries **#8 / #10 / #11 / #14** (findings F1–F4 of the M2 audit). Stretch:
> ADO-039 (enforce atomic checkout) and SYM-008 (the E5-C loop in ZAIrgRush).
> Sprint plan:
> [sprint-2026-08-30-m3-friction-log.md](../roadmap/sprint-2026-08-30-m3-friction-log.md).

## TL;DR

The sprint is closed early (2026-08-30, the same day as the start): all three goals
and both stretches are done, the friction log is zeroed (0 open entries out of 14).
7 commits to main cod-doc + 1 commit to ZAIrgRush. Engineering health
is green: 1601 tests passed, ruff/mypy/drift clean (130/130).

## 1. Deliverables

### G1 — friction #10 (ADO-058, bug)

| Task | Commit | Content |
|---|---|---|
| ADO-058 | `ee51e7d` | `path` is propagated through `import_markdown`/`import_or_update_markdown` → `docs.create`: imported `.txt/.rst/.markdown` get the real path, drift no longer shows them as `missing`. Backfill was not needed (drift was clean). Red run — in the task-doc 'verification' |

### G2 — friction #8 (ADO-059, UX)

| Task | Commit | Content |
|---|---|---|
| ADO-059 | `b98b707` | `cod-doc import docs --dry-run --limit N` (0 = the full list, default 50); a tail hint "… more K (full list: --limit 0)"; tests on a corpus of >50 files |

### G3 — friction #14 + #11 (ADO-060, ADO-061)

| Task | Commit | Content |
|---|---|---|
| ADO-060 | `523d2ba` | Mapping of foreign frontmatter: `_DIATAXIS_TYPE_ALIASES` (tutorial/how-to→guide, explanation→analysis, reference→module-spec) + fallback keys `diataxis`/`quadrant`; only the create path, update preserves the type. Red run — in the task-doc 'verification' |
| ADO-061 | `5cbb57d` | The walker collects missed hidden-dirs; dry-run prints "Hidden directories skipped (N): …"; the `project-onboarding` skill is synchronized |

### Stretch — ADO-039 (enforce atomic checkout)

| Task | Commit | Content |
|---|---|---|
| ADO-039 | `a2f3cfb` | Phase-2 enforce is on: `update_status` raises `StatusTransitionError` on todo→in_progress without `via_checkout=True` regardless of the strict mode; the web fragment goes through `checkout_service.checkout("human:web")`; the CLI `task status` catches the error; 16 places in 12 test files are moved to checkout; AGENTS.md §5.3 is in sync |

### Stretch — SYM-008 (E5-C loop) + ADO-057

| Task | Commit | Content |
|---|---|---|
| SYM-008 / ADO-057 | `0282835` (cod-doc), `4110cc6` (ZAIrgRush) | cod-doc: CLI `cod-doc ctx docs\|drift\|search --json` — read-only (`commit=False`), contract `{docs, links_at_risk, token_estimate}`, 7 cli tests. ZAIrgRush: `swarm/docctx.py` (subprocess + guard), `promptbuilder.docs_block` (per-task cache, byte stability when off), `handoff(docs=)`, `[experiments] doc_context = off\|executor\|reviewer\|all`, trailers `Swarm-Task:`/`Cod-Doc-Task:`; `tools/swarm/check.sh` is green (1219 passed) |

Deviations of SYM-008 from RFC 22 §3.4: the document block is mixed in only
into the executor handoff (the reviewer is not run); the `Cod-Doc-Task:` trailer —
a deterministic stub `ZRG-{id}` (there is no mirrored-task field in tasks.json
yet).

The sprint formalization — commit `9b596a6` (kickoff doc, tasks ADO-058…061,
a pointer in ROADMAP).

## 2. Engineering health (at the end of the sprint)

| Check | Result |
|---|---|
| `pytest tests/` | 1601 passed |
| `ruff check` / `format --check` | clean |
| `mypy cod_doc/` | clean (318 files) |
| `doc drift -p cod-doc --all` | 130/130 in_sync |
| Ratchet `per-file-ignores` | 6 entries (base H1), no growth |

## 3. Findings

- **F1 (low, backlog).** `ctx docs --paths` filters by the `path`/`doc_key`
  of documents, not by code files: for a sample of "documents about tools/swarm"
  a glob over md paths is needed. When used in ZAIrgRush this means that
  `task.paths` (`.py` files) must be mapped to doc paths — a candidate for
  mapping in `docctx.py` when `doc_context` is actually enabled.
- **F2 (low).** The naive token heuristic `len(utf-8)//4` is inflated for
  Cyrillic (~2 bytes/char). For context budgeting it is enough,
  but in the docs it is worth noting that this is an upper bound.
- **F3 (carryover).** F3 of the M1 audit (single-file upload does not update the hash) and
  F5/F7 of the M2 audit remain in the Section F backlog — were not in scope.
- **F4 (process, positive).** Early closure of the second sprint
  in a row: the weekly window turned out to be excessive for the friction scope.
  At the M4 planning the window should be shortened or the scope — expanded.
- **F5 (open check M3).** The ROADMAP criterion "every fix is verified on
  the Orakul corpus (405 docs)" is not feasible in this environment: the project
  `orakul` is not registered in the config. The fixes are verified on the live
  ZAIrgRush corpus (dry-run shows warnings about unknown `type:`
  and a hidden-dirs counter). The check remains open in the ROADMAP until
  Orakul is re-registered.

## 4. Acceptance by goals

| Goal | Criterion | Result |
|---|---|---|
| G1 — friction #10 | path is normalized, drift in_sync for non-md | ✅ `ee51e7d`, red run recorded |
| G2 — friction #8 | `--limit N` + tail hint, default 50 | ✅ `b98b707` |
| G3 — friction #14/#11 | diataxis/quadrant mapping + visible skip of hidden | ✅ `523d2ba`, `5cbb57d` |
| Stretch ADO-039 | enforce checkout on all surfaces | ✅ `a2f3cfb` |
| Stretch SYM-008 | byte stability of off + 3 fail-open tests + check.sh | ✅ `0282835` + `4110cc6` |

Sprint DoD: red runs of bugs are recorded in the task-doc 'verification';
all tasks — checkout → complete with `commit_sha`; edits of tracked md
are imported in the same commits; drift 130/130; the ratchet did not grow;
friction log ADO-005 — 0 open entries.

## 5. Next step

- **M4** per [ROADMAP.md](../roadmap/ROADMAP.md): the goal is determined by
  the owner at the planning; candidates — the Section F backlog (F3 M1, F5/F7
  M2, F1/F2 of this audit) and Track B (RFC 16–21).
- **ZAIrgRush:** enabling `doc_context = "executor"` on a real task —
  a separate E5-C experiment with measurement (metrics per RFC 22 §3.4);
  bring `Cod-Doc-Task:` to a real mirror (a field in tasks.json).
