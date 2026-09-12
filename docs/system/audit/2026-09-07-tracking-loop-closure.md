---
type: audit-report
scope: tracking-loop-closure
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-07
last_updated: 2026-09-07
audience: [contributors, agents]
related_docs:
  - 2026-09-06-sprint-m5-trustworthy-gate.md
  - 2026-09-02-sprint-m4-proof-of-value.md
  - ../roadmap/ROADMAP.md
---

# Audit — closing the task-tracking loop: cod-doc and Orakul

## 1. Summary

The "observation → finding → task" loop was broken in all three registered
projects, and broken silently: routines were set up, enabled, half of them
had zero runs, and the ones that did run never landed their findings
anywhere. This session closed it for `cod-doc` and `orakul` and, in passing,
rescued 24 MB of Orakul history that the next `git worktree remove` would
have destroyed without a trace.

| What | Was | Now |
|---|---|---|
| Cron lines with `routine tick` | 1 (`zairgrush`) | 3 (`zairgrush`, `cod-doc`, `orakul`) |
| Routines with a task-creating policy | 0 of 4 | 4 (`doc_drift` and `link_integrity` in both projects) |
| Alembic revision of live DBs | `0028_findings` at head `0029_drop_audit_log` | `0029_drop_audit_log` in both |
| Orakul DB | `.cod-doc/state.db` inside a worktree in detached HEAD, `*.db` in `.gitignore` | `~/.cod-doc/orakul/state.db`, `db_url` in the registry |
| Orakul drift | 7 documents `edited_in_place` | 406 / 406 `in_sync`, `problem_count` 0 |
| Orakul tasks | 8, all `done` | 147: 8 `done` + 139 in the `orakul-delivery-2026-09` plan |
| Broken links | never measured even once | cod-doc 4 in 3 sections, orakul 13 in 6 |

## 2. Diagnosis: the mechanism is set up, enabled, and not running

This is the same class of defect that M5 was started for — "declared, looks
like it works, doesn't actually run" — but at the level not of a gate, but
of the observation loop itself.

**OS-cron poked one project out of three.** Before the session, `crontab`
held exactly one `cod-doc routine tick` line, and even that with
`-p zairgrush`. The `cod-doc` and `orakul` routines never ran at all:
`routine.fired` in cod-doc's `activity_event` before 2026-09-07 — four
events in the entire history (`doc_drift_daily` 2026-06-06, 2026-08-04,
2026-08-05 and `alembic_head_daily` 2026-08-28), all manual; in Orakul —
none at all.

**No routine had the right to create a task.** `on_finding` in every
project sat at the default `comment_only`. The result on `zairgrush`, the
only project that ticked: **567 runs** before the session
(555 `approval_stale_default` + 12 `doc_drift_daily`), **380 drift
findings**, **0 created tasks**. The last `doc_drift_daily` run saw 32
drifting documents — and saw them every night for ten nights in a row,
doing nothing about them. Here also a correction to the original phrasing
of the finding: the routines were **not** mute, they were powerless. Zero
is not findings, it is tasks.

The same picture on cod-doc in miniature: the last two `doc_drift_daily`
runs (2026-08-04 and 2026-08-05) produced 3 `stale_export` findings each
and zero tasks, after which the routine fell silent for a month.

**Corroborating evidence — migration lag.** Both live DBs sat on
`0028_findings` at head `0029_drop_audit_log`, with the `audit_log` table
physically present, which 0029 drops (in the snapshot of the Orakul DB at
the moment of rescue: `alembic_version = 0028_findings`, `audit_log`
present, 0 rows). The `alembic_head_daily` routine was set up precisely for
this class of discrepancy — and it was exactly the one that did not tick.
The mechanism guarding against lag itself lagged and stayed silent.
→ **ADO-083**.

## 3. Rescuing the Orakul DB

