---
type: audit-report
scope: paperclip-adoption / Section C (Phase 3 — Extensions)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-b-phase-2.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/06-atomic-checkout.md
  - ../../../proposals/07-routines.md
  - ../../../proposals/08-status-taxonomy.md
  - ../../../proposals/11-agents-md.md
  - ../../../AGENTS.md
---

# Section C — Closure Report (Phase 3: Extensions)

> **Назначение.** Зафиксировать закрытие 7 задач Section C
> (PCA-200/201/210/211/220/221/230) и описать findings → backlog.

## 1. TL;DR

- **PCA-220 + 221** — TaskStatus 7-state taxonomy (proposal 08).
  Аддитивное расширение enum (legacy pending/in-progress/done сохранены).
  State-machine `task_status_machine.py` с `validate_transition` интегрирована
  в `task_service.update_status` в **warn-mode** (proposal 06 §89: Phase 1
  permissive, Phase 2 enforce). `strict=True` opt-in.
- **PCA-200 + 201** — атомарный checkout (proposal 06).
  Migration 0014 + `checkout_service.py` + 2 MCP-тула. `CheckoutConflictError`
  на 409, idempotent для same agent. Activity events на checkout/release.
- **PCA-210 + 211** — routines (proposal 07).
  Migration 0014 (объединено), `routine_service.py` + 7 MCP-тулов.
  Один реальный check (`approval_stale` — закрывает Section B finding F3),
  4 noop-плейсхолдера для daemon wiring.
- **PCA-230** — `AGENTS.md` (proposal 11). 12 разделов в корне +
  обновлённый PR-template (Model used + DoD).
- **Тестовое покрытие:** 41 новый unit-тест. Полный suite — **970 passed**
  (было 929).
- **6 findings** (G1-G6) → backlog в Section F (PCA-918..923).

## 2. Section C deliverables

