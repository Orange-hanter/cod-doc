---
name: rfc-authoring
description: |
  How to write an RFC in proposals/: structure (context → current state →
  proposal → migration → risks), mandatory category/risk/dependencies,
  the "RFC ready for decomposition" criterion. Triggers: rfc, proposal,
  borrowing, idea, borrow, adapt, new capability, concept, proposals,
  NN-.
---

# Skill — RFC authoring

## When it loads

When an idea is larger than a task, but not yet a plan: borrowing
someone else's pattern, a new capability, an architectural shift. Trigger
keywords: `RFC`, `proposal`, "concept", `proposals/`.

**Threshold.** An RFC is written if the idea (a) touches ≥ 2 layers or
introduces a new DB entity, **or** (b) requires ≥ 5 tasks. Less — that
is just a task via `task_create`. An architectural *decision* on an
already-adopted direction is not an RFC, but an ADR (skill `adr-author`).

## File format

`proposals/NN-kebab-slug.md`, where `NN` is the next free two-digit
number. The number is **not reused** even after the RFC is rejected.

The first line after the heading is a mandatory meta-line:

```markdown
# 21 — Degraded-Path Auditability + Error Audit Trail (hybrid)

> Category: 🟡 Adaptation · Risk: medium · Dependencies: proposal 04, proposal 09
```

| Field | Values |
|---|---|
| Category | 🎯 Direct (fits the existing model) · 🟡 Adaptation (needs work) · 🔵 Architecture (changes layer boundaries) |
| Risk | low · medium · high |
| Dependencies | numbers of other proposals or "none" |

## Body structure

1. **Context** — what pain, where known from. If borrowing — name the
   source and what exactly was studied.
2. **Current state** — what already exists in the code, with `file:line`.
   The section is mandatory: without it the RFC proposes what is already
   implemented.
3. **Proposal** — concrete: signatures, table schema, tool names.
   Pseudocode is allowed, "make it nice" is not.
4. **Migration / backward compatibility** — what breaks, what to do with
   existing data.
5. **Risks and what we do not do** — explicit non-goals. This section
   saves more time than all the others.
6. **Estimate** — rough: how many tasks, what order of weeks.

## Registration

An RFC does not exist until it lands in two places:

1. A row in the [`proposals/README.md`](../../../proposals/README.md)
   table (`# | Document | Category | Effect | Risk`).
2. A place in the adoption graph there (mermaid) — what it depends on,
   what it unblocks.

An RFC without a row in README is a draft in a personal folder, not a
proposal.

## "Ready for decomposition" criterion

- [ ] The "Current state" section is verified against the code, not from
  memory.
- [ ] There is at least one concrete contract (signature / SQL / tool
  name).
- [ ] Non-goals are written out.
- [ ] Dependencies on other RFCs are named by numbers.
- [ ] An estimate in tasks and weeks is present.

Next — `plan_create` on your own scope and decomposition via skill
`plan-to-tasks`. **Do not** append RFC tasks to someone else's existing
plan.

## Lifecycle

An RFC is not a status document. It does **not** move to `done`:
implementation is tracked by the plan in the DB. A rejected RFC stays
in `proposals/` with an explicit note in README about the reason — this
is a cheap protection against re-inventing.

## Anti-patterns

- **An RFC instead of a task.** 21 RFCs for 5 plans is already a skew;
  writing an RFC for what is done in a day means growing a backlog of
  documents.
- **"Current state" from memory.** RFC #21 described 8 places with
  `pragma: no cover` that by the time of implementation were already
  closed by task STB-010 — part of the proposal was stale before the
  start.
- **An RFC without non-goals.** The scope creeps on the very first task.
- **Implementing an RFC without a plan.** Tasks dissolve into someone
  else's sections, and progress on the RFC becomes unmeasurable.

## Related

- [proposals/README.md](../../../proposals/README.md) — catalog and adoption order.
- skill `plan-to-tasks` — the next step after adoption.
- skill `adr-author` — for decisions, not proposals.