The Orakul project DB (23 748 608 bytes, 8490 `activity_event` events,
3201 revisions) lived in `.cod-doc/state.db` inside the git-worktree
`Orakul-client-file-size-split`, which was in **detached HEAD**, with `*.db`
in that repository's `.gitignore`. No copy existed anywhere. The next
`git worktree remove` — routine cleanup of one of Orakul's thirteen
worktrees — would have destroyed the entire project tracking history
silently and irreversibly.

The transfer was done with `sqlite3 .backup` to
`~/.cod-doc/orakul/state.db`; the project's registry entry was given
`db_url: sqlite:////Users/dakh/.cod-doc/orakul/state.db`. The `path` field
is **intentionally left** pointing at the worktree: the markdown corpus is
resolved through it, and rebinding to a permanent clone is a separate piece
of work that involves checking all 406 paths (**DOCS-005** in the Orakul
backlog).

The transfer immediately exposed a defect:
`cod_doc/api/deps.py::get_engine_for_slug` resolved the DB hard-coded as
`<root>/.cod-doc/state.db` and **ignored `db_url`**, whereas the CLI and
MCP go through `infra/db.py::db_for_entry`, which respects it. A project
with an external DB worked everywhere except the web UI and `/api/*`,
where it looked uninitialized. → **STO-021**, fixed in the session, PR #19
(draft). Local gate on the branch: `ruff` clean, `ruff format` — 516 files,
`mypy` strict — 321 files without remarks, `pytest` — 1726 passed. The task
was left `in_progress` until CI is green: a local run does not count as the
gate (ADO-070).

## 4. Orakul: from eight tasks to a delivery plan

**Drift is closed.** 7 documents were imported in the `edited_in_place`
state (`docs/00-current-state`,
`docs/08-technical/25-ai-code-review-pipeline-spec`,
`docs/08-technical/ai-review-false-positives`,
`docs/dev/explanation/ai-review-pipeline`,
`docs/dev/reference/api-endpoints-index`,
`docs/dev/reference/cli-and-quality-gates`, `docs/dev/reference/sibling-repos`).
Only `doc import`, not a single `doc export`: per ORA-010 the Orakul corpus
is files-as-source, and export would rewrite the author's YAML (ADO-064).
The result — **406 / 406 `in_sync`, `problem_count` 0**. Along the way, the
stale checkout-lock on `ORA-030` was released.

**Plan `orakul-delivery-2026-09`, 9 sections A…I.** The slicing axis is
domain, not phase and not priority. The reason is technical and hard:
`task_service` has `update_status`, `update_description`,
`update_acceptance`, `update_priority` and **no** way to move a task to
another section. So the axis must be the one that does not change over a
task's lifetime — and both phase and priority do change.

| Section | Tasks | Section | Tasks |
|---|---|---|---|
| A — Core & data health | 24 | F — AI review pipeline | 14 |
| B — Suppliers & purchasing | 18 | G — Accounting Export (US-031) | 7 |
| C — QR / POS / hall | 24 | H — BugDrop triage | 13 |
| D — Platform, infra & code quality | 21 | I — Docs & cod-doc hygiene | 8 |
| E — Business, legal & GTM | 10 | **Total** | **139** |

Of the 139 tasks, **137** carry a `GitHub: …` line with issue links in the
first line of their `description` (exceptions: `DOCS-002`, whose body was
overwritten by the very first routine run, and `FND-001`, which arrived via
finding promotion). Sources are GitHub issues, the roadmaps
`11-sprints-roadmap-2026-05-29`, `12-product-roadmap-2026-06`,
`13-roadmap-2026-06-10-qr-pos` and the undone-work register
`15-not-done-register-2026-06`; the source link is a `Source:` line
wherever one exists.

**Section H — 13 BugDrop complaint clusters**, collapsed from the raw
stream so that not a single issue number was lost: 41 numbers in the
canonical `GitHub:` line (44 counting mentions in bodies — `#175`, `#560`,
`#561` are discussed in bodies but not in the header). Clustering is by
scenario, not by complaint wording: four complaints about negative
preparation balance — one `DROP-001`, five complaints about layout — one
`DROP-010`.

