---
name: adr-author
description: |
  When to write an ADR (Architecture Decision Record) and how to frame its
  Context / Decision / Alternatives / Consequences. The skill loads when
  an agent or human makes an architectural decision affecting ≥ 2 modules
  or changing the DB schema / contract between layers.
  Triggers: adr, decision, supersede, deprecate, rationale, trade-off,
  architecture, alternative, choose, switch, replace, migrate.
---

# Skill — ADR Author

## When it loads

Tasks where an architectural decision is discussed or recorded.
Trigger keywords: `ADR`, `decision`, `supersede`, `rationale`,
`trade-off`, `alternative`, "choose", "replace", "abandon".

## When to write an ADR (threshold)

Write an ADR if **at least one** holds:

- the decision affects ≥ 2 modules (e.g. `domain` + `infra`);
- the DB schema changes (table, column, index, migration);
- the contract between layers changes (a type in `domain/entities.py`,
  an MCP tool, a web route, a CLI format);
- the decision sets a **policy** everyone must follow
  ("all mutations write a revision", "all requests go through a
  repository");
- the decision **reverts** a previous one (`supersedes ADR-NNN`).

**Do not** write an ADR for:

- the internal implementation of one module without external effect;
- minor refactors / renames;
- bugfixes without architectural consequences;
- choosing a variable name / code style (that is standards, not ADR).

## ADR structure

An ADR in COD-DOC is four markdown fields plus metadata. Each field has
a specific purpose; do not confuse them.

### 1. Context (what we are deciding)

**Describe the problem, not the solution.** What does not work in the
current state? What constraints / forces / dependencies force a decision
now?

> Template questions:
> - What was there before?
> - What changed / what new appeared?
> - What alternative paths have already been considered or rejected?
> - What are the constraints (perf, deadline, team size, existing code)?

A good Context is 2–6 paragraphs. If it fits in one sentence — most
likely an ADR is not needed.

### 2. Decision (what we chose)

**A statement in the present tense.** "We use X". Not "we will try X",
not "X is probably better". An ADR is a commitment.

If the decision is composite — list it as bullets. Immediately state
**what we did NOT choose** for contrast (this works better than a long
Decision).

### 3. Alternatives considered

**What was rejected and why.** At least 2 alternatives. Without this an
ADR is indistinguishable from an opinion.

Format for each:
- name;
- one or two sentences "why rejected" (not "does not fit", but
  "would cause X").

### 4. Consequences

**What will change after the decision is adopted.** Split into:

- **Positive** — what will improve / a new opportunity appears;
- **Negative / cost** — what will get worse / what compromises;
- **Future tasks** — what needs to be done as a consequence (link to
  task_id if any).

> Do not write "no positives" — if there really are none, an ADR is not
> needed.

## Lifecycle

```
PROPOSED ──accept──▶ ACCEPTED ──supersede──▶ SUPERSEDED
   │                     │
   └─reject──▶ DEPRECATED └─deprecate──▶ DEPRECATED
```

- **PROPOSED** — a submission. The body is editable.
- **ACCEPTED** — the active decision. The body is immutable (only
  supersede / deprecate / add_diagram).
- **SUPERSEDED** — replaced by a specific ADR (the `superseded_by`
  field). The status is set automatically by the service on
  `adr_supersede`.
- **DEPRECATED** — cancelled without a replacement. Terminal.

## Supersede vs Update

| You want | What to do |
|---------|------------|
| Fix a typo in an ACCEPTED ADR | The body is immutable; create a new ADR with `supersedes` |
| Change the decision substantially | New ADR + supersede |
| Clarify the context from "we now know" | New ADR + supersede |
| The ADR is still PROPOSED, needs work | `adr_update` (can mutate) |

## Authorship

- Human: `decided_by: human:<email>`.
- Agent: `decided_by: agent:<run-id>` (run_id is taken from `run_context`
  automatically when a revision is written).

## Links

- **Task → ADR:** `adr_link_task(adr_id="<ADR-NNN>", task_id="COD-123",
  relation=implements)`. If the task is a consequence of the decision.
- **ADR in markdown:** just write `ADR-NNN` or `[[adr:ADR-NNN]]` — the
  link-service will parse and resolve it.

## One agent pass

If you are an agent writing an ADR within one task:

1. Frame the Context (2–3 paragraphs).
2. List 2+ alternatives.
3. Pick one, justify in the Decision.
4. Distill Consequences (+ / − / next tasks).
5. `adr_create(...)` with `status="proposed"`. Do not set `accepted`
   yourself — a human does it in the Web UI or via CLI.
6. If the decision replaces an old one — after `accept` (a human does it)
   call `adr_supersede(superseding=NEW, superseded=OLD, reason=...)`.

## References

- [Capability adr-system](../../../docs/system/capabilities/adr-system.md)
- [Vision](../../../docs/system/adr-vision.html)
- [Default template](../../../templates/adr_default.md.j2)
- [Roadmap](../../../docs/system/roadmap/adr-system-task-plan.md)
