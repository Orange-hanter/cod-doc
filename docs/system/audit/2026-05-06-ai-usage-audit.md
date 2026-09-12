---
type: audit-report
scope: AI/LLM usage in COD-DOC (orchestrator + ai_generate + ai_text + MCP)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-06
last_updated: 2026-05-06
audit_target_revision: HEAD = b07a97e (post WEB-013..014/COD-070..079, daemon UI)
related_docs:
  - ../MASTER.md
  - ../capabilities/agents-and-skills.md
  - ../capabilities/context-retrieval.md
  - 2026-05-06-cli-vs-web-parity.md
related_code:
  - cod_doc/agent/
  - cod_doc/services/ai_generate.py
  - cod_doc/services/ai_text.py
  - cod_doc/services/context_service.py
  - cod_doc/services/trace_service.py
  - cod_doc/services/model_catalog.py
  - cod_doc/api/web/pages/docs.py
  - cod_doc/api/web/pages/stories.py
  - cod_doc/api/web/fragments/tasks_fields.py
---

# AI usage — System Audit (2026-05-06)

> A complete inventory of all points where COD-DOC calls an LLM or
> exposes tools to external AI agents. The goal is to record the surface,
> limitations and user pain points; §6 proposes directions for development.

## 0. TL;DR

- **8 AI surfaces** (orchestrator + 6 generation/improve flow + embeddings).
- **One provider** — OpenRouter (OpenAI-compatible API). No direct calls
  to `api.anthropic.com`.
- **Default model** — `anthropic/claude-sonnet-4-6`, switchable via
  [config.py:64](../../../cod_doc/config.py).
- **Prompt caching is absent** in all calls — even when resubmitting
  MASTER.md and large system-prompts. The main source of potential savings.
- **Retry only in the orchestrator-loop** ([retry.py](../../../cod_doc/agent/retry.py));
  all `ai_generate`/`ai_text` calls — single-shot try/except.
- **Cost tracking is absent**: tokens are written to `trace_call`, but are not
  aggregated into a dashboard and are not converted into $.
- **Streaming only in the orchestrator** via WebSocket
  [api/websocket.py](../../../cod_doc/api/websocket.py); generation of stories/tasks/docs/improve —
  blocking, without HTMX-progress.
- **Human-in-the-loop**: there is an `ask_human` tool (the agent can ask),
  but there is no mandatory approve stage in the orchestrator. Web-flow approve via
  preview→save (good), CLI/daemon flow approve is optional.

## Summary

| Severity | Count | Description |
|---|---:|---|
| critical | 0 | — |
| high | 4 | No prompt caching; no cost tracking; retry on gen-flows; UX of blocking waits |
| medium | 6 | Generation streaming; AI-output provenance; budget guard; embeddings not integrated; rate-limit UX; context-degrade visibility |
| low | 3 | Manual model catalog; deprecated-version detection; cost prediction before launch |
| **total** | **13** | — |

---

## 1. Inventory: where AI is called

### 1.1 Orchestrator loop (main AI-runner)

**Where:** [cod_doc/agent/orchestrator.py:21,70-87,407-415](../../../cod_doc/agent/orchestrator.py),
[prompts.py](../../../cod_doc/agent/prompts.py), [tool_defs.py](../../../cod_doc/agent/tool_defs.py),
[tools.py](../../../cod_doc/agent/tools.py), [retry.py](../../../cod_doc/agent/retry.py).

| Aspect | Value |
|---|---|
| Client | `AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key)` |
| Model | `cfg.model` (default `anthropic/claude-sonnet-4-6`) |
| System prompt | `prompts.py` — Snowball Protocol, fail-fast, hybrid references |
| Tools | `read_file`, `write_file`, `calc_hash`, `get_context`, `get_project_status`, `create_task`, `update_task`, `story_get`, `story_link`, `ask_human`, `search_docs`, `reindex` |
| Retry | `with_retry` (4 attempts, exp backoff, jitter) for transient (RateLimit/5xx/Connection) |
| Context-degrade | 3 stages (`no_master` → `refs_only` → `minimal`) on `context_length_exceeded` |
| Budget | Soft cap `cfg.max_context_tokens` (default 100K), soft trimming before the request |
| Trace | `trace_service.record(kind="chat", task_id, input_tokens, output_tokens, tool_calls)` |
| Stream → UI | `event_bus.publish(slug, ...)` → WebSocket `/ws/projects/{slug}` (TUI/Web) |

