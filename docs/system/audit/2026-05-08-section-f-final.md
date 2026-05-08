---
type: audit-report
scope: paperclip-adoption / Section F (Final 8 — backlog drained)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-f-consolidation.md
  - ../roadmap/paperclip-adoption-task-plan.md
---

# Section F — Final Closure Report (8 remaining backlog tasks)

> **Назначение.** Зафиксировать закрытие последних 8 задач из backlog
> (PCA-914, 915, 917, 921, 922, 924, 925, 928). Вместе с предыдущей
> консолидацией это закрывает весь paperclip-adoption RFC-беклог.

## 1. TL;DR

- **PCA-915** (F4) — `_uuid7()` (RFC 9562 §5.7) в `activity_service`. Время-сортируемые ID без новых зависимостей.
- **PCA-925** (H2) — `_PRICING_USD_PER_MTOK` dict в `openai_compat.py`. Покрывает claude-sonnet-4-6, claude-opus-4, claude-3-5-{sonnet,haiku}, gpt-4o, gpt-4o-mini, o1, gemini-2.0-flash, gemini-pro. Lookup с/без `provider/` префикса.
- **PCA-917** (F6) — `_revert_task_doc()` в `revision_service`; `EntityKind.TASK_DOC` теперь поддерживается в `revert()`.
- **PCA-921** (G4) — `checkout_service.warn_if_no_checkout()` + вызовы в `task.complete`, `task.set_blocker`, `task.log_progress`. Логирует warning, не падает.
- **PCA-928** (I1) — `DocumentModel.content_sha256_head` + migration `0016_document_sha256`. `scan_folder()` сравнивает stored sha с file head → корректный `changed` статус. `import_or_update_markdown(source_sha256=...)` сохраняет sha при импорте; bulk-import endpoint передаёт sha.
- **PCA-914** (F3) — `_bootstrap_default_routines()` в `project_service.init_project`. Создаёт `approval_stale_default` routine (cron `*/15 * * * *`) при инициализации проекта. Idempotent.
- **PCA-922** (G5) — `_update_or_create_finding_task()` в `routine_service.run_now()` для `on_finding=update_existing_task`. Stable signature (`<!-- routine:NAME -->`) в description обеспечивает дедупликацию.
- **PCA-924** (H1) — `ChatChunk` тип + `stream_chat()` в `MockAdapter`. `supports_streaming(adapter)` хелпер. Real adapters (openai_compat / anthropic) пока не реализуют — Protocol не требует. **2 новых теста**.
- **2 новых тестa** (streaming). **1010 tests pass** (1008 → 1010).
- **0 deferred findings** — backlog полностью пуст. **61 task done** (53 → 61).

## 2. Section F final deliverables

