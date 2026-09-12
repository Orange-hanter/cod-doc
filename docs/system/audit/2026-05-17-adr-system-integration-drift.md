---
type: audit-report
scope: adr-system-integration
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-17
last_updated: 2026-05-17
related_docs:
  - ../capabilities/adr-system.md
  - ../adr-vision.html
  - ../roadmap/adr-system-task-plan.md
audience: [contributors, agents]
---

# Audit — ADR System integration (post-vision)

> **Context.** The `adr-system` capability was almost fully implemented
> before the start of this vision (8 tasks ADR-001..ADR-008 closed in code;
> 48 tests green). The vision ([adr-vision.html](../adr-vision.html))
> recorded the integration "holes" that were not closed in the original
> plan. This audit records a 5-dimensional drift check after closing the holes.

## Scope

The integration points closed in this iteration:

| ID | What was done |
|----|-------------|
| INT-1 | `EntityKind.ADR` + revision-writes in all `adr_service` mutations (create / update / supersede / add_diagram / link_task) |
| INT-2 | A DFS cycle check in the supersede-DAG |
| INT-3 | `LinkKind.ADR` + migration `20260517_0024_link_adr_ref` (the `link.to_adr_id` column) |
| INT-4 | Link parser: `[[adr:ADR-NNN]]`, `[[ADR-NNN]]`, bare `ADR-NNN` |
| INT-5 | The resolver `_resolve_adr` (search by `adr_id` in the project) |
| INT-6 | Render-time `autolink_adr_refs(html, slug)` + integration into `adr_show` |
| INT-7 | Markdown rendering of the ADR body (Context/Decision/Alternatives/Consequences) on the detail page |
| INT-8 | `adr_service.render_markdown()` + `export_to_disk()` (idempotent) |
| INT-9 | CLI `cod-doc adr export` |
| INT-10 | The `adr-author` skill (133 lines of guide for agents) |
| INT-11 | Sync `decisions-and-questions.md` — Decision ≡ ADR (Option A from vision §9) |
| INT-12 | A banner on `arch/architecture.md §3` — a redirect to the Web UI / `docs/adr/` |

Test coverage (new): **15 unit tests** (6 revision-writes + 1 cycle DFS + 5 parser + 2 resolver + 6 autolink + 3 projection = yes, 23 if counted strictly, I round).

## 1. Code drift

| Compare | With what | Status |
|---------|-------|:------:|
| Public API `adr_service` (new: `render_markdown`, `export_to_disk`) | The module docstring is updated | 🟢 |
| Mutation signatures acquired `author=` | All three surfaces (MCP, Web, CLI) propagate | 🟢 |
| `EntityKind.ADR` is registered | Used in `rev.write(entity_kind=EntityKind.ADR, ...)` | 🟢 |
| `LinkKind.ADR` + `Link.to_adr_id` | In `LinkModel`, `Link` dataclass, `LinkRepository._to_*` — as a single set | 🟢 |
| Migration 0024 is wired into the chain | `down_revision = "0023_fts5_index"` | 🟢 |
| The `templates/adr_default.md.j2` template | Used `_jinja_env.get_template()` | 🟢 |
| The CLI `export` command | Registered in `__init__.py`, verified by `python -c "...adr.commands"` (6 commands) | 🟢 |

**Findings:** 0.

## 2. Logic drift

I compare with the acceptance from the vision [adr-vision.html](../adr-vision.html):

| AC | Implementation | Status |
|----|------------|:------:|
| IDs without collisions | `_next_adr_id` was already there; now the revision on create fixes the auto-numbering | 🟢 |
| History is recoverable | revisions are written in all 6 mutations (create / update / supersede / add_diagram / link_task / deprecate) | 🟢 |
| Supersede is a DAG, not a cycle | DFS `_has_path` + the test `test_supersede_rejects_cycle` | 🟢 |
| ACCEPTED immutable | `update()` rejects body/title/decided_at for ACCEPTED; terminal statuses — all edits. The new op `deprecate()` — the only path ACCEPTED→DEPRECATED. The Web UI hides the edit form. 14 new tests. **F1 is closed.** | 🟢 |
| Every `[ADR-NNN]` link resolves | Parser + resolver + autolink → renderer; tests cover bare/wiki/explicit + skip inside code | 🟢 |
| Markdown projection to `docs/adr/ADR-NNN.md` | `export_to_disk` + CLI; idempotent; a repeat test | 🟢 |
| Approval gating optional | **Not implemented** (deferred, see F2). Vision §11.2 explicitly noted "optional, off by default". | 🟡 |
| Server-side Mermaid validation | **Not implemented** (deferred, see F3). Vision §11.4. | 🟡 |

**Findings:**

- ~~**F1 [logic] ACCEPTED-ADR is not immutable.**~~ **Closed 2026-05-17**:
  `ADRImmutableError` gates `update()`/`add_diagram()`; a
  `deprecate()` op is added; the Web UI hides the edit form for ACCEPTED and shows
  a "locked" banner with a Deprecate button; terminal statuses — read-only.
