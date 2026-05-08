---
type: audit-report
scope: paperclip-adoption / Section B (Phase 2 — Audit infra)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-section-f-closure.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/05-issue-documents.md
  - ../../../proposals/09-activity-log.md
  - ../../../proposals/12-approvals.md
---

# Section B — Closure Report (Phase 2: Audit infra)

> **Назначение.** Зафиксировать закрытие 6 задач Section B плана
> `paperclip-adoption-task-plan` (PCA-100, PCA-101, PCA-110, PCA-111,
> PCA-120, PCA-121) и оформить findings → backlog для Phase 3 / Section F.

## 1. TL;DR

- **PCA-100/101** — `task_document` table + `TaskDocumentService` + 5 MCP-тулов
  (`task_doc_get/put/list/revisions/revert`). Optimistic lock через
  `base_revision_id`, snapshot revisions в shared `revision`-таблице
  (`entity_kind='task_doc'`).
- **PCA-110/111** — `activity_event` table + `ActivityEmitter` + 2 MCP-тулa
  (`activity_list`, `activity_for_run`). `run_id` подбирается из contextvar.
- **PCA-120/121** — `approval` + `approval_task_link` + `approval_doc_revision_link`
  tables + `ApprovalService` + 5 MCP-тулов (`approval_request/list/get/resolve/cancel`).
  Resolve возвращает `wake_hint` для `run_agent_once`. Single-pending-per-task
  invariant с auto-cancel на supersede.
- **Тестовое покрытие:** 43 новых unit-теста (13 + 12 + 18). Полный suite —
  929 passed (было 886).
- **6 findings** (F1-F6) → backlog в Phase 3 / Section F.

## 2. Section B deliverables

