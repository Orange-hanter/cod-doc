---
name: ground-truth-reconcile
description: |
  Three-way reconciliation of DB ↔ markdown ↔ code when task and plan
  statuses diverge from reality. Arbitration order, how to prove "done"
  by code, how it differs from hash-drift. Triggers: reconcile, ground
  truth, source of truth, stale status, three sources, plan revision,
  what is actually done.
---

# Skill — Ground-truth reconciliation

## When it loads

When the question arises **"what is actually done here?"**: the
markdown-plan says `pending`, the DB says `done`, and the code holds a
working implementation. Trigger keywords: `reconcile`, `ground truth`,
`source of truth`, "statuses diverged", "what is actually done", "the
plan is stale".

This is **not** `drift-handling`. The difference:

| | `drift-handling` | `ground-truth-reconcile` |
|---|---|---|
| Subject | document hash vs file on disk | task status vs implementation in code |
| Symptom | `stale_export`, `edited_in_place` | "pending in the plan, but done in code" |
| Tool | `doc_drift`, `check_stale_refs` | reading code + tests, `plan audit` |
| Outcome | `doc export` / `import` | `task complete` / `task status` + plan edit |

## Arbitration order (do not reorder)

1. **The DB (`.cod-doc/state.db`) is the source of truth for tracked
   tasks.** The task status is determined by the record in the DB, not
   by a markdown table.
2. **Code is the arbiter on divergence.** If the DB and markdown
   disagree, we look at what is actually implemented and bring both to
   the code.
3. **The markdown Progress Overview is secondary.** It is rebuilt from
   the DB.

## What counts as proof of "done"

You can claim `done` only with **three** references:

- **Implementation** — `file.py:line` with the function/class name, not
  "there is one in the service".
- **Coverage** — a specific test file (better — the test name) that runs
  it.
- **Acceptance** — line-by-line check against the task's `acceptance`
  field.

No test → the task is not `done`, but `pending` with a refined
description. "The code seems to be there" is not proof: this is exactly
how reverse drift is born, when the plan lies in the optimistic
direction.

### A common special case: a stub remnant

The implementation is ready, tests exist, but the docstring / comment /
AGENTS.md still says "to be implemented". This is **not** a reason to
keep the task open — it is a separate one-line edit. Order: remove the
stale text → close the task → mention in the reason. This is how STB-001
(agent_tools) and STB-013 (context_service L2/L3) were closed.

## Run algorithm

```bash
# 1. What the DB considers open
sqlite3 .cod-doc/state.db \
  "SELECT t.task_id, pl.scope, t.title, t.priority
     FROM task t JOIN plan pl ON pl.row_id = t.plan_id
    WHERE t.status = 'pending';"

# 2. Plan integrity: cycles + done-with-unclosed-blockers
cod-doc plan audit <scope> -p <slug> --json

# 3. For each pending task — look for the implementation in code
#    (names from description / acceptance)

# 4. Close only the proven ones, with reason-referencess to file:line + test
cod-doc task complete <ID> -p <slug> \
  --author "reconcile-YYYY-MM-DD" \
  --reason "<implementation file:line> + <test-file>; acceptance fulfilled"

# 5. Sync markdown-plans from the DB, not the other way
```

The direction of step 5 is the only admissible one. Editing the
markdown-status without a corresponding record in the DB reproduces
exactly the problem the reconciliation fixes.

## What to do with findings

| Finding | Action |
|---|---|
| Done in code, `pending` in the DB | `task complete` with a reason-proof |
| `done` in the DB, missing in code | `task status <ID> pending` + file a regression bug |
| The task lost its meaning | `cancelled`, not a silent deletion — history is needed |
| Plan exists in markdown, but not in the DB | either create it via `plan_create`, or mark the plan archival; a "half-plan" is worse than none |
| Divergence ≥ 3 tasks | this is already a phase → audit-report, see skill `audit-cadence` |

## Cadence

Run the reconciliation **on closing a phase** and **before drafting the
roadmap** — otherwise you plan on top of a fictional state. Between
phases, `plan audit` in CI is enough.

## Anti-patterns

- **Trusting the markdown Progress Overview table.** It is secondary by
  definition; it is what we are fixing.
- **Closing tasks in a batch "looks like it's all done".** Each one with
  its own proof, otherwise the reconciliation itself becomes a source of
  lies.
- **Fixing only in one direction.** Drift is two-sided: both
  under-closed and over-reopened tasks are found.
- **Leaving the divergence undescribed.** The outcome of the
  reconciliation is an audit-report in `docs/system/audit/`, otherwise in a
  month no one will remember why the statuses changed.

## Related

- [roadmap/ROADMAP.md](../../../docs/system/roadmap/ROADMAP.md) — "The source-of-truth rule".
- [audit/2026-06-05-doc-drift-source-of-truth.md](../../../docs/system/audit/2026-06-05-doc-drift-source-of-truth.md) — the first run.
- skill `audit-cadence` — formatting the result.
- skill `drift-handling` — a neighboring, but different, problem.
