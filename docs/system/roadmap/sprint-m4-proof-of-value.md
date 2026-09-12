---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-30
last_updated: 2026-09-02
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-30-m3-friction-log.md
  - ../audit/2026-09-06-sprint-m3-friction.md
---

# Sprint M4 — "Proof of value + debt triage"

> **Principle (owner decision 2026-08-30).** Code is written by an AI agent —
> deadlines and windows are not planned. A sprint is an **ordered queue of
> work** with clear contracts and an exit criterion. Order matters, dates do not.
>
> **Not source of truth.** Task statuses are in the DB (plan `adoption-2026-08`);
> priorities are in [ROADMAP.md](ROADMAP.md).

## 0. Ground truth at sprint start (reconciled 2026-08-30)

- M3 closed early: audit
  [2026-09-06-sprint-m3-friction.md](../audit/2026-09-06-sprint-m3-friction.md),
  final commit `1ae797e`. Friction log ADO-005 reset to zero (0 of 14).
- Suite 1601 passed, mypy clean (318 files), drift 130/130 in_sync,
  ratchet 6 entries.
- Plan `adoption-2026-08`: 61/78 done; the remainder is section D (12 tasks,
  the only high is ADO-040) and section E (SYM-009/010/011).
- The `orakul` pilot is not registered in the current environment — an open
  M3 checkbox in ROADMAP ("verified on the Orakul corpus").
- SYM-008 produced the cod-doc→ZAIrgRush bridge mechanics, but
  `doc_context = off`: E5-C metrics are not collected, value is not proven.

## 1. Queue (order = priority)

### 1. Re-register Orakul (unblock)

- `cod-doc project add` Orakul in the current environment; re-import the corpus.
- Run the M3 fixes on the corpus of 405 docs: dry-run `--limit 0`, hidden-dirs
  counter, warnings for foreign `type:`.
- Close the checkbox in ROADMAP ("each fix verified on the Orakul pilot
  corpus").
- Task: ADO-062 (chore, high) — a blocker for trust in the pilots.

### 2. E5-C in the field: doc_context=executor on a real ZAIrgRush task

The main question of the sprint: is there value in the cod-doc→loop bridge.

- Mapping `task.paths` (.py) → doc paths in `docctx.py`/config — without it,
  the document block is empty on real tasks (finding F1 of the M3 audit).
- Enable `doc_context = "executor"` in the ZAIrgRush swarm config.
- Run 1–3 real loop tasks; RFC 22 §3.4 metrics: latency/round, breaker-open
  rate, subjective quality assessment by the owner.
- Result — an artifact: findings + ADR/note in ZAIrgRush with a decision to
  "scale up / turn off" (both outcomes are valid).
- Task: ADO-063 (feature, critical) — the M4 exit criterion.

### 3. ADO-040 — unified write-path wrapper for activity events (high)

- A shared write-path helper/decorator (revision + activity + run_id).
- Coverage: adr/approval/task_doc/story/comment/checkout/link_resolver/
  repo_index/commit_link services emit events.
- Emit error — log/raise, not pass (currently swallowed,
  `task_service.py:405-420, 574-589`).
- The task is already in the DB (ADO-040, section D).

### 4. SYM-009 — ingest ai_review via a pull model (medium)

- `cod-doc ingest ai_review --from-pr N` + a poller
  `scripts/ingest-orakul-reviews.sh`.
- Upstream PR in ai-reviewer: fp/verifierStatus/actionabilityScore in
  slimFinding + bump EXPORT_VERSION (their P0 #18, ~10 lines).
- `cod-doc finding stability --sha`: pairwise Jaccard from
  finding_source_run.
- Acceptance (from the DB): 3 historical PR exports imported, repeat
  findings times_seen>1; Orakul git status clean; PR in ai-reviewer opened.

### 5. Section D tail (optional, only after items 1–4)

ADO-042 (SQL → repositories), ADO-043 (audit_log: writers or delete),
ADO-045 (DATA_MODEL sync). One task = one closed contract; not a sprint goal.

### Out of scope for M4

- SYM-010 (drift gate for Orakul) — pending a live Orakul (item 1) and demand
  from item 2; candidate for M5.
- SYM-011 (cross-project) — low, on demand.
- ADO-046…051 — backlog.
- Track B — rejected (ADO-056), do not reopen.

## 2. Exit criterion

1. Orakul is registered; the ROADMAP checkbox is closed.
2. The decision on E5-C is recorded as an artifact (ADR/findings + metrics).
3. ADO-040 done: wrapper + 9 services emit; an emit error is visible.
4. Gates are green: suite, ruff/format, mypy, drift 100%, ratchet ≤ 6.
5. M4 audit report (active, in the DB), ROADMAP updated.

## 3. Execution order

1. Setup: this sprint doc (doc create + import), tasks ADO-062/063 in the DB
   (section C), pointer in ROADMAP — one commit.
2. Item 1 → item 2 → item 3 → item 4 strictly in order; each task goes
   checkout → complete with `commit_sha`; bugs — with a red run.
3. Final: gates, audit report, ROADMAP, commit.

## 4. Risks

- **Empty blocks in E5-C** without a .py→doc-path mapping — the mapping is
  explicitly included in item 2.
- **The upstream PR in ai-reviewer (item 4) may stall** — an external
  dependency; if not merged by the end of M4, SYM-009 is moved, the sprint is
  not blocked.
- **Section D scope creep**: item 5 is optional by design.

## 5. Definition of Done

- [ ] Each queue task went through `task_checkout` → `task_complete` with
      `commit_sha`.
- [ ] The decision on E5-C is an artifact in ZAIrgRush (not "in the head").
- [ ] Edits of tracked `.md` are imported in the same commits;
      the final `doc drift --all` is 100% in_sync.
- [ ] `ruff` + `mypy` + `pytest` are green on the last commit;
      ratchet ≤ 6.
- [ ] M4 audit report — status active, in the DB, with links to commits.