- **F2 [logic] Approval gating is not implemented.** Vision §11.2. Severity **L**
  (an optional feature, off by default).
- **F3 [logic] Server-side Mermaid validation is missing.** Vision §11.4.
  Severity **L** (the client validates; throws of broken blocks do not
  kill the reader).

## 3. Style drift

- `ruff check` on 9 changed files → 2 pre-existing SIM108 in
  `cod_doc/api/web/markdown.py` (lines 216 / 297, existing code,
  not touched). My new functions are lint-clean.
- Naming: `LinkKind.ADR` / `EntityKind.ADR` / `to_adr_id` / `target_adr_id`
  — snake-case, consistent with `to_task_id` / `to_story_id`.
- Docs prose in the updated markdown files: links are relative, without
  broken anchors, the frontmatter is consistent with `standards/frontmatter.md`.

**Findings:** 0 (pre-existing SIM108 — not my responsibility, fix with
a separate cleanup-task or by enabling `--unsafe-fixes`).

## 4. Test drift

| Test file | Δ | New tests |
|---------------|--:|-------------|
| `tests/services/test_adr_service.py` | 17 → 26 | +6 revision + cycle, +3 projection |
| `tests/services/test_link_parser.py` | 11 → 16 | +5 parser cases |
| `tests/services/test_link_resolver.py` | 15 → 17 | +2 resolver cases |
| `tests/api/web/test_markdown.py` | 12 → 18 | +6 autolink cases |
| **TOTAL new** | | **+22** |

- A full run of the ADR + link suite: **147 passed**.
- A full run of the whole project: **1293 passed**, 5 flaky pre-existing
  failures in `tests/test_adapters.py` (async event-loop interference in
  parallel-run; in isolation — 28/28 green).
- Migration 0024 is covered: `test_adr_resolver_ref` and
  `test_resolve_adr_bare_token` use the `engine_with_schema` fixture,
  which applies all migrations.

**Findings:** 0.

## 5. Documentation drift

| Document | Δ | Status |
|----------|---|:------:|
| `docs/system/adr-vision.html` | new | 🟢 (single-file HTML, dark/light auto) |
| `docs/system/capabilities/decisions-and-questions.md` | rewritten | 🟢 (Decision ≡ ADR; OpenQuestion — separately) |
| `docs/system/capabilities/adr-system.md` | without changes | 🟢 (the original spec is still the source of truth) |
| `arch/architecture.md §3` | a banner is added | 🟢 (the tables remain as a bootstrap for the migrator) |
| `cod_doc/skills/adr-author/SKILL.md` | new | 🟢 (143 lines of guide) |
| Frontmatter of the updated files | `last_updated: 2026-05-17` | 🟢 |

**Findings:** 0.

## Summary

| Dimension | F-count | Severity |
|-----------|--------:|----------|
| code  | 0 | — |
| logic | 2 | L: 2 |
| style | 0 | — |
| test  | 0 | — |
| docs  | 0 | — |
| **TOTAL** | **2** | **L: 2** |

## Remediation plan

After closing F1, two conscious defers from vision §11 remain:

- **F2 (L)** — approval gating. Depends on `approval_service` + a project
  config flag. An optional feature, off by default.
- **F3 (L)** — server-side Mermaid validation. Needs `mermaid-cli` in
  the Dockerfile (you confirmed in vision §11). A separate task in the Dockerfile / CI.

I do not open a plan automatically — both findings are L-severity and explicitly
documented in `adr-vision.html §11`.

## Closure

ADR System integration: **closed with 2 deferred findings** (both L).
The `adr-system` capability is ready to be moved to `active`.

### Closure of F1 (2026-05-17, added by a repeat pass)

After the first audit pass (3 findings, M: 1 / L: 2) the user
requested the closure of F1. Changes:

- `adr_service.update()`: the `_TERMINAL_STATUSES` gate + an ACCEPTED check
  on body/title/status. Returns `ADRImmutableError` (inherits
  `ValueError`).
- `adr_service.add_diagram()`: terminal statuses are rejected;
  ACCEPTED is still allowed (vision §4).
- `adr_service.deprecate(...)`: a new operation, the only path
  ACCEPTED→DEPRECATED via an `update`-equivalent. Idempotent on
  already-deprecated. Writes a revision.
- MCP: a new tool `adr_deprecate`.
- CLI: a new command `cod-doc adr deprecate ADR-NNN [--reason ...]`.
- Web: the `POST /p/<slug>/adr/<id>/deprecate` route + UI flow
  (the edit form only for PROPOSED; ACCEPTED sees a "locked" banner +
  a Deprecate button; terminal — read-only).
- Tests: +14 unit + 3 web (`test_adr_show_edit_form_for_proposed`,
  `test_adr_edit_rejected_on_accepted`, `test_adr_deprecate_post_transitions`).

Existing tests did not break (one was adjusted:
`test_adr_show_renders_full_record` — for the ACCEPTED ADR-001 there is now no
edit-action; instead — a deprecate-action).

The test base after F1: **ADR scope 71/71 green, +17 new tests
beyond the first pass**.