| # | Деливерабл | Файл / артефакт | Статус |
|---|------------|------------------|--------|
| D1 | Section C audit-report | `docs/system/audit/2026-05-08-section-c-phase-3.md` | ✅ |
| D2 | Migration `0014_task_checkout_and_routine` | `cod_doc/infra/migrations/versions/20260508_0014_*.py` | ✅ |
| D3 | TaskStatus enum extension | `cod_doc/domain/entities.py` | ✅ |
| D4 | State machine | `cod_doc/services/task_status_machine.py` | ✅ |
| D5 | Checkout service + MCP | `cod_doc/services/checkout_service.py`, `mcp/tools/checkout_tools.py` | ✅ |
| D6 | Routine model + service + MCP | `cod_doc/infra/models/routines.py`, `services/routine_service.py`, `mcp/tools/routine_tools.py` | ✅ |
| D7 | TaskModel checkout fields | `cod_doc/infra/models/plans.py` | ✅ |
| D8 | AGENTS.md + PR template | `AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md` | ✅ |
| D9 | revert flow uses force=True | `cod_doc/services/revision_service.py` | ✅ |
| D10 | Server registration | `cod_doc/mcp/server.py` (2 new modules) | ✅ |
| D11 | Tests (41) | `tests/services/test_{task_status_machine,checkout,routine}_service.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-220 (migration)** — `TaskStatus` содержит canonical 7-state +
      legacy aliases; нет breaking changes для существующих task rows
      (хранятся as-is).
- [x] **PCA-221 (feature)** — `validate_transition` с матрицей; +1
      pragmatic deviation (in_progress → todo) задокументирована в коде.
      Интеграция в `update_status` через warn-mode по умолчанию.
- [x] **PCA-200 (feature)** — `task_checkout` и `task_release` MCP-тулы;
      idempotent для same agent; conflict raises с advisory «never retry»;
      `release_stale` для cleanup.
- [x] **PCA-201 (refactor)** — `update_status` принимает `via_checkout` /
      `strict` / `force`; revert flow wires force=True. Полное enforcement
      отложено в Phase 2 (см. F-задачу PCA-922).
- [x] **PCA-210 (migration)** — `routine` + `routine_run` tables со всеми
      полями proposal 07 (trigger / cron / on_finding / concurrency / catch_up);
      round-trip up→down→up работает.
- [x] **PCA-211 (feature)** — 7 MCP-тулов; `concurrency=skip` соблюдается;
      `routine.fired` / `routine.found_issue` events эмитятся; `approval_stale`
      check работает end-to-end (closes F3 from Section B audit).
- [x] **PCA-230 (docs)** — `AGENTS.md` 12 секций; PR-template содержит
      `Model used` и Definition of Done.

## 4. Findings (→ backlog)

### G1 — Checkout enforcement отложен (warn-mode по умолчанию) *(high)*

**Что сейчас:** `task_service.update_status` принимает любую невалидную
transition silently (warn-mode). Per proposal 06 §89 это правильно для
Phase 1, но Phase 2 enforcement не оформлен как задача.

**Последствие:** агент может делать `pending → done` или
`backlog → in_progress` обходя checkout, и state-machine не остановит.

**Рекомендация:** оформить Phase 2 enforcement как задачу. Она требует:
(а) грепа всех существующих callers `update_status`, (б) добавления
`strict=True` где безопасно, (в) переключение default'а после миграции.

### G2 — Scheduler daemon не реализован *(high)*

**Что сейчас:** `routine_service.run_now()` работает по запросу (manual
trigger). Cron-расписание (`routine.cron`, `trigger='cron'`) хранится в
БД, но никакого процесса, читающего эти строки и вызывающего `run_now`
по расписанию, нет.

**Последствие:** `approval_stale` (закрывающий F3) триггерится только
при ручном вызове `routine_run_now`. Аналогично `doc_drift` /
`task_stale` / `link_integrity` — ждут cron-loop.

**Рекомендация:** реализовать `cod_doc/services/routine_scheduler.py`
с `croniter` + tick-loop (или `apscheduler`). Запуск через CLI команду
`cod-doc routine daemon`. Вне scope этого Section C — отдельная F-задача.

### G3 — 4 из 5 check'ов в catalog — noop placeholders *(high)*

**Что сейчас:** `CHECK_CATALOG` содержит 5 имён, но только `approval_stale`
делает реальную работу. `stale_refs` / `link_integrity` / `doc_drift` /
`task_stale` возвращают `{"findings": [], "note": "noop"}`.

**Последствие:** routine с этими именами создаётся, но фактически
ничего не проверяет. Видимость нулевая.

**Рекомендация:** обернуть существующие MCP-тулы (`check_stale_refs`,
`link.verify`, `doc.drift`, `task.stale`) в check-функции. Каждая —
отдельная F-задача (4 small PRs).

### G4 — Write-tools (task_complete, task_set_blocker) не проверяют checkout *(medium)*

**Что сейчас:** PCA-201 acceptance говорит «все мутирующие task-тулы
требуют валидный активный checkout», но `task_complete`,
`task_set_blocker`, `task_clear_blocker`, `task_log_progress` пока
ничего не проверяют. Только `update_status` опционально валидирует
transition, не ownership.

**Рекомендация:** добавить helper `_warn_no_checkout(session, task_id,
agent)` в `cod_doc/mcp/tools/_db.py` + вызвать из 4 write-тулов
(warn-mode). Phase 2 — enforce.

### G5 — Routine `update_existing_task` policy не реализована *(medium)*

**Что сейчас:** `on_finding='create_task'` создаёт новую задачу при каждом
finding'е (вернее, создавал бы, если бы было кодирование — сейчас даже
этой ветки нет в `run_now`). Policy `update_existing_task` (нужна для
повторяющихся drift'ов на одних доках, чтобы не плодить дубли) —
proposal 07 §91 называет её must-have.

**Рекомендация:** реализовать signature-deduplication: hash от
`(check_name, scope_kind, scope_id, finding_kind)` → если открытая
задача с таким signature существует, добавить comment вместо новой
задачи. F-задача.

### G6 — `task_status_machine` не интегрирован в legacy `next_pending_task` *(low)*

**Что сейчас:** legacy MCP-тул `next_pending_task` возвращает task со
status='pending' (legacy строка). Новые callers ожидающие 'todo' могут
не находить задачи. `normalise()` не используется в legacy slice.

**Рекомендация:** обернуть SELECT в `next_pending_task` чтобы он матчил
оба варианта (`status IN ('pending', 'todo')`); или deprecate сам тул
(уже планируется в PCA-411).

## 5. Метрики

| Метрика | До Section C | После | Δ |
|---------|-------------:|------:|--:|
| Tables в schema | 22 | 24 | +2 |
| Migrations | 13 | 14 | +1 |
| Service modules | 28 | 31 | +3 |
| MCP write-tools | 41 | 50 | +9 (checkout=2, routine=7) |
| `tests/` total | 929 | 970 | +41 |
| Section C done tasks | 0 | 7 | +7 |
| Total Section A+B+C done | 28 | 35 | +7 |

## 6. Что не вошло (out of scope)

- **Web UI** для checkout / routines (timeline, approval inbox, kanban
  по новым статусам) — отдельные задачи.
- **CLI** для `cod-doc checkout / routine` команд — нет.
- **Кастомные routines из UI** (proposal 07 Q5) — только встроенные.
- **Cron daemon** (см. G2) — отдельная задача.
- **Pre-commit hook** на required PR-секции (proposal 11 Q3) — нет.

## 7. Следующий шаг

Section A (Phase 1) ✅, B (Phase 2) ✅, C (Phase 3) ✅ закрыты.

Открытые направления:
- **Section D (Phase 4 — Adapter, PCA-300..302)** — 3 задачи; LLMAdapter
  Protocol + openai_compat / claude_native + AdapterRegistry. Зависит
  только от Section A (закрыта). Маленькая.
- **Section E (UX & Migration, PCA-400..422)** — 7 задач. Web UI / import
  improvements / link redesign. Низкий риск, высокая видимость.
- **Section F backlog** — пополнилась findings F1-F6 (Section B) +
  G1-G6 (Section C). Если выбираем «consolidation cycle» —
  можно расчистить F-bucket до открытия Phase 4/5.

Findings G1-G6 заведены как PCA-918..923 в Section F.

Рекомендация: открыть Section D (compact, 3 tasks) или Section F
(consolidation cycle на накопленные findings) перед Section E.