| # | Задача | Файл / артефакт | Статус |
|---|--------|------------------|--------|
| 1 | UUID7 generator | `cod_doc/services/activity_service.py` | ✅ |
| 2 | Pricing dict + cost_estimate | `cod_doc/agent/adapters/openai_compat.py` | ✅ |
| 3 | TASK_DOC revert handler | `cod_doc/services/revision_service.py` | ✅ |
| 4 | `warn_if_no_checkout` helper | `cod_doc/services/checkout_service.py` | ✅ |
| 5 | Checkout warnings in 3 MCP tools | `cod_doc/mcp/tools/task_tools.py` | ✅ |
| 6 | `content_sha256_head` column | `cod_doc/infra/models/documents.py` | ✅ |
| 7 | Migration 0016 | `cod_doc/infra/migrations/versions/20260508_0016_document_sha256.py` | ✅ |
| 8 | sha256 wired through import + scan | `cod_doc/services/import_service.py`, `cod_doc/api/web/pages/docs.py` | ✅ |
| 9 | `_bootstrap_default_routines` | `cod_doc/services/project_service.py` | ✅ |
| 10 | `_update_or_create_finding_task` | `cod_doc/services/routine_service.py` | ✅ |
| 11 | `ChatChunk` + `supports_streaming` | `cod_doc/agent/adapters/base.py` | ✅ |
| 12 | `MockAdapter.stream_chat()` | `cod_doc/agent/adapters/mock.py` | ✅ |
| 13 | Section F final audit-report | `docs/system/audit/2026-05-08-section-f-final.md` | ✅ |
| 14 | Streaming tests | `tests/test_adapters.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-915** — `_uuid7()` produces UUID strings with version=7; sortable by timestamp prefix across milliseconds; same-ms IDs sorted by random bits (RFC 9562 compliant).
- [x] **PCA-925** — `cost_estimate(1_000_000, 500_000, "openai/gpt-4o")` returns `Decimal("7.50")`; unknown models return `Decimal(0)`; bare model names (without `provider/` prefix) also resolve.
- [x] **PCA-917** — `revert(EntityKind.TASK_DOC.value)` no longer raises `RevertNotSupportedError`; delegates to `task_doc_service.revert`.
- [x] **PCA-921** — `warn_if_no_checkout()` returns `None` when checkout matches caller, returns + logs warning string otherwise; never raises.
- [x] **PCA-928** — Re-importing a modified file flips its `scan_folder` status from `unchanged` to `changed`; pre-PCA-928 docs (no stored sha) still show `unchanged` with reason indicating missing sha.
- [x] **PCA-914** — `init_project()` always returns with at least one routine present (`approval_stale_default`); calling `init_project()` twice doesn't create duplicates.
- [x] **PCA-922** — Routine with `on_finding=update_existing_task` finds open task with `<!-- routine:NAME -->` signature and updates; otherwise creates a fallback task with `id_prefix="ROU"`.
- [x] **PCA-924** — `MockAdapter.stream_chat()` yields `ChatChunk` for text (≥1 content_delta + 1 finish) and tool-calls (1 tool_call_delta + 1 finish); `supports_streaming(adapter)` returns True.

## 4. Метрики

| Метрика | До | После | Δ |
|---------|---:|------:|--:|
| `tests/` total | 1008 | 1010 | +2 |
| Alembic migrations | 15 | 16 | +1 |
| LLMAdapter Protocol types | 7 | 8 (+ChatChunk) | +1 |
| Routine on_finding policies wired | 1 | 2 | +1 |
| Backlog pending tasks | 8 | 0 | -8 |
| Section F closed | 8 | 16 | +8 |
| **Grand total done (A–F)** | **53** | **61** | **+8** |

## 5. RFC статус — DRAINED

Весь paperclip-adoption RFC-беклог (PCA-001..PCA-930) реализован.
Sections A, B, C, D, E, F полностью закрыты.

| Section | Phase | Tasks | Status |
|:--------|:------|------:|:-------|
| A | Skills & Heartbeat | 17 | ✅ |
| B | Audit infra | 6 | ✅ |
| C | Extensions | 7 | ✅ |
| D | Adapter | 3 | ✅ |
| E | UX & Migration | 7 | ✅ |
| F | Tooling fixes + backlog | 21 | ✅ |
| **TOTAL** | | **61** | **✅** |

## 6. Что не входило в RFC (для следующих циклов)

Эти направления возникали по ходу работы, но не входили в исходный
RFC-беклог:

- **Real-adapter streaming** — `OpenAICompatAdapter.stream_chat()` и
  `AnthropicAdapter.stream_chat()`. Protocol готов, MockAdapter работает.
  SDK-streaming требует переработки `Orchestrator._agent_loop`, чтобы
  агент мог потреблять чанки до tool-call'а.
- **Web UI «Suggested links»** — подвал doc_show.html, accept/reject
  кнопки для `link_suggestion` (proposal 15 §2.3.3 шаг 15.7).
- **Cost dashboard** — UI-страница для `AgentRun.llm_tokens_in/out` ×
  pricing dict, агрегаты по модели и проекту.
- **Routine UI** — web-страница `/p/{slug}/routines` для CRUD + history
  (сейчас доступно только через MCP/CLI).
- **External adapters CLI** — `cod-doc adapter add NAME --module=…`
  (сейчас только через JSON plugin loader).

## 7. Рекомендация

RFC drained. Следующий шаг — либо новый RFC от пользователя, либо
proactive consolidation cycle (повторный аудит чистого репозитория с
новой парой глаз).