**Limitations:**
- One monolithic system prompt, without `cache_control` blocks → each
  iteration pays for the whole prompt again.
- `with_retry` does not distinguish `RateLimit-with-retry-after` vs generic 429:
  it uses the same `2^attempt` backoff, which can cause an early
  rebound on a fair rate-limit from OpenRouter.
- The `context_length_exceeded` ladder is smart, but **does not remember which
  level was sufficient**: the next iteration starts at L0 again and may
  hit 429 again.
- Tool execution (`ToolExecutor.execute`) has no timeout — a slow
  `read_file` on a large file blocks the loop.

### 1.2 Generation pipelines (AI-generation in Web)

**Where:** [cod_doc/services/ai_generate.py](../../../cod_doc/services/ai_generate.py),
called from [api/web/pages/docs.py](../../../cod_doc/api/web/pages/docs.py),
[api/web/pages/stories.py](../../../cod_doc/api/web/pages/stories.py).

| Function | Purpose | System prompt | Endpoint |
|---|---|---|---|
| `generate_stories` | From a selection of docs → JSON list of stories | `_STORY_SYSTEM_PROMPT` | `POST /p/{slug}/stories/generate` |
| `generate_tasks_for_story` | Story → 2-4h tasks | `_TASK_SYSTEM_PROMPT` | `POST /p/{slug}/stories/{id}/tasks/generate` |
| `generate_doc_from_sources` | N docs → new document | `_DOC_SYSTEM_PROMPT` | `POST /p/{slug}/docs/generate` |
| `generate_master_from_folder` | Folder scan → MASTER.md draft | `_MASTER_SYSTEM_PROMPT` | used in `import_master/scan` |