**Why the GitHub link lives as a string in the description, not as an
edge.** The `link` table is unfit: `link.from_section_id` is a `NOT NULL`
FK to `section.row_id`, i.e. the source of an edge can only be a
**document** section, not a task. The `external_ref` table, introduced by
migration 0028 precisely for external trackers, has not a single call in
the entire code — only a model, a migration, and a re-export in
`models/__init__.py`. → **ADO-092**.

## 5. The loop is closed

**Routines were recreated, not reconfigured.** `routine_update_status`
changes exactly one flag — `enabled`; changing `on_finding`, `cron`, or
`check_args` is only possible via `routine_delete` + `routine_create`, and
delete cascades and wipes `routine_run`. The cost of the operation — 4
lines of run history (exactly the four manual `routine.fired` that
survived in `activity_event`). → **ADO-088**.

| Project | Routine | cron | `on_finding` | `check_args` |
|---|---|---|---|---|
| cod-doc | `doc_drift_daily` | `0 0 * * *` | `update_existing_task` | — |
| cod-doc | `link_integrity_daily` | `30 0 * * *` | `update_existing_task` | `{"limit": 1500}` |
| cod-doc | `alembic_head_daily` | `0 0 * * *` | `comment_only` | — |
| cod-doc | `task_stale_weekly` | `0 9 * * 1` | `comment_only` | — |
| orakul | `doc_drift_daily` | `0 1 * * *` | `update_existing_task` | — |
| orakul | `link_integrity_daily` | `30 1 * * *` | `update_existing_task` | `{"limit": 3000}` |
| orakul | `alembic_head_daily` | `0 1 * * *` | `comment_only` | — |
| orakul | `task_stale_weekly` | `0 9 * * 1` | `comment_only` | — |

**Holder tasks.** `update_existing_task` looks for an OPEN task with the
signature `<!-- routine:<name> -->` in its description and overwrites its
body with the run summary; if it finds none, it creates a `ROU-*`, taking
plan_id/section_id from **the last created task of the project**, i.e. in
an arbitrary place. That is why holders were created: `ADO-081` / `ADO-082`
in cod-doc and `DOCS-001` / `DOCS-002` in Orakul. They cannot be closed: a
task that goes to `done` drops out of the open set, and the next finding
will again spawn a garbage `ROU-*`. → **ADO-090**.

**Explicit limits on `link_integrity`.** The default `limit=500` in
`_check_link_integrity` silently truncates the section sample and serves
the result as "no violations". In cod-doc 1140 sections (43.9 % covered),
in Orakul — 2723 (18.4 %). A false negative in an integrity check is the
worst kind of finding: the routine is green, half the corpus is unseen.
→ **ADO-089**.

**Cron.** Two lines were added, `*/15 * * * *` for `cod-doc` and `orakul`
next to the existing one for `zairgrush`; the actual schedule is set by the
routine's own `cron`, the tick merely presents it with the time.

**The very first real run found something real.** `link_integrity_daily`:
cod-doc — **4 broken links in 3 sections** out of 1140 checked; orakul —
**13 broken in 6 sections** out of 2723. Both findings landed on the
holders (`task.updated_by_routine` in both DBs), not a single `ROU-*` was
created.

## 6. Finding ingest: one promotion and two pipeline defects

