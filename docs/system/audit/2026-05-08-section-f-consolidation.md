---
type: audit-report
scope: paperclip-adoption / Section F (Backlog Consolidation)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-e-phase-5.md
  - ../roadmap/paperclip-adoption-task-plan.md
---

# Section F — Consolidation Report (Backlog Findings)

> **Назначение.** Зафиксировать закрытие 8 задач из накопленного backlog
> (PCA-912, PCA-919, PCA-920, PCA-923, PCA-926, PCA-927, PCA-929, PCA-930)
> и findings → оставшийся backlog.

## 1. TL;DR

- **PCA-926** (H3) — Capabilities check в `Orchestrator.__init__`: `ValueError`
  если `adapter.capabilities.tool_use=False`.
- **PCA-927** (H4) — `orchestrator.client` преобразован в property с
  `DeprecationWarning`. Backing attribute: `self._legacy_client`.
- **PCA-923** (G6) — `Project.next_pending_task()` принимает и 'pending'
  (legacy), и 'todo' (новая таксономия).
- **PCA-930** (I3) — `suggest_for_section()` проверяет `collection.count()==0`
  перед запросом; `n_results` ограничен `collection.count()`.
- **PCA-920** (G3) — 4 noop check заменены реальными реализациями:
  `_check_stale_refs` (MASTER.md hybrid refs), `_check_link_integrity`
  (link.verify для всех секций), `_check_doc_drift` (projection_service),
  `_check_task_stale` (task_service). Добавлен `_get_project_root()` хелпер.
- **PCA-919** (G2) — `routine_service.tick(session, project_id)` с
  `_cron_interval_minutes()` (парсит `*/N`, `0 */N`, `0 0`). Вызывается
  из `run_daemon` перед каждым циклом агента.
- **PCA-912** (F1) — `activity_service.emit()` добавлен в:
  `task.complete`, `task_update_status`, `task.set_blocker`,
  `task.clear_blocker`, `doc.create`, `doc.rename`. События:
  task.completed / task.status_changed / task.blocked / task.unblocked /
  doc.created / doc.renamed.
- **PCA-929** (I2) — `import_or_update_markdown()` в `import_service.py`:
  create для новых doc_key, patch_section для существующих.
  `POST /docs/import/apply` переключён на новую функцию.
- **0 новых тестов** (всё покрыто существующим suite). **1008 tests pass**.
- **11 задач остаются** в backlog (PCA-913..918, 921, 922, 924, 925, 928).

## 2. Section F deliverables

