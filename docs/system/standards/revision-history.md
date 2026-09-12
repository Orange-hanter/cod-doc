---
type: standard
scope: revision-history
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../DATA_MODEL.md
  - ../capabilities/doc-evolution.md
---

# Revision History Standard

> A unified change-history model for all COD-DOC entities.
> Replaces manual changelog tables in markdown documents (as in Restate `Docs/MASTER_DOCUMENTATION.md` or module specs).

## 1. Principle

- Every write-path call writes **one** `revision` record (append-only).
- A revision contains a diff, author, timestamp, reason, optionally a commit SHA.
- A past body version is restored by playing diffs in reverse order.
- The `revision` table is immutable; compaction (squashing old revisions) — a separate service job with a snapshot every N changes.

## 2. Record schema

```yaml
revision_id: ULID                # 26 chars, '01HQX5Z9F0K8RNG6CB7VHQK4XX'
parent_revision_id: ULID | null  # previous revision of the same entity
entity_kind: document | task | plan | story | link | module | proposal
entity_id: <row_id>
author: agent:<name> | human:<login> | mcp:<client> | system:<service>
at: ISO-8601                     # must match the timestamp in revision_id
diff: unified-diff | json-patch
reason: freeform string (recommended)
commit_sha: optional
```

**The `revision_id` format** — ULID (Crockford-base32, 26 chars, 128 bits). The first 48 bits are a ms timestamp; the remaining 80 are random. Sorts lexicographically by time; safe for offline sessions and multiple replicas. Details — `revision` in [DATA_MODEL.md §3.5](../DATA_MODEL.md).

## 3. When a revision is written

| Event | entity_kind | Comment |
|---------|-------------|-------------|
| `DocService.create` | document | `diff` = `+ entire body` |
| `DocService.apply_patch` | document | canonical unified diff |
| `DocService.rename` | document | diff by metadata |
| `TaskService.create` / `update` / `complete` | task | JSON-patch by changed fields |
| `PlanService.recalc` | plan | a revision only if `Progress Overview` actually changed |
| `StoryService.*` | story | JSON-patch |
| `LinkService.resolve_bulk` | link | an aggregated revision over all changed links |
| `DocService.rename` + cascade | document × N | one revision per affected document (for navigation) |

## 4. Author

The `author` field is mandatory. Formats:

- `human:<login>` — a human CLI/TUI session.
- `agent:<role>` — an LLM agent (`agent:task-steward`, `agent:doc-reviewer`).
- `mcp:<client>` — an external MCP client (`mcp:claude-code`, `mcp:copilot`).
- `system:<service>` — background jobs (`system:link-verifier`).

Author substitution is forbidden at the API level: an MCP client cannot write `human:...`.

## 5. Diff formats

- Text fields of documents (`body`, `section.body`) — **unified diff**.
- Structural entities (task, story) — **JSON-patch** (RFC 6902).
- Document renames — JSON-patch by frontmatter + an empty diff for the body.

## 6. Reason (`reason`)

Recommended, but not mandatory. Used:

- In `cod-doc log <doc-key>` for a human-readable history.
- In MCP `revision.list` — the agent reads "why it was done this way" without lifting the code.
- In `export-changelog` for the public CHANGELOG.md.

Format: one or two lines in human language.

For tasks, the reason = `status:pending→in-progress` when reason is not specified explicitly.

## 7. Relation to Git

If COD-DOC is invoked in the context of a git commit (via a pre-commit hook or CLI with `--commit`), `commit_sha` is set automatically.

Conversely: `cod-doc log --since <sha>` can extract all revisions tied to commits from that SHA.

## 8. Rollback

`cod-doc revision revert <revision_id>`:

1. Reads the diff backwards.
2. Applies the inverse via the corresponding service.
3. Writes a **new** revision with `reason: "revert of <revision_id>"` (never deletes the old one).

Forbidden:

- Direct deletion of a `revision` record.
- Rollback without a corresponding service operation (cannot write "raw" content directly).

## 9. Export to a public CHANGELOG

The `cod-doc export-changelog --since YYYY-MM-DD` command:

- Groups revisions by day.
- Filters only `entity_kind ∈ {document, task, plan}` with `status=active` or `status=done`.
- Groups by module.
- Generates a markdown report suitable for publication (analog of Restate `Docs/MASTER_DOCUMENTATION.md §Change Log`).

## 10. Format of in-document history

For documents where a "visual" changelog right in the body matters (e.g. a master doc), a `## Changelog` section is supported. Its **body is generated** from the `revision` table on export — manual editing is forbidden (COD-DOC will overwrite it).

```markdown
## Changelog

| Date | Event |
|------|---------|
| 2026-04-19 | First version. |
| 2026-04-20 | Added the "Data Model" section. |
```

## 11. Retention

- By default — indefinite.
- A compact job `cod-doc revision compact --older-than 365d` creates a snapshot every N days and deletes "intermediate" diffs. The original operation is preserved as the final snapshot.
- Revisions for task statuses (low-value) can be compressed into a `task-status-series` per day.

## 12. Viewing

| Command | What it shows |
|---------|----------------|
| `cod-doc log <doc-key>` | Document history with diff |
| `cod-doc log task <AUTH-025>` | Task history |
| `cod-doc log --plan <plan> --since 7d` | Everything that changed in the plan over a week |
| `cod-doc revision show <id>` | Details of a single revision |
| MCP `revision.list(target)` | Agent equivalent |

## 13. The `audit_log` ↔ `revision` boundary (DOC-ME-7)

`revision` and `audit_log` are two different tables with non-overlapping responsibilities.
The rule is simple: **`revision` records successful state mutations, `audit_log`
records everything else observable in the system's behavior.**

| | `revision` | `audit_log` |
|---|---|---|
| What | successful state-mutations of entities (doc/task/story/plan/adr/…) | read-requests, authz denials, MCP-metadata, failed write attempts |
| When written | only after the change is committed | on any significant event, including rejected ones |
| Reversible | yes — carries the diff for rollback (§8) | no — it is a journal of facts, not a source of state |
| Who reads | `cod-doc log`, `revision.list`, export-changelog | security audit, diagnostics, metrics |

An example on a single `task.update_status` operation:

- **ok** (transition allowed, committed) → a record in `revision` (+ optionally an activity event).
- **denied** (authz / `StatusTransitionError` / blocked-by-deps) → a record in `audit_log`
  with the reason for denial; **no revision is written** (state did not change).

Corollary: the number of `revision`s ≈ the number of actual changes; `audit_log` is broader and
includes attempts and reads. For "what actually changed" — `revision`; for "what
happened, including denials" — `audit_log`.