| # | Деливерабл | Файл / артефакт | Статус |
|---|------------|------------------|--------|
| D1 | Section B audit-report | `docs/system/audit/2026-05-08-section-b-phase-2.md` | ✅ this file |
| D2 | Migration `0011_task_documents` | `cod_doc/infra/migrations/versions/20260508_0011_task_documents.py` | ✅ |
| D3 | Migration `0012_activity_events` | `cod_doc/infra/migrations/versions/20260508_0012_activity_events.py` | ✅ |
| D4 | Migration `0013_approvals` | `cod_doc/infra/migrations/versions/20260508_0013_approvals.py` | ✅ |
| D5 | Models | `cod_doc/infra/models/{task_docs,activity,approvals}.py` | ✅ |
| D6 | Services | `cod_doc/services/{task_doc_service,activity_service,approval_service}.py` | ✅ |
| D7 | MCP tools | `cod_doc/mcp/tools/{task_doc_tools,activity_tools,approval_tools}.py` | ✅ |
| D8 | `EntityKind.TASK_DOC` enum | `cod_doc/domain/entities.py` | ✅ |
| D9 | Server registration | `cod_doc/mcp/server.py` (3 new modules) | ✅ |
| D10 | Tests (43) | `tests/services/test_{task_doc,activity,approval}_service.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-100 (migration)** — `task_document` table с UNIQUE(task_id, key),
      cascade FK на `task.row_id`, индекс на `task_id`. Up/down работают.
- [x] **PCA-101 (feature)** — `task_doc_get/put/list/revisions/revert`
      зарегистрированы; optimistic lock бросает `TaskDocConflictError`;
      revisions сохраняются с `entity_kind='task_doc'`; `run_id` стампится
      из contextvar.
- [x] **PCA-110 (migration)** — `activity_event` table с 5 индексами
      (`ts`, `(scope_kind, scope_id, ts)`, `run_id`, `(project_id, ts)`,
      `(kind, ts)`). Append-only; up/down работают.
- [x] **PCA-111 (feature)** — `ActivityEmitter.emit()` готов; `activity_list`
      с фильтрами по scope/kind/actor/since/until + пагинация; `activity_for_run`
      возвращает события одного run'а oldest-first.
- [x] **PCA-120 (migration)** — `approval` + `approval_task_link` +
      `approval_doc_revision_link` tables; 4 индекса. Up/down работают.
- [x] **PCA-121 (feature)** — `approval_request/list/get/resolve/cancel`
      зарегистрированы; single-pending-per-task invariant с auto-cancel;
      `wake_hint` возвращается при resolve; `activity.requested/resolved/cancelled`
      events эмитятся.

## 4. Findings (→ backlog)

### F1 — ActivityEmitter не подключён к существующим write-тулам *(high)*

**Что сейчас:** `activity_service.emit()` вызывается **только** из
`approval_tools.py` (3 места). `task_tools.create/update_status/set_blocker/
clear_blocker`, `doc_tools.create/update/rename`, `task_doc_tools.put/revert`,
`run_context.start_/finalize_orchestrator_run` — **не пишут события**.

**Последствие:** `activity_list` возвращает только approval-события; UI
timeline и audit-цепочка из proposal 09 §1 (*"одной страницей UI ответить
что произошло сегодня"*) не работают. Phase 1 plan из proposal 09 §3
(*"задачи (создание, статус, блокеры, комменты)"*) → не выполнен.

**Рекомендация:** ввести middleware/декоратор в `cod_doc/mcp/tools/_db.py`
(или явные `activity.emit()` в каждом write-тулe) для kinds:
`task.created/status_changed/blocker_added/blocker_cleared`,
`doc.created/updated/renamed`, `task_doc.updated`,
`run.started/finished/failed`. Каждый — в одной транзакции с самой мутацией.

### F2 — `approval_request` не переводит linked tasks в `in_review` *(high)*

**Что сейчас:** `ApprovalService.request()` создаёт approval, линкует
`approval_task_link` rows, но **не трогает статус задач**. Acceptance
для PCA-121 в плане был *«approval_request переводит связанные tasks в
in_review»* — невыполнен.

**Корень:** наша TaskStatus enum пока 3-state (`pending | in-progress | done`).
Статуса `in_review` не существует до **PCA-220** (Section C, 7-state taxonomy).

**Рекомендация:** не делать частичный фикс в Section B. Зафиксировать
это как **зависимость PCA-121 ↔ PCA-220** в плане; auto-status включается
после PCA-220, единым PR.

### F3 — Нет expiry-routine для `expired` approvals *(medium)*

**Что сейчас:** `expires_at` записывается в БД, но никакой routine не
переводит просроченные approvals в `status='expired'`. Они навечно
остаются `pending`.

**Корень:** routines (cron) — это **PCA-211** (Section C), пока нет.

**Рекомендация:** после PCA-211 добавить routine `approval_stale` который
SELECT'ит `pending AND expires_at < now()` → `cancel(reason='expired')`
(или новый kind `expire`) + wake оператора.

### F4 — Activity `id` использует UUID4, не UUID7 *(low)*

**Что сейчас:** `activity_service._make_id()` генерит `uuid4()` с TODO-комментом
*"swap to UUID7 when available"*. Поле подразумевалось time-sortable
(proposal 09 §32), но `uuid4` сортируется лексикографически рандомно.

**Последствие:** `ORDER BY id` даёт wrong ordering; пагинация по id
ломается. Сейчас спасает `ORDER BY ts` + `row_id` tiebreak, но `id`
как cursor использовать нельзя.

**Рекомендация:** добавить `uuid7`/`uuid_extensions` в зависимости
проекта (pip), заменить `uuid4()` → `uuid7()`. Single-line fix +
bump dep.

### F5 — Heartbeat-context не включает `task_documents` и pending approvals *(medium)*

**Что сейчас:** `task_heartbeat_context(task_id)` возвращает task summary,
linked_docs, recent_changes — но **не упоминает task-bound docs и
approvals**. Proposal 05 §5 явно требует:
*«добавить срез task_documents: [{key, current_revision_id, summary}]»*.

**Последствие:** агент, разбуженный по `task_assigned`, не видит свои
plan/design/verification доки и не знает, что задача в pending approval.

**Рекомендация:** расширить `heartbeat_service.heartbeat_context()`:
- `task_documents`: `[{key, title, current_revision_id, last_updated}]`
- `pending_approvals`: `[{approval_id, type, requested_at, expires_at}]`
Бюджет heartbeat ≤ 4 KB соблюдается (по 1 строке на entry).

### F6 — `task_doc.revert` параллелен `revision_service.revert()` *(low)*

**Что сейчас:** общий `revision_service.revert(revision_id)` поддерживает
TASK / SECTION / DOCUMENT (см. `RevertNotSupportedError`). Для TASK_DOC
сделана отдельная функция `task_doc_service.revert()`, которая обходит
этот flow.

**Последствие:** `mcp.revision_revert(...)` для task_doc-revision вернёт
"not supported", хотя данные позволяют revert. Раздвоение surface'а.

**Рекомендация:** дописать `revision_service.revert()` ветку для
`EntityKind.TASK_DOC` — она просто делегирует в `task_doc_service.revert()`.
`task_doc_revert` MCP-тул оставить как удобный shortcut.

## 5. Метрики

| Метрика | До Section B | После | Δ |
|---------|-------------:|------:|--:|
| Tables в schema | 19 | 22 | +3 |
| Migrations | 10 | 13 | +3 |
| Service modules | ~25 | 28 | +3 |
| MCP write-tools | ~30 | 41 | +11 |
| `tests/` total | 886 | 929 | +43 |
| Section A+B done tasks | 22 | 28 | +6 |

## 6. Что не вошло (out of scope)

- **Web UI:** approval inbox / activity timeline / task-doc tabs —
  не реализованы. Web-страницы для трёх новых сущностей — отдельные
  задачи в Phase 3+.
- **CLI:** `cod-doc activity / approval / task_doc` команды — нет.
- **Search index:** `search_docs` не индексирует task_docs (proposal 05 Q1
  оставлен открытым).
- **Retention/archive** activity_event'ов (proposal 09 §95) — не реализован.
- **`activity_summary_daily`** агрегатор для dashboard'а — не реализован.

## 7. Следующий шаг

Section A (Phase 1) и Section B (Phase 2) **закрыты**. Section C
(Phase 3 — Extensions) разблокирована: PCA-200..230 (atomic checkout,
routines, 7-state TaskStatus taxonomy, AGENTS.md).

Рекомендуемый порядок Section C:
1. **PCA-220** (TaskStatus 7-state migration) — снимает блокер с F2
   (приведёт linked tasks в `in_review` на approval_request).
2. **PCA-221** (status transition rules) — закрывает enforcement.
3. **PCA-211** (scheduler runner) — снимает блокер с F3 (expiry routine).
4. Параллельно: F1 (activity emission в существующих write-тулах) —
   small, может пойти как Section F-task без зависимостей.

Findings F1-F6 заведены как отдельные задачи в плане
`paperclip-adoption-task-plan` (Section F: PCA-912..PCA-917) для
отслеживания.