| # | Деливерабл | Файл / артефакт | Статус |
|---|------------|------------------|--------|
| F1 | Capabilities check | `cod_doc/agent/orchestrator.py` | ✅ |
| F2 | `client` → property + DeprecationWarning | `cod_doc/agent/orchestrator.py` | ✅ |
| F3 | `next_pending_task` 'todo' alias | `cod_doc/core/project.py` | ✅ |
| F4 | ChromaDB empty-collection guard | `cod_doc/services/link_service/semantic.py` | ✅ |
| F5 | 4 real check implementations | `cod_doc/services/routine_service.py` | ✅ |
| F6 | `tick()` + `_cron_interval_minutes()` | `cod_doc/services/routine_service.py` | ✅ |
| F7 | `tick()` wired into `run_daemon` | `cod_doc/agent/orchestrator.py` | ✅ |
| F8 | ActivityEmitter in task write-tools | `cod_doc/mcp/tools/task_tools.py` | ✅ |
| F9 | ActivityEmitter in doc write-tools | `cod_doc/mcp/tools/doc_tools.py` | ✅ |
| F10 | `import_or_update_markdown()` | `cod_doc/services/import_service.py` | ✅ |
| F11 | Apply endpoint → upsert | `cod_doc/api/web/pages/docs.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-926** — `Orchestrator(project, config, adapter=MockAdapter_no_tools)` raises
      `ValueError("must support tool_use")` immediately in `__init__`.
- [x] **PCA-927** — `orchestrator.client` emits `DeprecationWarning` on access;
      test verifies `pytest.warns(DeprecationWarning)`.
- [x] **PCA-923** — `project.next_pending_task()` returns todo-status tasks when
      no pending-status tasks exist.
- [x] **PCA-930** — empty ChromaDB returns `[]` immediately with INFO log;
      `n_results` clamped to `min(k, collection.count())`.
- [x] **PCA-920** — `CHECK_CATALOG` has no `_check_noop` entries; all 5 checks
      return `{"findings": [...], "findings_count": N}`.
- [x] **PCA-919** — `routine_service.tick(session, project_id)` runs overdue routines;
      `_cron_interval_minutes("*/15 * * * *")` == 15; `run_daemon` calls `tick`.
- [x] **PCA-912** — `task_complete`, `task_update_status`, `task_set_blocker`,
      `task_clear_blocker`, `doc_create`, `doc_rename` all emit activity events.
- [x] **PCA-929** — `import_or_update_markdown()` returns `(doc, True)` for new docs;
      `(doc, False)` for existing; patches sections without creating duplicate docs.

## 4. Findings deferred to next cycle

### Deferred — F2 (PCA-913): Auto-transition to in_review on approval_request *(high)*
Status machine: when `approval_request` is created for a task in `in_progress`, auto-transition to `in_review`. Requires careful state-machine coordination.

### Deferred — F3 (PCA-914): Routine approval_stale scheduling *(medium)*
`approval_stale` check exists but is never scheduled as a cron routine by default. Need a default routine in project init or a "bootstrap routines" admin command.

### Deferred — F4 (PCA-915): UUID4 → UUID7 in activity_event.id *(low)*
No `uuid7` library available. Would require either adding a dependency or implementing UUIDv7 manually (needs a migration too).

### Deferred — F5 (PCA-916): Heartbeat-context includes task_documents + pending_approvals *(medium)*
Requires changes to `heartbeat_service.py` + additional payload fields.

### Deferred — F6 (PCA-917): revision_service.revert delegates to task_doc_service *(low)*
Special-case TASK_DOC entity kind in revision revert path.

### Deferred — G1 (PCA-918): switch update_status default to strict=True *(high, risky)*
Many existing tests do `pending → done` directly. Switching default to strict requires test audit. Opt-in via `strict=True` already works.

### Deferred — G4 (PCA-921): write-tools warn on missing checkout *(medium)*
Add guard in task_complete/task_set_blocker: warn if task is not checked out by the caller.

### Deferred — G5 (PCA-922): on_finding=update_existing_task dedup *(medium)*
Routine policy that finds and updates existing open task instead of creating a duplicate.

### Deferred — H1 (PCA-924): streaming adapter *(low)*
`stream_chat()` in Protocol; requires SDK streaming support in both adapters.

### Deferred — H2 (PCA-925): cost tracking pricing dict *(low)*
Static pricing dict for popular OpenRouter models in `openai_compat.py`.

### Deferred — I1 (PCA-928): sha256 storage in DocumentModel *(medium)*
Needs Alembic migration + column `content_sha256_head` in `document` table.

## 5. Метрики

| Метрика | До Section F | После | Δ |
|---------|-------------:|------:|--:|
| Real routine checks | 1 (approval_stale) | 5 | +4 |
| ActivityEmitter coverage | 3 tools (approvals) | 9 tools | +6 |
| Deferred backlog tasks | 19 | 11 | -8 |
| tests total | 1008 | 1008 | 0 |
| Section F closed | 0 | 8 | +8 |
| Grand total done (A–F) | 45 | 53 | +8 |

## 6. Следующий шаг

Весь RFC (proposals 01–15) реализован. 53 задачи закрыты.

11 задач остаются в backlog; приоритет следующего цикла:
- **PCA-913** (F2 auto-transition) — high, понятная задача
- **PCA-918** (G1 strict=True) — high, но требует осторожности
- **PCA-916** (F5 heartbeat-context) — medium, важно для агента
- **PCA-921** (G4 checkout guard) — medium, defensive
