---
name: module-audit
description: |
  Mandatory 5-dimensional drift audit on closing a module or a large task:
  code / logic / style / tests / docs. Green CI ≠ module ready. Findings
  are written to an audit-report; with ≥ 1 finding a remediation plan is
  opened.
  Triggers: module ready, close module, finish module, complete module,
  module done, large task done, milestone, end of phase, post-merge,
  drift check, drift, audit drift, module audit, completion audit.
---

# Skill — Module audit (drift check)

## When it loads

On closing a **module** (any `docs/modules/<m>.md` with an active status)
or a **large task** (multi-day, multi-file, multi-AC). Also on a manual
request "run an audit" / "check drift".

Do not confuse with `audit-cadence`: that one is about closing phases /
consolidation cycles of the project as a whole; this one is about a
**drift check across 5 dimensions** for a single module.

## Principle

Closing a module WITHOUT a 5-dimensional audit is a pencil-whip. Green
CI ≠ module ready. CI checks **only** code+test, but not logic / style /
docs. Drift between these layers accumulates quietly and surfaces later
as "strange bugs" / "docs do not match code" / "does this even still
work?".

The audit is ALWAYS done in the same order (1→5), even if a dimension
seems "not applicable". If a dimension is not applicable — explicitly
write "N/A" with a reason.

## 5 drift dimensions

### 1. Code drift — implementation vs specification

Check that what is WRITTEN in the code matches what is DECLARED in the
specifications.

| We compare | Against what |
|---------|-------|
| Public functions / methods / endpoints | `docs/modules/<m>.md` § Signatures / Endpoints |
| Data shapes (structures, JSON-schemas, ORM-models) | `DATA_MODEL.md`, migrations |
| File layout, dependency directions | `ARCHITECTURE.md` compose / layer diagram |
| Entity names (type, field, variable) | Everywhere they are declared |

Tools: `doc_drift`, `check_stale_refs`, `hash_file`, manual diff.

Symptom of code-drift: "the function is named differently than in the
module-spec", "in DATA_MODEL the column is `email`, but in code `mail`".

### 2. Logic drift — behavior vs user-story / AC

**This is the most subtle dimension.** CI does not catch it. Only a
mental walk-through and an integration run.

- Re-read the **acceptance criteria** of each task in the module. Is
  _exactly that_ implemented, or "something similar"?
- Re-read the related **user-stories**. Does the scenario run from start
  to finish? Are alternative branches (refused / error) also covered?
- Are edge cases from the stories covered or silently dropped?
- Smoke-flow: walk through 1 happy + 1 error path by hand.

Symptom of logic-drift: "everything works, but not _what_ was asked",
"we implemented a DB write, but the AC says: 'send a notification after
the write' — forgot".

### 3. Style drift — code / doc style

- **Lint**: `ruff` / `mypy` / formatter — zero output. If warnings have
  accumulated — that is already drift.
- **Naming**: snake_case in Python, kebab in slugs, consistency of
  similar identifiers (`auth_handler` vs `auth_h`).
- **Docs prose**: see skill `doc-style` — language, links, frontmatter,
  headings, hybrid refs.
- **Code structure**: dependency injection instead of globals, no
  import cycles, no TODO without an owner.

### 4. Test drift — tests are healthy

- CI is green.
- New modules / functions have tests — **especially error paths and
  edge cases**, not only happy.
- Coverage did not drop relative to baseline (if any).
- Removed / renamed features → their tests removed / renamed too. No
  "orphaned" tests for non-existent functions.
- Test names describe _what_ is tested, not _how_
  (`test_login_with_expired_token_returns_401`, not `test_login_2`).
- Flaky tests are marked `@pytest.mark.flaky` with a task to fix.

### 5. Documentation drift — docs reflect reality

- **Hybrid references**: `link_verify` / `check_stale_refs` — all
  statuses 🟢. Not a single 🔴 STALE / BROKEN.
- **Hashes**: `update_master_hashes` after any edits to sources
  mentioned in MASTER.md.
- **Frontmatter `status`**: modules of an active release → `active`. If
  a module is deprecated — switch the status and mention it in MASTER.md.
- **MASTER.md**: add / update the module section if the public API
  changed.
- **ADR / decision-log**: new architectural decisions recorded.

## Output artifact: audit-report

**`audit-report`** in `docs/system/audit/<YYYY-MM-DD>-<module>-drift.md`.

Header / frontmatter format — see skill `audit-cadence`.

In the **Findings** section list problems per dimension with the prefix
`[code]` / `[logic]` / `[style]` / `[test]` / `[docs]`:

```
F1 [code] PublicAPI `auth.refresh_token()` is declared in the module-spec,
   missing in code. Either implement, or remove from the spec.
F2 [logic] AC of task AUTH-007 "send a webhook on token revoke" is
   not fulfilled — the webhook is missing from the call log.
F3 [style] Identifier `auth_h` (3 places) is inconsistent with
   `auth_handler` (12 places). Unify.
F4 [test] No tests for the error-path `raise OnMissingToken` in
   `auth/middleware.py:42`.
F5 [docs] `auth.md` § Endpoints stale: 3 endpoints renamed in code,
   old names remain in the doc.
```

At the end a summary table:

| Dimension | F-count | Severity (C/M/L) |
|-----------|---------|---------------------|
| code  | 1 | C: 1 |
| logic | 1 | C: 1 |
| style | 1 | L: 1 |
| test  | 1 | M: 1 |
| docs  | 1 | M: 1 |

## If findings ≥ 1

Open a **remediation plan** via `plan_create`:

```
scope: "<module>-drift-remediation-<YYYY-MM-DD>"
principle: "Close all F# from the audit-report before moving the module to active"
```

Add one task per finding to the plan with
`type=bug/refactor/docs/test`, `priority` by severity, `description`
with a reference to F# in the audit-report.

Only after closing the remediation plan is the module moved to `active`.

## Anti-patterns

- ❌ **"CI passed → ready".** Only Code+Test is covered; Logic / Style /
  Docs are not.
- ❌ Auditing only your own layer (e.g. only code-drift) — this is a
  "partial" audit, not a drift-check. All 5 dimensions are mandatory.
- ❌ Closing a module with open `F1..FN` without a remediation plan.
- ❌ An audit-report without a summary table by type.
- ❌ Findings without severity (`C` / `M` / `L`) — impossible to
  prioritize remediation.
- ❌ "Ran the audit, found 0 problems" without explicitly listing the
  checked items. Otherwise it is impossible to distinguish "all ok" from
  "did not check".

## Related

- Skill `audit-cadence` — audit-report format (shared template + where
  the file goes).
- Skill `drift-handling` — what to do with STALE / BROKEN references,
  hash-mismatch.
- Skill `doc-style` — style-drift conventions for docs prose.
- Skill `task-standard` — task format that will go into the remediation
  plan.
- `docs/system/standards/task-plan.md` § "Closing a module".
