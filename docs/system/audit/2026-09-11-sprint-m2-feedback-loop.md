---
type: audit-report
scope: sprint-m2-feedback-loop
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-28
last_updated: 2026-08-29
related_docs:
  - ../roadmap/ROADMAP.md
  - ../roadmap/sprint-2026-08-28-m2-feedback-loop.md
  - 2026-09-10-sprint-m1-phase1.md
audience: [contributors, agents]
---

# Audit — Sprint 2026-08-28 → 2026-09-11 "M2: feedback loop built-in"

> **Context.** The second sprint per [ROADMAP.md](../roadmap/ROADMAP.md) (plan
> `adoption-2026-08`). Goals: G1 — friction top-1+4 (ADO-006), G2 — route
> drift (ADO-011/012), G3 — audit tails F1/F2/F4. Stretch: SYM-007.
> Sprint plan: [sprint-2026-08-28-m2-feedback-loop.md](../roadmap/sprint-2026-08-28-m2-feedback-loop.md).

## TL;DR

The sprint is closed early (2026-08-29 with a deadline of 2026-09-11): all three goals and
the stretch are done, 12 tasks done, 10 commits to main. The pilots' friction log
reached 14 entries in 2 days (the M2 criterion — ≥10); top-1+4 are turned into features
and merged. Engineering health is green: 1562 tests passed, ruff/mypy/drift
clean.

## 1. Deliverables

### G1 — Friction findings (ADO-006)

| Task | Commit | Content |
|---|---|---|
| ADO-030 (friction #5) | `fecde21` | Incremental FTS: `search_service.upsert_doc` in `import_or_update_markdown` — search after import without `--reindex`; regression test |
| ADO-031 (friction #6) | `afa75a9` | CLI `cod-doc doc delete` + bulk (`--path-glob`/`--type`/`--dry-run`/`--yes`); cascade of sections/links/FTS; `doc.deleted` activity event; revisions are not touched |
| ADO-032 (friction #9) | `17b7137` | `venv` in `_SKIP_DIRS` of the importer and the web scanner |
| ADO-033 (friction #13) | `6cdbe4c` | MASTER.md Context Map — only existing directories; a placeholder for an empty project |

### G3 — Audit tails F1/F2/F4

| Task | Commit | Content |
|---|---|---|
| ADO-027 (F1) | `9aaac08` | Routine check `alembic_head`: alembic_version of the working DB vs migration heads; the `alembic_head_daily` routine is registered on cod-doc, run findings=0 |
| ADO-028 (F2) | `1c66f38` | Full 5-field cron parsing (`_cron_next_fire`) in `tick()` instead of interval degradation; dom/dow OR semantics; a 60 min fallback for unrecognized |
| ADO-029 (F4) | `f9a09de` | onboarding-skill: `project add` — a mandatory step with an explanation of the consequences (cron/CLI outside cwd) |

### G2 — Route drift (ADO-011/012)

| Task | Commit | Content |
|---|---|---|
| ADO-011 | `635b004` | `web-frontend.md` §3 — a full registry of 87 routes (auto-generated from FastAPI), an audit script: drift 0/0 |
| ADO-012 | `cf328e7` | CI advisory-job `web-routes` (runs the route audit in CI, does not block PR); verified in a clean HOME — `project add` is enough for the audit |

### Stretch — SYM-007 (ADR-bridge ZAIrgRush)

13/13 ADRs of the ZAIrgRush project are entered into the adr-system of the `zairgrush` project;
the supersede links ADR-004→ADR-003 and ADR-008→ADR-007 are formalized. The source
`decisions.jsonl` is moved to the owner's repo
(`ZAIrgRush/experiments/decisions.jsonl`) — with the owner's approval.

## 2. Engineering health (at the end of the sprint)

| Check | Result |
|---|---|
| `pytest tests/` | 1562 passed |
| `ruff check` / `format --check` | clean (489 files) |
| `mypy cod_doc/` | clean (315 files) |
| `doc drift -p cod-doc --all` | 123/123 in_sync |

## 3. Findings

- **F1 (friction #8, backlog).** `--dry-run` truncates the candidate list to
  50 lines — for a corpus of 400+ documents there is nothing to make an exclude decision on.
  A `--limit`/full output to a file is needed.
- **F2 (friction #10, backlog, bug).** Imported `*.txt` files get a
  broken `path` in the DB → drift shows them as `missing`. The importer does not
  normalize the path for non-md files.
- **F3 (friction #11, docs).** Hidden directories (`.cursor`, `.claude`, …)
  are silently skipped by the import — the behavior is reasonable, but undocumented and not
  visible in dry-run.
- **F4 (friction #14, backlog).** Russian-language frontmatter without a
  `type:` key (`diataxis`/`quadrant` in Orakul) → all documents get the type
  `module-spec`. A candidate for a mapping to DocumentType (analogous to ADO-015).
- **F5 (carryover).** F3 of the M1 audit (single-file upload does not update the hash) was not
  taken into the sprint — remains in the backlog.
- **F6 (process).** Two incidents of "stale link / forgotten re-import":
  the web page routines linked to the removed `_cron_interval_minutes`
  (caught by mypy on the final run), after editing tracked md files `doc import`
  was forgotten twice. The rule: the final mypy + drift run is
  a mandatory step before the report, not an option.
- **F7 (low).** The parser of §3 of web-frontend.md accepts exactly one
  `METHOD /path` per row of the table — document the limitation in
  the section template.

## 4. Acceptance by goals

| Goal | Criterion | Result |
|---|---|---|
| G1 — friction top-1+4 (ADO-006) | ≥4 findings from the log turned into features and merged | ✅ ADO-030/031/032/033 in main |
| G2 — route drift (ADO-011/012) | A route registry with zero drift + CI control | ✅ 87 routes, 0/0, advisory-job |
| G3 — tails F1/F2/F4 | All three findings closed by code/docs | ✅ ADO-027/028/029 in main |
| Stretch — SYM-007 | ADR ZAIrgRush in the adr-system | ✅ 13/13 + 2 supersede |

The M2 criterion on the friction log (≥10 entries) is exceeded: 14 entries.

## 5. Next step

- **M3** per [ROADMAP.md](../roadmap/ROADMAP.md): a feature on demand from
  the friction log — candidates F1/F2/F4 of this audit.
- **Stretch candidate:** SYM-008 — the E5-C loop in ZAIrgRush.
- The remainder of the friction log (#8, #10, #11, #14) — into the Section F backlog,
  prioritization at the M3 planning.
