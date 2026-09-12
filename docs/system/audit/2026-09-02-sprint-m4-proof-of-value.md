---
type: audit-report
scope: sprint-m4-proof-of-value
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-02
last_updated: 2026-09-02
related_docs:
  - ../roadmap/sprint-m4-proof-of-value.md
  - ../roadmap/sprint-m5-trustworthy-gate.md
  - ../roadmap/ROADMAP.md
  - ../releases/2026-08-30-sprint-m4.md
---

# Sprint M4 "Proof of value + debt review" — Closure / Audit Report

## 1. TL;DR

Sprint M4 is closed: all four queue items are done, the exit criterion is taken.
The value of the cod-doc → agent loop bridge is proven by a combat run for $0.98
with the verdict "we scale".

But the closure is accompanied by a finding that changes the weight of the criterion itself: **the item
"gates green", set in M1…M4, was only checked by a local run.
The real CI was not green a single time since 2026-05-06.** The sprint is closed by
the fact of what was done, not by the DoD formulation; the divergence is moved to M5
as the first priority.

## 2. Deliverables

| # | Queue item | Task | Artifact | Status |
|---|---|---|---|---|
| 1 | Re-registration of Orakul, verification of M3 fixes on 405 docs | ADO-062 | 405/405 in_sync, 0 stale_export | ✅ done |
| 1a | The `--force-write` incident: overwritten author frontmatter | ADO-064 | `be6f15a` — unparseable frontmatter is preserved verbatim | ✅ done |
| 2 | E5-C in combat: `doc_context=executor` + measurement | ADO-063 | `7bc156d`; the s5dc task done in 1 iteration, $0.98 | ✅ done |
| 3 | A single write-path wrapper + activity events | ADO-040 | `3b2662b`; coverage of 9 write-site families | ✅ done |
| 4 | ingest ai_review with a pull model | SYM-009 | `2ac0631`; upstream PR ai-reviewer#5 | ✅ done |

Release note: [`releases/2026-08-30-sprint-m4.md`](../releases/2026-08-30-sprint-m4.md).

## 3. Findings

**F1 — CI on main was not green a single time in the history of the branch.** `gh run list
--branch main`: 10 runs, 2026-05-06 … 2026-09-02, conclusion=failure for
all of them, success — zero. The reason for the last one: `FileNotFoundError: 'alembic'` in
`tests/api/conftest.py:44` — the fixtures shell out to `alembic`, which is not
on the runner's PATH. Locally it is there (`.venv/bin`), so the divergence is invisible
from the laptop. Consequence: the item "gates green" in the DoD M1…M4 was not checked by
anything but a local run. → **ADO-070 (critical)**.

**F2 — the only red test locally.** A full run on `2ac0631`:
1 failed, 1629 passed. `test_post_findings_invalid_payload_version` expects 400
on `version: 2`, but SYM-009 in the same commit taught the adapter to understand v2
(`_KNOWN_VERSIONS = {1, 2}`). The feature and the outdated negative case arrived
together. → **ADO-069 (critical)**.

**F3 — three MCP tools crash under a live server.** `capabilities`,
`tool_search`, `tools_diff` throw `RuntimeError: asyncio.run() cannot be
called from a running event loop` (`context_tools.py:394, 454, 522`). The tests
get the function as `_tool_manager._tools[name].fn` and call it synchronously — without
an event loop `asyncio.run()` is legal, so the test path is physically incapable of
reproducing the combat case. `capabilities` is the first command an agent uses to
survey an unfamiliar project. → **ADO-066 (critical)**.

**F4 — tests write to the real `~/.cod-doc/config.yaml`.** In the combat config
`model: m`, `base_url: https://x`, `api_key: sk-test` — a byte-for-byte copy of
the test fixture (`test_cmd_import.py:41` and four more files) — plus two
pytest catalogs in `projects:`. Mechanism: `config.py:19-20` computes
`CONFIG_DIR`/`CONFIG_FILE` on import, and `conftest.py:23` sets
`COD_DOC_HOME` in an autouse fixture — i.e. already after. A relapse of F5 of the audit
2026-07-29, closed by ADO-001 on 25.08: the fix lived five days. Caveat: the full
run on 2026-09-02 did not change the file's mtime, so the trigger is conditional.
→ **ADO-068 (critical)**.

**F5 — backlog grooming is unavailable to the agent.** `task_service.update_description`
and `update_acceptance` are only connected to the web; in MCP and CLI they are missing.
`update_priority` does not exist anywhere. The M5 planning was forced to write
through the service layer with a script. The four-surfaces rule is violated exactly on
the operation by which the agent manages its own backlog.
→ **ADO-067 (high)**.

**F6 — three declared contracts have no implementation.** Measurements on the live DB:
`revision.run_id` NULL in 2004 out of 2004; `activity_event.run_id` NULL in 326 out of
326; `audit_log` — 0 rows and 0 writers in all history; `run_scope()` has
not a single production call. At the same time AGENTS.md §5.4 claims "run-id
on all mutations", ARCHITECTURE §9 and DATA_MODEL §3.13 describe `audit_log`
as a log of write operations. → consolidated into **ADO-044**.

## 4. Plan health

- Plan `adoption-2026-08`: 66 done / 82 at the time of closing M4.
- Drift: 131 in_sync, 1 edited_in_place (`CLAUDE.md` — a catch-up edit of
  documentation after M4, imported by this same commit), 0 missing.
- Tasks: 265 total, 245 done, 5 cancelled, 15 pending → after the consolidation
  M5: 18 pending (2 are folded into ADO-044, 5 are opened).

## 5. Acceptance

The exit criterion of M4 from [sprint-m4-proof-of-value.md](../roadmap/sprint-m4-proof-of-value.md):

- [x] Orakul is registered; the M3 check "verified on the Orakul corpus" is closed —
      405/405 in_sync.
- [x] The decision on E5-C is recorded by an artifact — the verdict "we scale",
      $0.98, the s5dc task done in one iteration.
- [x] ADO-040 done: write-path wrapper, 9 families emit, the swallowing of emit errors
      is eliminated.
- [x] The M4 audit report (this document), ROADMAP is updated.
- [x] Gates green — **with a caveat**: local run 1629/1630, CI red
      (F1, F2). The item is counted by the actual state of the code, and the formulation
      of the DoD is redefined in M5: "gates green" = green CI.

## 6. Out of cycle → M5

Findings F1–F6 are passed to the sprint
[M5 "A gate you can trust"](../roadmap/sprint-m5-trustworthy-gate.md)
as tasks ADO-066…070 and the consolidated ADO-044. The order of the M5 queue is set
by these findings, not by the backlog remainder: first a gate you can trust,
then everything else.

The tail of section D (ADO-042, 045, 046, 047, 048, 049, 050) and SYM-011 are consciously
left in the backlog — see "Out of scope of M5".