From the artifact of PR [Orakul#568](https://github.com/Orange-hanter/Orakul/pull/568)
**11 findings were raised: 10 rejected, 1 promoted** to `FND-001`.

The promoted one is the only one where the cost of error is not
hypothetical. `ESCALATION_LEVELS = ['manager','ops_director','owner']` is
moved into shared and read by five callers (`useRequestActions.ts`,
`opsEscalateRules.ts`, `opsEscalate.ts`, `opsEscalate.test.ts`,
`TasksTab/index.tsx`), and on both server and client the escalation is
positional. `satisfies readonly` constrains the membership but not the
order; the only test pins just the last element. Swapping two entries
compiles, passes tests, and sends a manager's request straight to the
owner.

The ingest, along the way, exposed two silent false-negative defects in
itself:

- **ADO-094** — `cmd_ingest.py:79-90` takes
  `sorted(dest.rglob("*.json"))[0]` and, with multiple files, only emits a
  `log.warning`. In the `pr-review-export-568` artifact,
  `orakul-ai-review-swarm.json` sorts first (`-` = 0x2D sorts before
  `.` = 0x2E) — a `role: external-verdict` document without a `findings`
  key. The adapter dutifully parses zero findings; there is no exception,
  and the wrongly chosen file is indistinguishable from a clean PR. The
  workaround in the session was `--input` with an explicit path.
- **ADO-095** — the artifacts of PR #564 and #565 exist, have not gone
  stale, and carry `"wouldBlock": true, "llmFailed": true` with
  `Ollama 403: your subscription payment is past due`. `ingest ai_review`
  parses such an artifact as an ordinary result with zero findings and
  nowhere surfaces the failure flags. To a reader this is "PR is clean",
  even though no review happened. On the Orakul side the same thing is
  filed as **AIR-014**.

Both defects live on the path that M5 declared the quality gate, and both
return "clean" instead of "not checked".

## 7. Filed in cod-doc: ADO-081…ADO-095

Fifteen tasks, all in section D "Residual debt" of the `adoption-2026-08`
plan.

| ID | What | Priority |
|---|---|---|
| **Loop infrastructure** | | |
| ADO-081 | Documentation drift — live task of the `doc_drift_daily` routine | medium |
| ADO-082 | Link integrity — live task of the `link_integrity_daily` routine | medium |
| ADO-083 | Live project DBs lagged behind the migration head, `alembic_head` did not tick | high |
| ADO-088 | No `routine_update`: changing `on_finding`/`cron`/`check_args` only via recreation with history loss | medium |
| ADO-089 | `link_integrity` silently checks only 500 sections and reports "no violations" | medium |
| ADO-090 | `update_existing_task` without a holder task creates a `ROU-*` in a random section | medium |
| **Finding ingest** | | |
| ADO-094 | `ingest ai_review` takes the first `.json` from the artifact and silently returns 0 findings | high |
| ADO-095 | `ingest ai_review` accepts a failed LLM review as a clean PR (`llmFailed`/`wouldBlock` ignored) | high |
| **Documentation vs DB** | | |
| ADO-084 | `ROADMAP.md` does not know about section G — 25 `STO-*` tasks filed without an audit report | medium |
| ADO-085 | `MASTER.md` is substantively stale: the numbers in it do not match the DB | medium |
| ADO-086 | audit TY-001: 47 cod-doc documents remain with import-fallback `type=module-spec status=draft` | medium |
| **Surface contracts** | | |
| ADO-087 | `--json` on the CLI breaks against rich: machine output depends on terminal width | medium |
| ADO-091 | `idempotency_key` is declared in the `task_create` signature but not implemented in the service | medium |
| ADO-092 | `external_ref` — a table without a single surface: the link to an external tracker lives as a string in `description` | low |
| ADO-093 | The `doc import` docstring promises only frontmatter, but imports the document body too | low |

A separate, unfiled observation from this same session: the holder
summary is written as `reported {len(findings)} finding(s)` and
`findings[:5]`, whereas `routine_run.findings_count` sums the `broken`
field. On cod-doc this gave "3 finding(s)" with `findings_count = 4`, on
Orakul — "6 finding(s)" with a list of five sections and
`findings_count = 13`. The numbers in the task and in the run log mean
different things and diverge; a candidate for the next cycle's backlog.

## 8. ADO-080 — verified, but not closed

Functionally the task is done: TY-001 lives in
`services/validation/advisory.py:95-119`, is covered by
`tests/services/test_import_fallback_audit.py`, and arrived in commits
`cedce85` and `efc02de` (PR #16, merged). Check on the live DB: 58 cod-doc
documents in the `module-spec` + `draft` pair, of which **47** without an
author's `type:` — exactly the corpus that TY-001 now surfaces instead of
pretending to be classified (this is also ADO-086).

The task was nevertheless left open. Its own body carries an unclosed
tail: "Follow-up: read the Russian frontmatter keys for Document/Status on import; consider an explicit
untyped default instead of pretending module-spec". The import still does
not read the Russian frontmatter keys of the Orakul corpus and still
silently slides into `module-spec`. Closing ADO-080 would sink this tail
along with the task — and it is exactly the reason why 406 Orakul documents
once ended up all of one type.

## 9. Conscious inactions

Each one with a reason, not out of forgetfulness.

**`completed_commit` was not backfilled** for the eight closed tasks
`ORA-001…ORA-030`. Recovering the sha from `activity_event` would yield a
guess, not a fact: events record the closing of a task, not the commit it
was closed with. The "closing — with sha" rule applies forward from this
point; rewriting the log retroactively means the log cannot be trusted
(the same principle by which M5 refused to backfill `actor_kind`).

**Drift-gate comments were not left on Orakul's open PRs.** After the
drift backlog was closed, the gate on all open PRs gives zero findings:
verified on #562, #564, #565, #568 and #569 (the last opened already
during the session) — `finding_count: 0`,
`counts_by_rule: {drift: 0, link: 0, frontmatter: 0}` on each. A comment
would have been empty noise in someone else's repository, and for a
mechanism that sells the thesis "a verifiable fact", noise is more
expensive than a miss.

**The orphaned and the original Orakul DBs were not deleted.** In the
main clone `Mozarella/Orakul/.cod-doc/state.db` lies a second DB from
2026-08-30 (18 726 912 bytes, 3114 revisions, 405 documents, 0 tasks,
`0028_findings`), in the worktree — the original from which the backup
was taken. Both remain as a rollback path until everything is verified:
the web surface after STO-021, the nightly routines, and markdown
resolution by `path`. Cleanup is for later, and a separate decision
(**DOCS-004** puts `.cod-doc/` into Orakul's `.gitignore`).

## 10. What rechecking changed in the original numbers

| Statement on entry | Check |
|---|---|
| 552 runs on zairgrush | **567** before the session (555 + 12), 572 at the time of the report |
| runs produced 0 findings and 0 tasks | **380 findings**, 0 tasks: the routines saw drift and had no right to file it |
| 137 tasks created in Orakul | **139** in the plan; 137 is the number of tasks with a `GitHub:` line in the description |
| 47 BugDrop complaints in 13 clusters | **41** issue numbers in the cluster headers, 44 counting those discussed in bodies |
| the default `limit=500` covers less than a fifth of both corpora | less than a fifth only for Orakul (18.4 %); cod-doc — 43.9 % |
| Orakul DB 24 MB, orphaned 19 MB | 23 748 608 and 18 726 912 bytes |
| four open Orakul PRs give zero findings | **five**: #569 opened 2026-09-07 08:21, also zero |

## 11. Definition of Done

- [x] The `doc_drift` and `link_integrity` routines in both projects have a
      task-creating policy and holder tasks under the signature.
- [x] `cod-doc routine tick` is set up in cron for every registered
      project, not just one.
- [x] Both live DBs are brought up to the migration head
      (`0029_drop_audit_log`).
- [x] The Orakul DB is moved out from under `git worktree remove`; `db_url`
      is in the registry.
- [x] Drift is closed in both projects: cod-doc 138 / 138, orakul 406 / 406,
      `problem_count` 0.
- [x] The session's findings are filed as tasks with a measurement, not a
      phrasing: ADO-081…ADO-095, AIR-014.
- [x] The audit report — status active, in the DB.

What remains open is what this session does not close: green CI on PR #19
(the closing condition of STO-021), rebinding Orakul's `path` to a
permanent clone (DOCS-005), cleaning up the two backup DBs, and the
`ROADMAP.md` that still does not know about section G (ADO-084) and about
the Orakul plan.
