---
type: audit-report
scope: paperclip-adoption / Section D (Phase 4 — Adapter pattern)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-c-phase-3.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/10-adapter-pattern.md
  - ../../../cod_doc/agent/adapters/base.py
---

# Section D — Closure Report (Phase 4: LLM Adapter Pattern)

> **Purpose.** Record the closure of 3 tasks of Section D
> (PCA-300, PCA-301, PCA-302) and findings → backlog.

## 1. TL;DR

- **PCA-300** — `LLMAdapter` Protocol + neutral types
  (`ChatResponse`, `ChatMessage`, `ChatChoice`, `ToolCall`, `FunctionCall`,
  `AdapterCapabilities`) in `cod_doc/agent/adapters/base.py`. The neutral
  format mirrors the OpenAI structure, so parsing responses in the orchestrator
  remained unchanged.
- **PCA-301** — three built-in adapters: `openai_compat` (a transparent
  replacement of the former hardcoded `AsyncOpenAI`), `anthropic` (a native
  SDK + format-converters for tool-use), `mock` (a deterministic queue
  for tests, eliminates network calls).
- **PCA-302** — `AdapterRegistry` with a plugin-loader (JSON file), the
  `Config.llm_adapter` field (default `openai_compat`), DI in
  `Orchestrator.__init__(adapter=...)`, `_generate_tasks_from_master`
  updated. All 13 tests of `test_orchestrator.py` are migrated to
  `MockAdapter` (the `AsyncOpenAI` patches are removed).
- **26 new tests** in `tests/test_adapters.py`. **996 tests pass**
  (970 → 996).
- **4 findings** (H1-H4) → backlog Section F (PCA-924..927).

## 2. Section D deliverables

| # | Deliverable | File / artifact | Status |
|---|------------|------------------|--------|
| D1 | Section D audit-report | `docs/system/audit/2026-05-08-section-d-phase-4.md` | ✅ |
| D2 | `LLMAdapter` Protocol + types | `cod_doc/agent/adapters/base.py` | ✅ |
| D3 | `openai_compat` adapter | `cod_doc/agent/adapters/openai_compat.py` | ✅ |
| D4 | `anthropic` adapter | `cod_doc/agent/adapters/anthropic.py` | ✅ |
| D5 | `mock` adapter | `cod_doc/agent/adapters/mock.py` | ✅ |
| D6 | `AdapterRegistry` + plugin loader | `cod_doc/agent/adapters/registry.py` | ✅ |
| D7 | `adapters/__init__.py` | `cod_doc/agent/adapters/__init__.py` | ✅ |
| D8 | `Config.llm_adapter` + `anthropic_api_key` | `cod_doc/config.py` | ✅ |
| D9 | `Orchestrator` DI refactor | `cod_doc/agent/orchestrator.py` | ✅ |
| D10 | Adapter tests (26) | `tests/test_adapters.py` | ✅ |
| D11 | `test_orchestrator.py` migrated to MockAdapter | `tests/test_orchestrator.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-300** — Protocol + neutral types are defined; `LLMAdapter`
      is marked `@runtime_checkable`; `MockAdapter` passes the isinstance check.
- [x] **PCA-301** — `openai_compat` behavior is identical to the former (no
      regressions, 970 previous tests are green). `anthropic` converts
      tool-use both ways. `mock` with an empty queue returns the default
      "done". Calls are recorded in `calls`.
- [x] **PCA-302** — `Orchestrator(project, config, adapter=mock_adapter)`
      wires the adapter directly; without an adapter → selection from the registry by
      `config.llm_adapter`. All orchestrator tests use MockAdapter,
      there are no network calls.

## 4. Findings (→ backlog)

### H1 — Streaming is not implemented *(medium)*

`AdapterCapabilities.streaming=False` for all adapters. `stream_chat()`
is not part of the Protocol (intentionally left for Phase 2 per proposal 10 §61).
UI streaming (showing intermediate thinking) works through the event loop
of the orchestrator, not through SDK streaming — this is correct for the current
architecture. If live-streaming of the response before the tool-call is needed —
we need to add `stream_chat` to the Protocol + implement it in both adapters.

**Recommendation:** do not do it now. Record as backlog.

### H2 — Cost tracking is normalized to zero for `openai_compat` *(low)*

`cost_estimate` in `openai_compat` returns `Decimal(0)` — there is no static
price directory for OpenRouter models. `anthropic` contains an approximate
price for 3 models. Without correct prices the metric `AgentRun.llm_tokens_in/out`
exists, but `cost_event` is missing.

**Recommendation:** add a static dict of prices for popular OpenRouter
models (claude-sonnet, gpt-4o, gemini-pro) to `openai_compat.py`. Source —
a hardcoded JSON in the repo, updated manually. A separate F-task.

### H3 — Capabilities check is not implemented *(low)*

Proposal 10 Q6: "what to do if a task requires tool_use, but
`capabilities.tool_use=False`?". In the current implementation the orchestrator does not
check capabilities before the call — if the adapter does not support
tool_use, it will just fail with an error from the SDK.

**Recommendation:** add a check in `Orchestrator.__init__`:
```python
if not self.adapter.capabilities.tool_use:
    raise ValueError(f"Adapter {self.adapter.name!r} must support tool_use")
```
Trivial + defensive.

### H4 — `self.client` deprecated shim is not documented *(low)*

In `Orchestrator.__init__` `self.client = getattr(self.adapter, "_client", None)` is added as a legacy shim for code that accesses `orchestrator.client` directly. No deprecation warning is emitted, and there is no list of such places.

**Recommendation:** grep `orchestrator.client` + add a `DeprecationWarning`
on access to `self.client`. Remove the shim in the next major version.

## 5. Metrics

| Metric | Before Section D | After | Δ |
|---------|-------------:|------:|--:|
| LLM backends | 1 (hardcoded) | 3 built-in + plugin | +∞ |
| Adapter modules | — | 5 | +5 |
| Orchestrator LLM calls with network | 13 tests × N calls | 0 | -∞ |
| `tests/` total | 970 | 996 | +26 |
| Section D done tasks | 0 | 3 | +3 |
| Total A+B+C+D done | 35 | 38 | +3 |

## 6. What was not included (out of scope)

- **ollama adapter** (proposal 10 §82) — no request for local models.
- **Streaming** (H1) — see above.
- **External adapters CLI** (`cod-doc adapter add`) — only the JSON plugin loader.
- **Cost dashboard** (H2) — no UI page for cost_estimate.

## 7. Next step

Sections A, B, C, D are closed. **38 done tasks** in total.

Open directions:
- **Section E (UX & Migration, PCA-400..422)** — 7 tasks. Folder manifest
  scanner, web import UI, legacy YAML migration, link redesign. High
  visibility, medium risk.
- **Section F backlog** — 12 + 4 = 16 accumulated tasks (F1-F6 + G1-G6 +
  H1-H4). Can be consolidated before opening Section E.

Findings H1-H4 are opened as PCA-924..927 in Section F.

Recommendation: **Section E** (completes the entire RFC backlog), or
**Section F consolidation** (quick wins G2/G3/H3/H4).
