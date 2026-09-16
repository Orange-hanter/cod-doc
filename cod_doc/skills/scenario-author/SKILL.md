---
name: scenario-author
description: |
  How to write a test scenario in cod-doc: the five RFC 24 §9 kinds, the
  shape (preconditions / steps / expected result), anchoring on a capability
  document, and the one thing an author must never set — a coverage verdict.
  Triggers: scenario, test scenario, сценарий, сценарий тестирования,
  happy path, error path, boundary, invariant, integration, preconditions,
  expected result, coverage, scenario_create, scenario_export, RFC 24.
---

# Skill — Scenario author

## When this loads

Anything that writes or edits a test scenario: `scenario_create`,
`scenario_update`, `scenario_set_steps`, `cod-doc scenario new`, or a request
phrased as "describe how we'd test X", "add scenarios for capability Y".

## What a scenario is here

A scenario is a **claim about what should be true**, written down so a human
or an agent can act on it. It is not a test, and it does not know whether a
test exists.

RFC 24 splits the problem in two, and this skill only touches one half:

| Half | Who owns it | Where it lives |
|---|---|---|
| Intention — the claim | you, via `scenario_*` | `scenario` tables, projected to `docs/system/scenarios/` |
| Evidence — does a test prove it | the structure producer in ai-reviewer | `scenario_assessment` (STR-002), not yet built |

## The five kinds (RFC 24 §9, fixed vocabulary)

Do not invent a sixth. The producer joins assessments on these exact names.

| Kind | Use it for | Example from this repo |
|---|---|---|
| `happy_path` | the capability doing its job with valid input | completing a task recomputes plan progress |
| `error_path` | a named failure the system must handle, not crash on | `task_update_status` from `todo` without `via_checkout` is refused (ADO-039) |
| `boundary_value` | the edge of a range, an empty set, the first/last element | exporting a group whose scenarios were all retired |
| `invariant` | something that must hold across every operation | every mutating service writes a revision and an activity event (ADO-040) |
| `integration` | two or more surfaces meeting | a task mutation exposed in `task_service` appears in both CLI and MCP (ADO-067) |

Every group needs one `happy_path` and at least one `error_path`.
`scenario coverage` reports the gap; the rest is judgement.

## The shape

**Preconditions — state, not actions.** What is already true when the
scenario begins. "A plan with one open task exists", not "create a plan".

**Steps — one action each, imperative, no assertions.** "Complete the task",
"Read the plan progress". A step that says "check that…" belongs in the
expected result.

**Expected result — one observable outcome.** Something you could point at:
a status, a returned field, a file on disk, an emitted event. "It works" is
not an expected result.

## Anchoring

Always pass `--doc-key docs/system/capabilities/<x>` and, when the obligation
lives in a specific section, `--section-anchor`. The anchor is what lets a
later change notice that the documented obligation moved underneath the
scenario.

Find the anchor with:

```bash
cod-doc doc show docs/system/capabilities/plan-management -p cod-doc --json | jq '.sections[].anchor'
```

The group key defaults to the document's basename and is also the projection
filename, so an anchored scenario needs no `--group`.

## The forbidden move

**Never set a coverage status.** `covered`, `partial`, `missing` and
`unverifiable` are RFC 24 §9 *evidence*, derived by the producer under rules
cod-doc cannot enforce on hand-typed input — an aggregate-LCOV ceiling, a
mandatory `statusReason`, `unresolved` never decaying into `missing`. The
write path rejects them with `SCV-003`.

What you may set is the claim status: `draft` while writing, `confirmed` once
someone stands behind it, `retired` when it no longer applies.

## Tool order

MCP `scenario_create` / `scenario_set_steps` → CLI `cod-doc scenario new` →
the service layer directly. Use the lowest one that works.

## The export ritual

Markdown is the artefact, the tables are the source:

```bash
cod-doc scenario export -p cod-doc --group plan-management
cod-doc doc drift -p cod-doc --doc-key docs/system/scenarios/plan-management   # expect in_sync
```

Never edit `docs/system/scenarios/*.md` by hand — the next export refuses to
overwrite it and `doc drift` reports `edited_in_place`. Fix the scenarios and
re-export.

## Good and bad, side by side

**Good**

```
title:         Plan progress recomputes after a task completes
kind:          happy_path
doc-key:       docs/system/capabilities/plan-management
preconditions: A plan has one section with two tasks, both in `todo`.
steps:         1. Check out the first task
               2. Complete it with a commit sha
               3. Read plan_progress for the plan
expected:      plan_progress reports 1 of 2 tasks done for that section.
```

**Bad — and why**

```
title:         Test plans                        ← names no behaviour
kind:          smoke                             ← not one of the five kinds
preconditions: Create a plan and some tasks      ← an action, not a state
steps:         1. Do the plan stuff and check
                  progress is correct            ← two actions plus an assertion
expected:      It works                          ← nothing observable
status:        covered                           ← a verdict, not a claim (SCV-003)
```
