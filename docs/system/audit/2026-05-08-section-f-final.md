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

> **Purpose.** Record the closure of the last 8 tasks from the backlog
> (PCA-914, 915, 917, 921, 922, 924, 925, 928). Together with the previous
> consolidation this closes the entire paperclip-adoption RFC backlog.

## 1. TL;DR

- **PCA-915** (F4) — `_uuid7()` (RFC 9562 §5.7) in `activity_service`. Time-sortable IDs without new dependencies.
- **PCA-925** (H2) — `_PRICING_USD_PER_MTOK` dict in `openai_compat.py`. Covers claude-sonnet-4-6, claude-opus-4, claude-3-5-{sonnet,haiku}, gpt-4o, gpt-4o-mini, o1, gemini-2.0-flash, gemini-pro. Lookup with/without the `provider/` prefix.
- **PCA-917** (F6) — `_revert_task_doc()` in `revision_service`; `EntityKind.TASK_DOC` is now supported in `revert()`.
- **PCA-921** (G4) — `checkout_service.warn_if_no_checkout()` + calls in `task.complete`, `task.set_blocker`, `task.log_progress`. Logs a warning, does not fail.
- **PCA-928** (I1) — `DocumentModel.content_sha256_head` + migration `0016_document_sha256`. `scan_folder()` compares the stored sha with the file head → correct `changed` status. `import_or_update_markdown(source_sha256=...)` stores the sha on import; the bulk-import endpoint passes the sha.
- **PCA-914** (F3) — `_bootstrap_default_routines()` in `project_service.init_project`. Creates an `approval_stale_default` routine (cron `*/15 * * * *`) on project init. Idempotent.
- **PCA-922** (G5) — `_update_or_create_finding_task()` in `routine_service.run_now()` for `on_finding=update_existing_task`. A stable signature (`<!-- routine:NAME -->`) in the description ensures deduplication.
- **PCA-924** (H1) — `ChatChunk` type + `stream_chat()` in `MockAdapter`. `supports_streaming(adapter)` helper. Real adapters (openai_compat / anthropic) do not implement it yet — the Protocol does not require it. **2 new tests**.
- **2 new tests** (streaming). **1010 tests pass** (1008 → 1010).
- **0 deferred findings** — the backlog is completely empty. **61 tasks done** (53 → 61).

## 2. Section F final deliverables

| # | Task | File / artifact | Status |
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

## 4. Metrics

| Metric | Before | After | Δ |
|---------|---:|------:|--:|
| `tests/` total | 1008 | 1010 | +2 |
| Alembic migrations | 15 | 16 | +1 |
| LLMAdapter Protocol types | 7 | 8 (+ChatChunk) | +1 |
| Routine on_finding policies wired | 1 | 2 | +1 |
| Backlog pending tasks | 8 | 0 | -8 |
| Section F closed | 8 | 16 | +8 |
| **Grand total done (A–F)** | **53** | **61** | **+8** |

## 5. RFC status — DRAINED

The entire paperclip-adoption RFC backlog (PCA-001..PCA-930) is implemented.
Sections A, B, C, D, E, F are fully closed.

| Section | Phase | Tasks | Status |
|:--------|:------|------:|:-------|
| A | Skills & Heartbeat | 17 | ✅ |
| B | Audit infra | 6 | ✅ |
| C | Extensions | 7 | ✅ |
| D | Adapter | 3 | ✅ |
| E | UX & Migration | 7 | ✅ |
| F | Tooling fixes + backlog | 21 | ✅ |
| **TOTAL** | | **61** | **✅** |

## 6. What was not in the RFC (for next cycles)

These directions came up during the work but were not part of the original
RFC backlog:

- **Real-adapter streaming** — `OpenAICompatAdapter.stream_chat()` and
  `AnthropicAdapter.stream_chat()`. The Protocol is ready, the MockAdapter works.
  SDK-streaming requires reworking `Orchestrator._agent_loop` so that the
  agent can consume chunks before the tool-call.
- **Web UI "Suggested links"** — the footer of doc_show.html, accept/reject
  buttons for `link_suggestion` (proposal 15 §2.3.3 step 15.7).
- **Cost dashboard** — a UI page for `AgentRun.llm_tokens_in/out` ×
  the pricing dict, aggregates by model and project.
- **Routine UI** — a web page `/p/{slug}/routines` for CRUD + history
  (currently available only through MCP/CLI).
- **External adapters CLI** — `cod-doc adapter add NAME --module=…`
  (currently only through the JSON plugin loader).

## 7. Recommendation

RFC drained. The next step is either a new RFC from the user, or
a proactive consolidation cycle (a re-audit of the clean repository with
a fresh pair of eyes).
