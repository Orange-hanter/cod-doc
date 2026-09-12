---
status: implemented
type: ux-proposal
author: human:dakh
date: 2026-05-06
scope: web-ui · legacy-tasks · migration
related:
  - cod_doc/api/web/pages/tasks.py
  - cod_doc/templates/web/project/tasks_list.html
  - cod_doc/templates/web/project/tasks_legacy_list.html
  - cod_doc/cli/cmd_import.py
  - cod_doc/services/restate_importer.py
  - cod_doc/core/project.py
  - cod_doc/mcp/tools/task_tools.py
---

# Proposal 14 · Migration of legacy tasks from YAML to DB via UI

> 🎯 Goal: close the "two-layer" task storage. Today the same
> projects keep tasks both in `.cod-doc/tasks.yaml` (old YAML) and in
> the DB table `task` (COD-032). Import already exists, but only in CLI — UI
> only shows "Legacy YAML tasks (N), not yet migrated" and offers nothing
> to click. Need one "Import to DB" button + rules that prevent YAML tasks
> from reappearing.

## 1. What is wrong now

### 1.1. Symptoms

[`tasks_list.html:10-15`](cod_doc/templates/web/project/tasks_list.html#L10-L15) and
[`tasks_legacy_list.html:15-18`](cod_doc/templates/web/project/tasks_legacy_list.html#L15-L18):

| Symptom | Cause |
| --- | --- |
| On `/p/{slug}/tasks` it says "There are no tasks in this project yet", and at the top hangs a link "Legacy YAML tasks (34)" | The page reads only DB; legacy is a parallel world in `tasks.yaml` |
| To migrate 34 tasks to DB, you need to open a terminal and run `cod-doc import legacy-tasks <slug>` | UI does not know about this command at all — neither an endpoint nor a button |
| The "not yet migrated" banner hangs even after the user has already checked everything and believes no migration is needed | There is no "decided not to migrate" state; no dry-run, no diff |
| `add_task` / `update_task` via legacy MCP wrote to YAML (`legacy_project_tools.py`, removed in `c310503`) | The old tools were registered next to the new ones ([`task_tools.py`](cod_doc/mcp/tools/task_tools.py)) and were not marked deprecated → agents sometimes picked legacy |
| The legacy page shows only id/title/status/priority/updated, without description/result | A read-only preview without full content — the user does not see what exactly is migrated |

### 1.2. Why it happens

Timeline (per `git log` and architectural marks):

1. **Before COD-032** — the only task storage was `tasks.yaml`.
   The [`Project`](cod_doc/core/project.py#L120-L207) class and legacy MCP-tools
   (`add_task`, `update_task`, `next_pending_task`) work with it directly.
   All 37 current records in `.cod-doc/tasks.yaml` are heritage of this period.
2. **COD-032+** — a relational schema was introduced (`task` + `plan` +
   `plan_section` + revisions). In parallel `task_tools.py`,
   web-UI `/tasks`, the agent pipeline (`agent/orchestrator.py`) appeared.
   The old code **was not removed** — it kept serving existing flows
   so as not to break familiar scenarios.
3. **COD-051** — added `cod-doc import legacy-tasks` for bulk transfer
   ([`cmd_import.py:84-117`](cod_doc/cli/cmd_import.py#L84-L117) +
   [`restate_importer.py:242-335`](cod_doc/services/restate_importer.py#L242-L335)).
   This removed the urgency of migration, but created a stable "swamp":
   import exists → no reason to delete YAML, YAML exists → legacy tools keep
   writing to it. UI reflected the swamp as a separate tab.
4. **Now** — in the `cod-doc` repo itself: 37 tasks in YAML, 0 in DB
   (for the GatewayDemo project on the screenshot). And so it is for almost
   all projects started before COD-051.

The root cause is one: **import is implemented, but not presented to the user
as a "normal" action**. While the button is not clickable from the browser,
it is not clicked at all.

## 2. Proposed model

### 2.1. One cycle: Preview → Import → Freeze

On the `/p/{slug}/tasks/legacy` page add an action block **at the top**
of the table:

```
[ 🔍 Preview import ]   [ 📥 Import all (N) ]   [ ❄ Mark as archived ]
```

- **Preview import** (dry-run) — opens a diff-modal: "N tasks will be
  created in plan `imported-legacy`, K of them already look like DB-tasks
  (by title hash) → mark as duplicates". No changes to DB.
- **Import all** — no flags, no subset selection. Imports the entire YAML
  content in one transaction, shows a summary
  (`imported / skipped / errors`). On success — the button turns into
  "✓ Imported on YYYY-MM-DD HH:MM".
- **Mark as archived** — for the case "no need to migrate, leave as is".
  Simply renames `tasks.yaml` → `tasks.archived.yaml` and removes the
  banner from `/tasks`.

No partial imports, no checkbox selection — a bulk data transfer, not a
daily workflow. If something goes wrong — `git revert` the DB-migration
(revisions already support this via the `restate-import:*` reason).

### 2.2. HTTP endpoints

Add to [`api/web/pages/tasks.py`](cod_doc/api/web/pages/tasks.py):

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/p/{slug}/tasks/legacy/import?dry_run=1` | Returns an HTML fragment with the diff (import plan). No write. |
| `POST` | `/p/{slug}/tasks/legacy/import` | Real import. Returns an HTML fragment with a summary + a link "Open imported plan". |
| `POST` | `/p/{slug}/tasks/legacy/archive` | Renames yaml. Idempotent. |

All three are htmx-endpoints (HTML, not JSON). Protection: `X-CSRF-Token` or
`SameSite` cookie — check what is used on other POST-forms
(e.g. on `/p/{slug}/daemon/start`).

Implementation exactly over the already existing
[`restate_importer.import_legacy_tasks`](cod_doc/services/restate_importer.py#L242-L335):
for dry-run — `session.rollback()` and serialize `summary`; for
archive — `yaml_path.rename(yaml_path.with_suffix(".archived.yaml"))`.

### 2.3. The "migrated" state as a fact, not a flag

After a successful import:

- On `/p/{slug}/tasks` the banner "Legacy YAML tasks (34) — not
  yet migrated" changes to "📦 Legacy YAML tasks (34) — imported
  YYYY-MM-DD into plan `imported-legacy`". This must be read from
  the DB (the plan with scope `imported-legacy` and the count of tasks in it),
  not stored as a separate flag.
- On `/p/{slug}/tasks/legacy` at the top appears a block
  "✓ Already imported N → M; a repeated import will create duplicates". The
  "Import all" button becomes secondary + requires confirmation.

### 2.4. Close the write path to YAML

This is critical — otherwise after import the gap will accumulate again.

1. In legacy `legacy_project_tools.py` (removed in `c310503`)
   change the docstring of `add_task` / `update_task` to
   `"DEPRECATED — use mcp__cod-doc__task_create instead"`.
2. At the implementation level `Project.add_task` ([`core/project.py:188-192`](cod_doc/core/project.py#L188-L192))
   when `tasks.yaml` is already archived → raise `RuntimeError("legacy
   tasks.yaml archived; use DB-backed task_create")`. Not "silently write
   to a new YAML" — this guarantees that archiving = final.
3. After closing the first batch of projects (3-4) — drop the write
   methods `Project.add_task / update_task` entirely, leave only the
   read-side (`get_tasks`, `_load_tasks`) for the legacy page.

### 2.5. Extended preview on the legacy page

Now [`tasks_legacy_list.html:48-69`](cod_doc/templates/web/project/tasks_legacy_list.html#L48-L69)
shows 5 columns. Before "click import" the user must see what exactly is
migrated. Minimum:

- An expandable `<details>` on each row: description + result.
- A "target plan-section" column — for clarity that everything lands in
  one synthetic plan (`imported-legacy / Imported (legacy)`).

## 3. What we do not do

- **Two-way sync YAML↔DB.** That is desynchronization, not migration.
  YAML is the source for a one-time import, then read-only → archive.
- **A YAML editor in UI.** If a task needs editing — migrate the project
  to DB and edit there.
- **Auto-import on first page visit.** The user must explicitly click the
  button — they may have valid reasons not to migrate
  (e.g. an experimental project slated for deletion).
- **Selectable rows / partial import.** This is a one-time operation; a UX
  with checkboxes increases the bug surface, and the value is zero.

## 4. Work plan

| Step | Task | Files |
| --- | --- | --- |
| 1 | `POST /tasks/legacy/import` (htmx-endpoint + dry-run) | `api/web/pages/tasks.py`, `templates/.../tasks_legacy_list.html` |
| 2 | `POST /tasks/legacy/archive` | same |
| 3 | Update `tasks_list.html` banner: "imported / not yet imported / archived" by DB state and `tasks.archived.yaml` | `templates/.../tasks_list.html`, `api/web/pages/tasks.py:tasks_list` |
| 4 | Expandable preview of description/result on the legacy page | `templates/.../tasks_legacy_list.html` |
| 5 | Mark `add_task`/`update_task` MCP deprecated, raise `RuntimeError` on write in archived state | historical `mcp/tools/legacy_project_tools.py`, `core/project.py` |
| 6 | Test: legacy → import → repeat-import = no-op (via duplicate detection in `task_service.create`) | `tests/web/test_tasks_page.py`, `tests/services/test_restate_importer.py` |
| 7 | After 3-4 successful migrations — remove legacy write methods entirely | `core/project.py`, historical `mcp/tools/legacy_project_tools.py` |

Steps 1-4 — one task (`COD-XXX: legacy tasks UI import`). Steps 5-7 —
a separate one right after, so as not to mix UX and deprecation in one PR.

## 5. Risks and countermeasures

| Risk | Countermeasure |
| --- | --- |
| Repeated Import click → duplicates in DB | `restate_importer` already uses `task_service.create(allow_duplicate=True, reason="restate-import:<id>")`; needs to change to `allow_duplicate=False` + skip-by-title. After YAML archiving a repeat run is physically impossible |
| Archiving deletes data that may still be needed | `tasks.yaml → tasks.archived.yaml` — a rename, not a delete; in `git history` the file is still there |
| User clicked Import, but the pipeline dropped half the tasks | `import_legacy_tasks` already uses savepoint per-row (COD-071), partial success is visible in summary; errors are logged with the title key |
| An MCP agent in the background still writes to YAML in parallel with the import | Recommendation: run the import with the daemon stopped (the new UI stop/start button already exists — `b07a97e`); long-term — RuntimeError on write after archive |

## 6. Acceptance criteria

- On `/p/{slug}/tasks/legacy` there is an "📥 Import all" button, by
  which in one click 37 tasks from YAML appear in the DB with the plan
  `imported-legacy`.
- A repeated click does not create duplicates.
- After a successful import the user can go to `/p/{slug}/tasks`
  and see the imported tasks in the common list.
- After "Mark as archived" the legacy banner disappears from `/tasks`; the
  `/tasks/legacy` page returns 404 or "archived 2026-…".
- `add_task` via legacy MCP on an archived project fails with
  a clear message, not writes to a new file.
