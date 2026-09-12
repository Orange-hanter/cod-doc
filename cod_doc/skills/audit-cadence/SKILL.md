---
name: audit-cadence
description: |
  Closing a phase → audit-report in docs/system/audit/.
  Opening a new phase → kickoff-brief in docs/system/roadmap/.
  Triggers: audit, phase, kickoff, close, complete, section, briefing,
  consolidation, milestone.
---

# Skill — Audit cadence

## When it loads

Tasks where the user / LLM **closes a phase of work** (section, milestone,
consolidation cycle) or **opens a new one**. Trigger keywords: `audit`,
`phase`, `kickoff`, `close`, `complete section`, `consolidation`,
`milestone`, `closure`, `briefing`.

## Two events, two artifacts

| Event | Artifact | Where it goes |
|---------|----------|---------------|
| Closing a phase / section / cycle | **audit-report** (`type: audit-report`) | `docs/system/audit/<YYYY-MM-DD>-<scope>-cycle-N.md` (or `<section>.md`) |
| Opening a new phase | **kickoff-brief** (`type: kickoff-brief`) | `docs/system/roadmap/<scope>-kickoff-<YYYY-MM-DD>.md` |

Example: Phase 1 paperclip-adoption is finished → write
`audit/2026-XX-YY-paperclip-phase-1.md`. Phase 2 starts → write
`roadmap/paperclip-phase-2-kickoff-2026-XX-YY.md`.

## audit-report skeleton

```
---
type: audit-report
scope: <section / cycle name>
status: active
source_of_truth: true
owner: cod-doc core
created: <YYYY-MM-DD>
last_updated: <YYYY-MM-DD>
related_docs: [...]
---

# <Section / Cycle> — Closure / Audit Report

## 1. TL;DR
## 2. Deliverables (table: # / item / file / status)
## 3. Findings (numbered F1, F2, ...)
## 4. Plan health
## 5. Acceptance
## 6. Out of cycle (handed off → next)
```

## kickoff-brief skeleton

```
---
type: kickoff-brief
scope: <new phase / section>
status: active
source_of_truth: false
canonical_source: <path to execution-plan>
owner: cod-doc core
audience: [next-session-agent, contributors]
---

# <Phase> — Kickoff Brief

## 1. TL;DR
## 2. Context / State
## 3. First tick (what to do right away)
## 4. Acceptance for this phase
## 5. Commands
```

## Rules

- In one cycle — exactly **N** audit-reports for N cycles; findings of
  one cycle → backlog in the next (the "findings → tasks → next cycle"
  pattern).
- Audit-report — `source_of_truth: true`, kickoff-brief — `false`
  (canonical_source points to the execution-plan).
- On closing a section, switch the related docs from `status: active` →
  `status: resolved` (for audit-reports of past cycles whose tasks are
  closed).
- The kickoff-brief lives until the phase closes; then it is archived
  into an audit-report.

## Related

- [docs/system/MASTER.md](../../../docs/system/MASTER.md) — global index
  of audit/ + roadmap/.
- [standards/frontmatter.md](../../../docs/system/standards/frontmatter.md) —
  valid `type:` and `status:`.
