# 10 — Adapter pattern for LLM executors

> Category: 🔵 Architecture · Risk: high · Dependencies: 01

## Context: like paperclip

In [`packages/adapters/`](https://github.com/paperclipai/paperclip/tree/master/packages/adapters):
- `claude-local`, `codex-local`, `cursor-local`, `gemini-local`, `acpx-local`, `pi-local`, `opencode-local`, `openclaw-gateway`.

All register in a **mutable registry** (see [`adapter-plugin.md`](https://github.com/paperclipai/paperclip/blob/master/adapter-plugin.md)):
```ts
registerServerAdapter(adapter)
unregisterServerAdapter(type)
requireServerAdapter(type)
```

External adapters can be loaded via JSON-config (`~/.paperclip/adapter-plugins.json`). Principle: the core must not contain hardcoded imports of executors.

## Current state of cod-doc

[cod_doc/agent/orchestrator.py:70-77](cod_doc/agent/orchestrator.py#L70-L77):
```python
self.client = AsyncOpenAI(
    api_key=config.api_key,
    base_url=config.base_url,
    default_headers={...},
)
```

— an OpenAI-compatible client is hardcoded in the constructor. To:
- use Claude directly (anthropic SDK with tool-use)
- run a local model (llama.cpp, ollama)
- A/B-test models

— you need to edit `Orchestrator`. This blocks experiments.

## Proposal

### 1. `LLMAdapter` interface

```python
class LLMAdapter(Protocol):
    name: str
    capabilities: AdapterCapabilities  # streaming, tool_use, json_mode, vision

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef],
        *,
        max_tokens: int,
        temperature: float | None = None,
    ) -> ChatResponse:
        ...

    async def stream_chat(self, ...) -> AsyncIterator[StreamEvent]:
        ...

    def estimate_tokens(self, text: str) -> int:
        ...

    def cost_estimate(self, input_tokens: int, output_tokens: int) -> Decimal:
        ...
```

### 2. Registry

```python
# cod_doc/agent/adapters/__init__.py
_REGISTRY: dict[str, type[LLMAdapter]] = {}

def register_adapter(name: str, factory: type[LLMAdapter]) -> None: ...
def get_adapter(name: str, config: dict) -> LLMAdapter: ...
def list_adapters() -> list[str]: ...
```

### 3. Built-in adapters

| Adapter          | Backend                          | When                            |
| ---------------- | -------------------------------- | -------------------------------- |
| `openai-compat`  | OpenAI SDK / OpenRouter / vLLM   | default (current behavior)      |
| `anthropic`      | anthropic SDK                    | for Claude tool-use native       |
| `ollama`         | ollama HTTP                      | local models                     |
| `mock`           | deterministic fake               | tests                            |

### 4. Configuration

In [cod_doc/config.py](cod_doc/config.py):
```toml
[llm]
adapter = "anthropic"

[llm.anthropic]
api_key = "..."
model = "claude-opus-4-7"
```

The adapter selector — the only thing that changes when switching the backend.

### 5. Tool-use mapping

The hardest part — different SDKs have different tool-use formats:
- OpenAI: `tools=[{type:"function", function:{name, parameters}}]`, `tool_calls` in response.
- Anthropic: `tools=[{name, description, input_schema}]`, `content=[{type:"tool_use", ...}]`.

Solution: a **neutral internal format** in [tool_defs.py](cod_doc/agent/tool_defs.py), each adapter maps to its SDK-specific one.

### 6. External adapters (Phase 2)

Analog of `~/.paperclip/adapter-plugins.json` — `~/.cod-doc/llm-adapters.json` with references to python packages implementing the protocol. Do not do at start.

## Implementation plan

1. **Define the `LLMAdapter` protocol** + types (`Message`, `ToolDef`, `ChatResponse`, `StreamEvent`).
2. **Extract existing OpenAI logic** into `cod_doc/agent/adapters/openai_compat.py`. The orchestrator no longer creates the client directly.
3. **Refactor `Orchestrator`** — accepts `adapter: LLMAdapter` via DI; all interaction through the interface.
4. **Registry + config.** Selector by name.
5. **Implement the `anthropic` adapter.** Native tool-use.
6. **Implement the `mock` adapter** — raises test coverage of the orchestrator.
7. **Documentation.** A section in HANDBOOK: "how to add an adapter".

## Risks

- **Big scope.** The heaviest of all proposals. Start only when there is a real need to switch the backend (or for tests).
- **Tool-use semantic drift.** SDKs behave slightly differently (e.g. Claude parallel tool calls in one response, OpenAI — sequential). The adapter must hide this, but abstraction leaks are possible.
- **Cost-tracking.** If models cost differently, a unified cost metric must normalize. Replace heuristics with per-adapter `cost_estimate`.

## Success metrics

- Switching the LLM backend — a change in config, zero changes in [orchestrator.py](cod_doc/agent/orchestrator.py).
- Unit tests of the orchestrator use the `mock` adapter, make no network calls.
- At least 2 working backends (`openai-compat` + `anthropic`) at phase close.

## Related

- 01 (skills) — skill loading does not depend on the adapter, common to all.
- All other proposals — neutral to the adapter choice.

## When NOT to do

- While the only backend is OpenRouter with different models (the `openai-compat` adapter already covers this).
- While there is no tangible benefit from Claude-native tool-use or a local model.
- This is pure architectural debt — take it on if the consumption scenario is clear.

## Notes (cod-doc context)

- **`mock` adapter as a side-task.** A full registry is a big scope, but a `mock` adapter for orchestrator tests can be pulled out separately: create a minimal interface under two implementations (real OpenAI-compat + mock). This already gives deterministic tests of [orchestrator.py](cod_doc/agent/orchestrator.py) without opening the whole abstraction.
- **Tool-use semantic drift — a real pain.** OpenAI does tool calls sequentially, Anthropic — in parallel in one response. This is not "a minor SDK difference", this is semantics. The adapter must hide it, and this requires test coverage on both backends.
- **Cost-tracking normalization.** `claude-opus` and `gpt-4o` cost differently per token; a unified `cost_event` metric must contain an absolute value in one currency, not "tokens". Source of pricing — a static reference in code or an external API?
- **When NOT to do — the RFC itself says.** While the only real scenario is OpenRouter with different models, the current `openai-compat` covers it. Do not take it on until there is an explicit request for Claude-native or a local model.
- **Streaming.** If a streaming mode in UI is important (showing intermediate thinking), it must be accounted for in the protocol from the start, otherwise a rework will be needed.

## Open questions

- **Q1.** Can the `mock` adapter be pulled out as a separate PR without the full registry — as a minimal dependency-injection point in `Orchestrator`?
- **Q2.** Source of pricing data for `cost_estimate` — hardcoded in code, a separate JSON in the repo, or an external API (LiteLLM, OpenRouter)?
- **Q3.** Streaming in UI — is it supported now? If not — lay it into the adapter at once or a non-streaming MVP?
- **Q4.** Adapter secrets config — env vars, a separate secret-store, or in `~/.cod-doc/credentials.toml`?
- **Q5.** External adapters via JSON-config (Phase 2) — are they needed at all, or are built-in + `mock` enough?
- **Q6.** Capability mismatch — what to do if a task requires tool-use, but the adapter has `capabilities.tool_use=False` (e.g. a local model without support)? Auto-fallback to another adapter or an error?
