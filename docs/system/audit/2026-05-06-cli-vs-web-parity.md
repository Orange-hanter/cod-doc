---
type: audit-report
scope: cod_doc/cli/* vs cod_doc/api/web/* (CLI ↔ Web parity)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-06
last_updated: 2026-05-06
audit_target_revision: HEAD = b07a97e (post WEB-013..014/COD-070..079, daemon UI)
related_docs:
  - ../MASTER.md
  - ../ARCHITECTURE.md
  - ../capabilities/web-frontend.md
  - 2026-05-02-section-web-frontend.md
related_code:
  - cod_doc/cli/
  - cod_doc/api/web/
---

# CLI ↔ Web parity — System Audit (2026-05-06)

> Audit of divergence: what is available through the CLI, but **missing or
> partially implemented in the Web UI**. The goal is to record the debt in
> order to satisfy the invariant [ARCHITECTURE.md §1](../ARCHITECTURE.md):
> "everything available in the services must be available in the Web within
> one PR-cycle after CLI/MCP".
>
> Source of the CLI command list — `grep -rnE "@.*\.command\("` over
> [cod_doc/cli/](../../../cod_doc/cli/). Source of the Web route list —
> [cod_doc/api/web/pages/](../../../cod_doc/api/web/pages/) +
> [fragments/](../../../cod_doc/api/web/fragments/).
>
> Verified manually, not by an agent: see §6 "Methodology".

## 0. Baseline statistics

| Metric | Value |
|---|---:|
| CLI commands (including subcommands) | **50** |
| Web routes (pages + fragments) | **38** |
| Domains covered by Web | 8 / 11 (`docs`, `tasks`, `plans`, `stories`, `revisions`, `project`, `settings`, `daemon`) |
| Domains **without** Web | 3 (`link`, `hash`, `audit`) |
| Pure read-only in Web (without write-counterpart) | `revisions`, `link*`, `audit*` (* missing entirely) |
| Web operations without a CLI analog | **6** (see §0.1 — AI-generate ×4, `import_master`, `plan freeze`, fields edit) |

### 0.1 Symmetry: where Web already outpaces CLI

For honest accounting — this audit is not one-sided. The Web has operations
that are not exposed in the CLI:

| Web operation | Route | CLI analog |
|---|---|---|
| AI document generation | `POST /p/{slug}/docs/generate` + `/generate/save` | ❌ |
| AI story generation | `POST /p/{slug}/stories/generate` + `/save` | only `story create` (manual) |
| AI task generation from a story | `POST /p/{slug}/stories/{id}/tasks/generate` + `/save` | ❌ |
| AI improvement of task fields | `POST /p/{slug}/tasks/{id}/fields/{field}/improve` | ❌ |
| Project bootstrap from MASTER.md | `POST /p/{slug}/import_master/scan` + `/save` | ❌ (`import all` exists, but with different semantics) |
| Inline editing of description/acceptance | `GET/POST /p/{slug}/tasks/{id}/fields/{field}` | ❌ (CLI only has status/complete/create) |
| Inline editing of document sections | `GET/POST /p/{slug}/docs/{key}/sections/{anchor}` | ❌ (CLI only edits via whole-document `doc rename`/`doc import`) |
| One-click "accept document" | `POST /p/{slug}/docs-accept` | ❌ (CLI only sets status via create/rename) |
| `plan freeze` | `POST /p/{slug}/plans/{id}/freeze` | ❌ |

This means that in several operations **the CLI lags behind the Web**, and
when closing the gaps from §1-§3 it is reasonable to also expose this
functionality in CLI/MCP, so as not to accumulate inverse debt. Especially
— `task field edit` and `doc section patch`, where the Web is already
mature, while headless scenarios (MCP agent) cannot edit a
description/section outside of `doc import`.

## Summary

| Severity | Count | Description |
|---|---:|---|
| critical | 0 | — |
| high | 5 | Entire domains without UI: links, audit, hash; key operations: doc rename, revision revert |
| medium | 9 | Plan analytics (audit/critical-path/chains), story write-ops, doc drift/export, task create form |
| low | 4 | Add/remove project, log progress, task list-blocked, hash calc |
| **total** | **18** | — |

---

## 1. High

### CW-HI-1. The `link` domain is missing from the Web entirely

**Where:** [cod_doc/cli/link.py](../../../cod_doc/cli/link.py) — 4 subcommands.

| CLI | Service | Web |
|---|---|---|
| `link list DOC --section ANCHOR` | `link_service.list_for_section` | ❌ |
| `link sync DOC ANCHOR` | `link_service.sync_section` | ❌ |
| `link verify DOC ANCHOR` | `link_service.verify_section` | ❌ |
| `link backfill` | `link_service.backfill_project` | ❌ |

**Symptom:** the link model is the foundation of COD-DOC (auto-linking,
[capabilities/auto-linking.md](../capabilities/auto-linking.md)), but the
Web-front user does not see a single link and cannot re-parse them. Broken
links in a document are only discoverable from the terminal. The document
page does not even have an "N broken links" badge.

**What is needed:**
1. On [doc_show.html](../../../cod_doc/templates/web/project/doc_show.html)
   add a "Links" panel next to the sections: ok / broken / skipped counters
   from `link_service.verify_section`.
2. Endpoint `POST /p/{slug}/docs/{doc_key:path}/sections/{anchor}/links/sync`
   with HTMX returning the updated panel.
3. Project-wide `link backfill` — a button in `/settings` (a dangerous
   operation, confirm-modal).

### CW-HI-2. `doc rename` — not implemented in the Web

**Where:** [cod_doc/cli/doc/cmd_rename.py](../../../cod_doc/cli/doc/cmd_rename.py).

```bash
cod-doc doc rename DOC_KEY NEW_KEY -p P [--path NEW_PATH] [--no-cascade]
```

**Symptom:** renaming a document = invalidating all links in the project,
+ optionally moving the file + cascade updating the link table.
Impossible from the Web. Any refactoring of the docs hierarchy bypasses the UI.

**What is needed:** [doc_show.html](../../../cod_doc/templates/web/project/doc_show.html)
header → a "Rename" button, an HTMX form (`new_key`, `new_path?`, `cascade?`),
endpoint `POST /p/{slug}/docs/{doc_key:path}/rename`. DoD: error-branch
coverage (key conflict, broken refs).

### CW-HI-3. `revision revert` + `revision show` — not implemented in the Web

**Where:** [cod_doc/cli/revision.py:222-315](../../../cod_doc/cli/revision.py).

| CLI | Web | Note |
|---|---|---|
| `revision list` | ✅ `GET /p/{slug}/revisions` | parity |
| `revision show ID` | ❌ | Web shows a preview in the list, but not the full diff |
| `revision revert ID` | ❌ | History rollback is only available from the terminal |

**Symptom:** the audit tab is implemented as a read-only log. You cannot
jump from the log to the full snapshot of a previous version. You cannot
roll back. On a revision conflict (see WEB-022 alert flow) the user sees
a warning, but has no tool to roll back someone else's change from the
browser.

**What is needed:** `GET /p/{slug}/revisions/{rev_id}` (full payload + diff),
`POST /p/{slug}/revisions/{rev_id}/revert` (confirm-modal, HTMX-target
`#alerts`). Re-use [_frag/section_view.html](../../../cod_doc/templates/web/_frag/section_view.html)
to render the old state.

### CW-HI-4. Project-wide `audit` is missing from the Web

**Where:** [cod_doc/cli/cmd_audit.py](../../../cod_doc/cli/cmd_audit.py) — 297 LOC,
runs frontmatter-checks (FM-001..FM-005) + drift-checks (DR-*) across the
whole project.

**Symptom:** the memory `validation_pattern.md`
pins advisory-audit as part of the write-path, but **batch-run from the Web
is missing**. Project health-check = terminal.

**What is needed:** `GET /p/{slug}/audit` page: audit run (lazy, via
HTMX `hx-trigger="load"`), a table of issues by severity (FM/DR codes), a
filter by type. The endpoint calls `audit_service.run_project_audit` (if
there is no such thing — extract it from
[cmd_audit.py](../../../cod_doc/cli/cmd_audit.py)).

### CW-HI-5. `task create` — no task creation form in the Web

**Where:** [cod_doc/cli/task.py:191-289](../../../cod_doc/cli/task.py).

```bash
cod-doc task create -p P --plan PLAN --section SEC --title T \
  --type feature --priority high [--depends-on ...] [--effort ...]
```

**Symptom:** the Web covers the entire task life cycle, **except creation**:
there is list, show, status (HTMX), complete, edit description/acceptance,
AI-improve. But there is no "New task" button. Creating tasks by hand = CLI
or MCP only. AI task generation from a story exists
(`POST /stories/{id}/tasks/generate`), but that is a special flow, not a
general form.

**What is needed:** `GET /p/{slug}/plans/{plan_id}/tasks/new` (a form with
section, type, priority, dependencies selection via autocomplete), `POST`
to the same URL → redirect to `/p/{slug}/tasks/{new_id}`.

---

## 2. Medium

### CW-ME-1. `plan audit`, `plan critical-path`, `plan forward/reverse` — not in the Web

**Where:** [cod_doc/cli/plan/cmd_audit.py](../../../cod_doc/cli/plan/cmd_audit.py),
[cmd_critical_path.py](../../../cod_doc/cli/plan/cmd_critical_path.py),
[cmd_chain.py](../../../cod_doc/cli/plan/cmd_chain.py).

| CLI | Web on `plan_show.html` |
|---|---|
| `plan audit PLAN` | ❌ (no issues tab) |
| `plan critical-path PLAN` | ❌ (mermaid is drawn, but the critical path is not highlighted) |
| `plan forward TASK` | ❌ (task_show has read-only chains) |
| `plan reverse TASK` | partial — chains are rendered in task_show |

**Symptom:** the Web shows progress + next-batch + a mermaid graph, but **no
analytical operations**. It is impossible to see: "which tasks block closing
the plan", "what is the length of the critical path", "how many tasks
finishing this one will unblock". In the CLI — three commands.

**What is needed:** on [plan_show.html](../../../cod_doc/templates/web/project/plan_show.html)
add:
- an "Audit" tab (HTMX-fragment with issues),
- critical-path highlighting on the mermaid (a separate CSS-class on edges),
- links to chain-view in task_show (it already exists — only needs to be
  labeled).

### CW-ME-2. `plan export` — markdown projections are not available in the Web

**Where:** [cod_doc/cli/plan/cmd_export.py](../../../cod_doc/cli/plan/cmd_export.py).

```bash
cod-doc plan export PLAN -p P [--section progress_overview|next_batch|...]
```

**Symptom:** the CLI generates markdown projections for inserting into
roadmap documents. The Web has no such buttons → stakeholder report =
terminal.

**What is needed:** `GET /p/{slug}/plans/{plan_id}/export?section=...` →
response `text/markdown` + a "Copy to clipboard" button in `plan_show.html`.

### CW-ME-3. `doc drift` — no check for divergence between the projection and the DB

**Where:** [cod_doc/cli/doc/cmd_drift.py](../../../cod_doc/cli/doc/cmd_drift.py).

**Symptom:** drift-detection is part of the memory `validation_pattern.md`
(FM-004/FM-005 advisory). From the Web you cannot tell whether the on-disk
file diverges from the DB projection. This is especially dangerous after
manual markdown edits from outside.

**What is needed:** an "out of sync" badge next to the doc_key on
[doc_show.html](../../../cod_doc/templates/web/project/doc_show.html) +
a "Re-export from DB" button (`doc_service.export`). One endpoint:
`POST /p/{slug}/docs/{doc_key:path}/export`.

### CW-ME-4. `doc export` — the "materialize to disk" button is missing

**Where:** [cod_doc/cli/doc/cmd_export.py](../../../cod_doc/cli/doc/cmd_export.py).

**Symptom:** related to CW-ME-3. Scenario: the operator edited sections
through the UI (WEB-012) and wants the up-to-date markdown file on disk.
Currently — only `cod-doc doc export DOC -p PROJ`.

**What is needed:** merge with CW-ME-3 into a single task.

### CW-ME-5. `story status`, `story add-criterion`, `story link`, `story coverage` — missing

**Where:** [cod_doc/cli/story/](../../../cod_doc/cli/story/).

| CLI | Web |
|---|---|
| `story create` (manual) | ❌ (only AI-generate) |
| `story status SID NEW` | ❌ |
| `story add-criterion SID TEXT` | ❌ |
| `story link SID --to-task/--to-doc REF` | ❌ |
| `story coverage SID` | ❌ |

**Symptom:** the stories page implements AI generation (`/stories/generate` +
`/stories/save`) and auto-generation of tasks (`/stories/{id}/tasks/generate` +
`/tasks/save`). But beyond that — read-only. Transitioning a story to
`accepted`, adding an acceptance criterion by hand, linking to an existing
task, viewing coverage — all impossible. The story page has turned into a
one-shot draft tool.

**What is needed:** on [story_show.html](../../../cod_doc/templates/web/project/story_show.html)
add:
- inline-edit for status (HTMX dropdown by analogy with task_status),
- an "Add criterion" form (by analogy with section patch),
- a "Link to task/doc" form (autocomplete by entity-id),
- a "Coverage" block (aggregate + bar) under the header.

### CW-ME-6. Onboarding: `import docs` / `import legacy-tasks` / `import all`

**Where:** [cod_doc/cli/cmd_import.py](../../../cod_doc/cli/cmd_import.py).

**Symptom:** the Web has:
- `POST /p/{slug}/docs/import` — uploading a single markdown file,
- `POST /p/{slug}/import_master/scan` + `/save` — a semi-automatic
  bootstrap from MASTER.md.

But **batch import of the whole project** (`import all PROJECT`) and
migration of legacy `.cod-doc/tasks.yaml` (`import legacy-tasks`) — **CLI
only**. [proposals/13-import-ux-redesign.md](../../../proposals/13-import-ux-redesign.md)
and [14-legacy-tasks-migration-ux.md](../../../proposals/14-legacy-tasks-migration-ux.md)
describe the target UX, but it is not implemented.

**What is needed:** a wizard on the new-project page (`/p/{slug}` for an
empty DB): step 1 "Scan repo for docs" (dry-run preview), step 2 "Import
legacy tasks" (if `.cod-doc/tasks.yaml` is found), step 3 "Confirm".
Endpoints — wrappers over `import_service.*`. Part is already decomposed
(see `import_master/scan`).

### CW-ME-7. `agent run PROJECT` ad-hoc — no UI trigger per project

**Where:** [cod_doc/cli/cmd_agent.py](../../../cod_doc/cli/cmd_agent.py),
the API already exists: [routes.py:133](../../../cod_doc/api/routes.py).

**Symptom:** in [index.html](../../../cod_doc/templates/web/index.html) there
is a global daemon-bar (start/stop from commit b07a97e), but **running the
agent on a specific project** from the UI is impossible — the endpoint
`POST /api/projects/{name}/run` exists, but there is no button. The
WebSocket log stream (`/ws/projects/{name}/run` in
[webhooks.py:135](../../../cod_doc/api/webhooks.py)) is also not wired to
the UI.

**What is needed:** on [show.html](../../../cod_doc/templates/web/project/show.html)
a "Run agent now" button + an HTMX console with an SSE/WS stream. This
overlaps with WEB-030 (`/run` SSE console) — the only remaining endpoint in
[capability §3](../capabilities/web-frontend.md#3-routes).

### CW-ME-8. `task` — log progress / set blocker / list blocked

**Where:** the methods exist in `task_service`, but not all are exposed in
the CLI:

| Service method | CLI | Web |
|---|---|---|
| `update_status`, `complete` | ✅ | ✅ |
| `update_description`, `update_acceptance` | ❌ (no CLI) | ✅ (fields/edit) |
| `set_blocker`, `clear_blocker` | ❌ (no CLI) | ❌ |
| `log_progress` | ❌ (no CLI) | ❌ |
| `list_blocked`, `list_stale_in_progress` | ❌ (no CLI) | ❌ |

**Symptom:** this is a rare case — the CLI **also lags** behind the service
layer. But since the methods already exist in the service and are tested,
it is more natural to close them in the Web right away (and leave the CLI
as an option for headless).

**What is needed:** a "Blocker" panel on task-show (set/clear with a
reason), an inline "Log progress" textarea (HTMX append to
`task.progress_log`), a global "Blocked tasks" page
(`/p/{slug}/tasks?status=blocked` is almost there already, but without UI
for clear). See also §3.

### CW-ME-9. `project status NAME [--json]` — the Web shows less data

**Where:** [cod_doc/cli/cmd_project.py:112](../../../cod_doc/cli/cmd_project.py).

**Symptom:** the CLI produces detailed YAML/JSON: counts by document types,
last_run, broken-refs count, daemon-status, "next actions" recommendations.
The Web dashboard (7 KPI cards) is only an aggregate. Reporting for a
stakeholder is again in the terminal.

**What is needed:** add a "Health & Recommendations" block on
[show.html](../../../cod_doc/templates/web/project/show.html), calling the
same service (`project_service.status_summary`, if there is none — extract
it from [cmd_project.py:112-189](../../../cod_doc/cli/cmd_project.py)).

---

## 3. Low

### CW-LO-1. `project add` / `project remove` — registry management only in the CLI

**Where:** [cod_doc/cli/cmd_project.py:59-95](../../../cod_doc/cli/cmd_project.py).

Registering and removing a project from the global `~/.cod-doc/config.yaml`
is only done from the terminal. From `/settings` you can only edit the
API-key and daemon-flags, but not "register a new project".

**What is needed:** on `/settings` a "Register project" form (path, name,
master.md). `project remove` — a confirm-modal. Risk: both operators are
sensitive (they can clobber a record), an explicit confirmation is needed.

### CW-LO-2. `hash calc FILE` / `hash update` — not needed in the UI?

**Where:** [cod_doc/cli/cmd_hash.py](../../../cod_doc/cli/cmd_hash.py).

`hash calc` — a low-level utility for debugging hybrid links
(SHA-256). A Web analog is not needed (curl-level). `hash update` — a batch
recompute of hashes in MASTER.md, a rare operation.

**What is needed:** **deferred.** Record in this audit as "consciously left
out of the Web" and do not create a task. If needed — a "Recalculate
hashes" button in settings.

### CW-LO-3. `task list-blocked` / `task stale` — without a dedicated page

See CW-ME-8. Can be implemented as a filter on the existing
`/p/{slug}/tasks?status=blocked` + a new `?stale=1`. Does not require a
separate task, closed in CW-ME-8.

### CW-LO-4. `cli serve` / `mcp` / `tui` / `wizard` — intentionally not in the UI

CLI-only by design: `serve` brings up the API (on which the Web itself
lives), `mcp` — the MCP-server for Claude, `tui` — the Rich terminal,
`wizard` — initial setup for an empty `~/.cod-doc/config.yaml`. Not counted
as debt.

---

## 4. Summary matrix

| Domain | CLI cmds | Web (RW) | Web (RO) | Coverage |
|---|---:|---:|---:|---:|
| **docs** | 8 (`create`, `body`, `drift`, `export`, `import`, `list`, `rename`, `show`) | 5 (create, accept, generate-AI, import, sections-patch) | 2 (list, show) | **5/8 (62 %)** — no rename, drift, export |
| **tasks** | 5 (`list`, `show`, `create`, `status`, `complete`) | 4 (status, complete, fields edit, fields improve) | 2 (list, show) | **4/5 (80 %)** — no create form |
| **plans** | 7 (`show`, `ready`, `audit`, `critical-path`, `forward`, `reverse`, `export`) | 1 (freeze) | 2 (list, show) | **3/7 (43 %)** — no audit/critical-path/forward/reverse/export |
| **stories** | 7 (`list`, `show`, `create`, `status`, `add-criterion`, `link`, `coverage`) | 2 (generate-AI, tasks/generate) | 2 (list, show) | **2/7 (29 %)** — no status/criterion/link/coverage/manual-create |
| **revisions** | 3 (`list`, `show`, `revert`) | 0 | 1 (list) | **1/3 (33 %)** — no show, revert |
| **link** | 4 (`list`, `sync`, `verify`, `backfill`) | 0 | 0 | **0/4 (0 %)** — missing entirely |
| **project** | 5 (`list`, `add`, `remove`, `init`, `status`) | 2 (init, settings RW) | 1 (status overview) | **3/5 (60 %)** — no add/remove |
| **hash** | 2 (`calc`, `update`) | 0 | 0 | **0/2 (0 %)** — deferred (CW-LO-2) |
| **audit** | 1 (`audit`) | 0 | 0 | **0/1 (0 %)** |
| **import** | 3 (`docs`, `legacy-tasks`, `all`) | 1 (single-file `docs/import` + `import_master`) | 0 | **1/3 (33 %)** |
| **agent** | 1 (`run`) | daemon start/stop (global) | — | **partial** (no ad-hoc per-project) |

**Total by operations:** the Web covers roughly **52 %** of the actions
available through the CLI in the matrix (46 operations without
`serve`/`mcp`/`tui`/`wizard`, which intentionally remain CLI-only — see
CW-LO-4). The full CLI set is 50 commands.

---

## 5. Top-10 priority gaps

| # | Gap | Severity | Trigger |
|---|---|---|---|
| 1 | `link` domain in the Web (list/sync/verify) | high | Broken links are invisible from the UI |
| 2 | `doc rename` cascade | high | Refactoring the docs hierarchy only from the CLI |
| 3 | `revision show` + `revert` | high | The audit tab = read-only log |
| 4 | Project-wide `audit` page | high | Project health-check = terminal |
| 5 | `task create` form | high | The Web covers the entire lifecycle, except creation |
| 6 | Plan analytics (audit/critical-path/chains) | medium | Plan analytics only from the CLI |
| 7 | Story write-ops (status/criterion/link/coverage) | medium | The stories page has turned into draft-only |
| 8 | `doc drift` + `doc export` (merge) | medium | Drift between the DB and disk is not visible |
| 9 | Onboarding wizard (`import all` + legacy) | medium | First impression of the UI = empty screens |
| 10 | `agent run` per-project (WEB-030 SSE) | medium | A global daemon exists, no per-project trigger |

---

## 6. Methodology

1. CLI commands collected via `grep -rnE "@.*\.command\(" cod_doc/cli/` →
   **50 entry points** in [cod_doc/cli/](../../../cod_doc/cli/) (distribution
   by domain — in the matrix §4).
2. Web routes collected via `grep -nE "^@router\." cod_doc/api/web/pages/*.py
   cod_doc/api/web/fragments/*.py` → **38 entry points** (including HTMX
   fragments for inline editing).
3. Each CLI command is classified: `parity` (a full analog exists),
   `partial` (a related one exists, but without covering the specific
   use-case), `missing` (no endpoint at all).
4. Severity:
   - **high** — the absence of the operation breaks the basic "work without
     a terminal" scenario, or a whole domain is invisible;
   - **medium** — the operation is available, but via the CLI; Web
     productivity is lower;
   - **low** — the operation is rare or intentionally left in the CLI.

## 7. What remained out of scope

- **MCP-only service methods** not exposed in the CLI (e.g.
  `task_service.set_blocker`). This audit compares CLI ↔ Web, not
  service-coverage. A service-coverage audit is needed separately.
- **TUI** ([cod_doc/tui/](../../../cod_doc/tui/)) — this is an alternative
  front-end, not CLI commands. Not compared.
- **Visual polish / accessibility** of the Web — covered in
  [audit/2026-05-02-section-web-frontend.md](2026-05-02-section-web-frontend.md).

## 8. Changelog

| Date | Event |
|---|---|
| 2026-05-06 | Audit conducted. 18 findings (5 high, 9 medium, 4 low). Top-10 prioritized. No tasks were created in the roadmap — this is the initial recording of debt. |