All 4 functions share the helper `_chat_json` ([ai_generate.py:305-348](../../../cod_doc/services/ai_generate.py#L305)):

```python
client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
completion = client.chat.completions.create(
    model=cfg.model,
    messages=[{"role": "system", ...}, {"role": "user", ...}],
    max_tokens=cfg.max_tokens,
    response_format={"type": "json_object"},
)
```

**Limitations** (all four flows):
- **Single-shot try/except** — no retry at all. One RateLimit → AIBackendError → red banner in the UI.
- **Sync API** (`OpenAI`, not `AsyncOpenAI`) → blocks the FastAPI event loop
  during long generations (observed: 10-30 seconds on a crowded MASTER.md).
- **No streaming** — the user waits silently. Only an HTMX spinner.
- **JSON-mode without strict schema validation on the model side**: parsing
  via `json.loads` + manual `_coerce_story/task/...`. On small models
  prose is sometimes returned on top of JSON → AIBackendError.
- **User-message is trimmed to 60K characters** in `generate_stories` —
  for large projects it goes out of scope without a warning.
- **No prompt cache** (even when the same set of docs is sent again).

### 1.3 `improve_text` (inline AI-editing of fields)

**Where:** [cod_doc/services/ai_text.py:48-104](../../../cod_doc/services/ai_text.py),
endpoint [tasks_fields.py:199](../../../cod_doc/api/web/fragments/tasks_fields.py).

```bash
POST /p/{slug}/tasks/{task_id}/fields/{field}/improve
```

| Aspect | Value |
|---|---|
| System prompt | `_SYSTEM_PROMPT` — senior technical editor, preserves markdown + language |
| User message | `intent` (what to change) + `text` (original) |
| Trace | `trace_service.record(kind="improve", ...)` |
| UI | Returns an HTMX fragment with suggested-text, the user accepts/rejects |

**Limitations:**
- No diff view: the user sees the final text, but not **what exactly**
  changed. Accept = rewrite everything in one block.
- No history of intents saved — every time you write "make it shorter"
  from scratch.
- Same synchronous sync OpenAI client → blocks the event loop.

### 1.4 Generate tasks from MASTER (autonomous mode)

**Where:** [orchestrator.py:521-567](../../../cod_doc/agent/orchestrator.py).

The daemon-loop, when `daemon_enabled=true`, takes the first ~4000 characters
of MASTER.md, forms an **inline prompt** and asks the LLM to create tasks via
a limited tool-set (`create_task`, `get_project_status`).

**Limitations:**
- **Inline prompt without a template** — no version control, no A/B testing.
- **Hard-coded 4000 characters** of MASTER trimming — for large projects
  half the context is lost without a warning.
- No idempotency: a re-run can create duplicate tasks, except
  via `task_find_duplicate` (MCP-only utility).

### 1.5 Embeddings (optional)

**Where:** [cod_doc/services/](../../../cod_doc/services/) (ChromaDB),
config in [config.py:87-103](../../../cod_doc/config.py).

| Aspect | Value |
|---|---|
| Backend | `embedding_backend` ∈ {`openai` (default), `local` sentence-transformers} |
| Model | default `openai/text-embedding-ada-002` |
| Storage | ChromaDB on disk (`cfg.chroma_path`) |
| Tools | `search_docs(query)`, `reindex()` — available to the agent via `tool_defs.py` |

**Limitations:**
- **Not integrated into the main loop** — the orchestrator does not do
  retrieval automatically before the LLM call. Available only if the LLM
  itself decides to call `search_docs`.
- **No hooks on changes** — the index is updated manually via
  `reindex()`. After `doc_create`/`section_patch` Chroma does not know.
- Default `text-embedding-ada-002` — an outdated model (2022), accuracy
  noticeably lower than `text-embedding-3-small`/`-large` (2024) at the
  same price.

### 1.6 MCP server (external AI consumes our tools)

**Where:** [cod_doc/mcp/](../../../cod_doc/mcp/), launched via `cod-doc mcp`.

This is the inverse direction: **our tools are exported** to an external
MCP client (Claude Code, Claude Desktop), which decides itself when to
call them. We do not pay for tokens here — the host agent pays.

**Limitations** (from our project's perspective):
- No per-tool authorization model — the client has full access to the
  project's domain model. Insufficient for a multi-tenant scenario.
- `tools/list` is returned in full (~50 tools); large LLMs absorb this,
  but small models get confused in the choice.

---

## 2. Configuration and key management

| Field | Default | Where | UI |
|---|---|---|---|
| `api_key` | — (required) | [config.py:58](../../../cod_doc/config.py) | `/settings` (password input) |
| `base_url` | `https://openrouter.ai/api/v1` | config.py:60 | `/settings` |
| `model` | `anthropic/claude-sonnet-4-6` | config.py:64 | `/settings` (dropdown + custom) |
| `max_context_tokens` | 100_000 | config.py:68-74 | `/settings` |
| `max_tokens` | output cap | config.py | — |
| `embedding_backend` | `openai` | config.py:88-95 | `/settings` |
| `embedding_model` | `openai/text-embedding-ada-002` | config.py:96-103 | `/settings` |
| `agent_enabled` | false | config.py | `/settings` |
| `agent_interval` | (sec) | config.py | `/settings` |

Stored in `~/.cod-doc/config.yaml` or in env (`COD_DOC_*`).

**Limitations:**
- One global `api_key` for all projects — you cannot set different
  tariff keys for prod-data and sandbox.
- API key is read plain-text from `config.yaml` (perms 0600 are not enforced).
- **No key validation** on save: the error only shows up on the first
  AI call.

---

## 3. Model catalog and provenance

[model_catalog.py](../../../cod_doc/services/model_catalog.py) describes
8 models: Sonnet 4.6 / Opus 4 / Haiku 4.5, GPT-5, GPT-4.1-mini,
Gemini 2.5 Pro, DeepSeek R1, Llama 3.3-70b. Used only for the
dropdown in Settings, **does not validate** the selected model.

**Provenance of AI-output in the DB:**

| Entity | "AI-generated" marker |
|---|---|
| Document | `frontmatter.generated_from = [source_doc_keys]` (only for `/docs/generate`) |
| Story | `Revision.reason = "ai-generate"` |
| Task | `Revision.reason = "ai-generate:{story_id}"` or daemon-flow |
| Field improve | revision `reason="ai-improve:{field}"` (see tasks_fields.py — requires manual review) |

**Limitations:**
- The AI-author marker is spread across `Revision.reason` — **there is no aggregate**
  "all AI-generated entities of the project". Analytics "how much AI vs human
  writes" is extracted from the DB with a regular expression over reason.
- No link `revision → trace_call` — it is impossible to get from a specific
  edit to its LLM call (tokens, latency, model).
- Document `frontmatter.generated_from` is not indexed → the query "what
  is generated from MASTER.md" = scanning all docs.

---

## 4. Logging and tracing

**Trace table** ([trace_service.py](../../../cod_doc/services/trace_service.py)):
fields `model`, `task_id`, `kind` (chat/search/reindex/improve), `input_tokens`,
`output_tokens`, `duration_ms`, `tool_calls` (JSON), `error`.

**Where the user sees it:**
- The "Trace" tab on task-detail (if task_id is linked) — list newest-first.
- There is no global "AI activity" page.

**Limitations:**
- `task_id` can be NULL → orphan-traces (for example, doc generation
  not linked to a task) are **invisible** in the UI. Access only via a direct
  SQL query.
- **No retention policy** — the table grows linearly, no vacuum.
- **No cost column**. To calculate $, you need a JOIN with `model_catalog.py`
  (static in code, not in the DB).
- Tool_calls as a JSON-string → no fast filter "all read_file calls"
  without LIKE.

---

## 5. Limitations and pain points (summary)

### 5.1 High

#### AI-HI-1. No prompt caching
**Symptom:** each orchestrator-iteration sends the same
~3K-token system prompt + MASTER.md again. On a 50-step task —
overpayment ×50. All four `ai_generate.*` flows are also without cache.

**Where:** [orchestrator.py:407-415](../../../cod_doc/agent/orchestrator.py),
[ai_generate.py:320-328](../../../cod_doc/services/ai_generate.py).

OpenRouter supports Anthropic prompt caching (via `cache_control`
blocks) and provider-specific cache hints. Not used.

#### AI-HI-2. No cost tracking and budget guard
**Symptom:** the user does not see the costs. Daemon mode with `agent_enabled`
+ `agent_interval=60` can burn $50 overnight without an alert. Cost fields
are absent both in `trace_call` and in the UI.

#### AI-HI-3. Generation flows without retry
**Symptom:** transient 429/503/connection-reset on `/docs/generate` or
`/stories/generate` → red banner, the user hits refresh, everything
starts over (we pay for the second prompt). [ai_generate.py:329-330](../../../cod_doc/services/ai_generate.py)
catches **any** Exception as fatal.

#### AI-HI-4. Blocking UX of long generations
**Symptom:** `/docs/generate` on a large corpus takes 20-40 sec.
The user sees a spinner, does not understand "is the request alive", sometimes
F5 → duplicate. No streaming, no "X% complete", no cancel.

### 5.2 Medium

#### AI-ME-1. Generation uses sync `OpenAI` in a FastAPI handler
[ai_generate.py:317](../../../cod_doc/services/ai_generate.py) — sync
client inside a def-handler. Blocks the event loop. Under load even
one or two parallel requests make the whole Web background.

#### AI-ME-2. Streaming is available only in the orchestrator (WS)
Web-flow generation (stories/tasks/docs/improve) — single-shot. No SSE,
no partial-render. WEB-030 (SSE run console) for the daemon-loop is not
implemented yet.

#### AI-ME-3. Embeddings are not used automatically
ChromaDB is configured and works, but retrieval before the LLM call is not done.
The agent must itself think to call `search_docs` — in practice, rarely.
Context efficiency is below potential.

#### AI-ME-4. AI-output provenance is fragmented
See §3. Analytics "AI-vs-human" = regex over `revision.reason` + scanning
`frontmatter`. No JOINs to trace_call → cost-per-document cannot
be calculated.

#### AI-ME-5. Context-degrade is not fed back
`orchestrator.py` after a success in degraded mode still starts the next
iteration at L0. If the load is stable, we pay for the same 429 every time.

#### AI-ME-6. Rate-limit UX without `Retry-After`
[retry.py:108-141](../../../cod_doc/agent/retry.py) uses a synthetic
exp backoff even when there is a `Retry-After` header from OpenRouter — may
retry earlier or later than optimal.

### 5.3 Low

#### AI-LO-1. `model_catalog.py` is updated manually
When Sonnet 4.7 is released, a code-PR is needed. No sync with OpenRouter `/v1/models`.

#### AI-LO-2. No deprecated-version detection
If `cfg.model` drops out of the provider's model list — we find out via
a failure in prod.

#### AI-LO-3. No cost-prediction before launch
In `/docs/generate` there is no field "estimated cost: $X.XX" based on the size
of source-docs × model cost. The user clicks "Generate" blindly.

---

## 6. Proposals for development (to solve user pain)

Grouped by pain type. Each entry is a backlog candidate, not a plan.
Closure — after a separate RFC/proposal.

### 6.1 Money and spend control

**P-1. Cost tracking + dashboard** *(closes AI-HI-2, AI-LO-3)*
- Add a `cost_usd` column to `trace_call` (compute from `model_catalog`
  on write).
- Page `/p/{slug}/ai-activity` (or a tab in `/settings/usage`):
  taxonomies — model × kind × day.
- Soft budget in `config.yaml`: `daily_budget_usd` → the daemon-loop stops
  when reached.
- Hard guard on `/docs/generate` and similar: if the request exceeds
  N% of the daily budget — a confirm-dialog.

**P-2. Cost-prediction before launch**
- On the `/docs/generate` form: a live counter "~$0.04 input, ~$0.02 output, max ~$0.30"
  based on tokenizing source-docs (`tiktoken` or a heuristic).
- Same for `/stories/generate`, `/improve`.
- Pain: the user clicks "Generate" and **knows** what they are buying.

**P-3. Prompt caching** *(closes AI-HI-1)*
- In the orchestrator: move the system prompt + MASTER.md into a `cache_control`-block
  (Anthropic-style via OpenRouter).
- Measure the Cache-hit rate in `trace_call` (new fields `cache_read_tokens`,
  `cache_write_tokens`).
- ROI: on a 50-step task — input-tokens savings of 80-90%.
- Pain: the user sees that repeated runs cost an order of magnitude less.

### 6.2 UX of long operations

**P-4. Streaming generation in Web** *(closes AI-HI-4, AI-ME-2)*
- Move `/docs/generate`, `/stories/generate`, `/improve` to async
  +`stream=True` + SSE (`hx-ext="sse"` already works in `_layout/project_tabs.html`).
- Partial render: show sections as they arrive.
- "Cancel" button — `DELETE /jobs/{job_id}`.
- Pain: the wait stops feeling like "it's frozen".

**P-5. Retry for generation flows** *(closes AI-HI-3)*
- Apply `with_retry` to all `_chat_json` calls.
- Distinguish transient (429/503/connection) vs fatal (auth/4xx-other).
- Pain: a single network drop does not lose the work.

**P-6. Async-everywhere in Web AI-handlers** *(closes AI-ME-1)*
- Change [ai_generate.py:_chat_json](../../../cod_doc/services/ai_generate.py)
  to `async def` + `AsyncOpenAI`.
- Switch FastAPI handlers from `def` to `async def`.
- Pain: with two users the UI does not hang.

### 6.3 AI-output quality and trust

**P-7. Diff-view for `improve`** *(closes part of AI-ME-x in §1.3)*
- Return not plain-text, but a diff (old ↔ new) as in `_frag/section_view`.
- A "Why?" field — why the AI proposed this edit (optional prompt-trick:
  ask to return `{before, after, rationale}`).
- Pain: the user sees **what exactly** changes and decides consciously.

**P-8. Embeddings retrieval before the LLM call** *(closes AI-ME-3)*
- Hooks on `doc_create` / `section_patch` / `task_create` →
  recompute the Chroma vector (background task).
- In the orchestrator: before the main LLM call — top-K retrieval on the current
  task, added to `<context_refs>`.
- Migrate the default embedding model to `text-embedding-3-small`.
- Pain: the agent finds relevant sections **on its own**, without the need
  to explicitly list refs in the task.

**P-9. Provenance UI: agg AI-output** *(closes AI-ME-4)*
- A `created_by_kind` column in `Document` / `Task` / `Story`: `human|ai|hybrid`.
- A foreign key `revision.trace_call_id`.
- The page `/p/{slug}/ai-activity` (see P-1) shows: "AI created N tasks /
  M docs over the period, average cost $X".
- Pain: "can I trust this document?" — the answer is visible, not a guess.

### 6.4 Reliability

**P-10. Honor `Retry-After`** *(closes AI-ME-6)*
- In [retry.py](../../../cod_doc/agent/retry.py): if the exception has
  `retry_after` (RateLimitError parses the header), use it instead of
  exp backoff.

**P-11. Adaptive context-level memory** *(closes AI-ME-5)*
- Save to `task_meta` the last successful context-level (`L0|no_master|refs_only`).
- The next iteration starts from it, not from L0.

**P-12. Tool-execution timeout**
- In `ToolExecutor.execute` — `asyncio.wait_for(..., timeout=30)`.
- Pain: the orchestrator does not hang on a rogue read_file of a 1GB log.

### 6.5 Long-running backlog (P-13..15)

**P-13. Per-project / per-environment API key**
Currently one global. Allow an override in `Project.config` to separate
prod/sandbox spending.

**P-14. Auto-sync `model_catalog`** *(closes AI-LO-1, AI-LO-2)*
Cron-job (or a lazy refresh on start) → fetch OpenRouter `/v1/models`
→ update the local catalog. Deprecation detection → warning in `/settings`.

**P-15. Mandatory approval-stage for daemon mode**
Currently the daemon writes to the DB directly. Add `daemon_approval_required=true`:
instead of a commit, the agent creates a `proposal` (see [proposals/12-approvals.md](../../../proposals/12-approvals.md)),
the human reviews in the UI, clicks accept. Pain: the autonomous mode stops
being "scary".

---

## 7. Top-5 changes with maximum impact

| # | Proposal | Closes | Cost | Benefit |
|---|---|---|---|---|
| 1 | **P-3 Prompt caching** | AI-HI-1 | medium (`cache_control` contract) | -80% input-tokens on repeated runs |
| 2 | **P-1 Cost tracking + dashboard** | AI-HI-2, AI-LO-3 | medium (new fields + page) | the user stops fearing the daemon |
| 3 | **P-4 Generation streaming** | AI-HI-4, AI-ME-2 | medium (async transition + SSE) | the UX stops "freezing" |
| 4 | **P-5 Retry for gen-flows** | AI-HI-3 | small (apply `with_retry`) | eliminating "random" failures |
| 5 | **P-8 Embeddings retrieval** | AI-ME-3 | medium (hooks + model migration) | the agent finds context on its own |

---

## 8. Methodology

1. The inventory was collected via grep over `cod_doc/`:
   - `grep -rnE "AsyncOpenAI|OpenAI\(|claude|openrouter|cache_control|stream="`,
   - `grep -rn "AIBackendError\|trace_service\|response_format"`,
   - checked `cod_doc/agent/`, `cod_doc/services/`, `cod_doc/api/web/pages/`,
     `cod_doc/api/web/fragments/`, `cod_doc/mcp/`.
2. Each claim in §1-§4 was verified by reading the file at the indicated line.
3. Severity:
   - **high** — the user loses money or productivity every day;
   - **medium** — UX-degradation, avoidable;
   - **low** — hygiene, operational risks.
4. The proposals in §6 do not claim to be a plan; this is backlog seed for
   separate RFCs.

## 9. What remained out of scope

- **Key security** (encryption-at-rest, env-only) — a separate
  security audit.
- **Prompt quality** (prompt-engineering review of system-prompts) —
  requires A/B testing, a separate workstream.
- **AI-output ethics** (hallucinations, fail-fast exists in prompts.py but
  not assessed quantitatively) — a separate quality-audit.
- **MCP authorization model** — will be considered when a
  multi-tenant scenario appears.

## 10. Changelog

| Date | Event |
|---|---|
| 2026-05-06 | Audit conducted. 13 findings (4 high, 6 medium, 3 low). 15 proposals grouped by pain type. Top-5 prioritized. |
