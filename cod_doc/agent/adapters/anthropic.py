"""Anthropic (claude_native) adapter — native Anthropic SDK (PCA-301, proposal 10).

Uses the `anthropic` SDK directly instead of the OpenAI-compatible
shim. Advantages over openai_compat:
- Native Claude tool-use format (parallel tool calls in one response).
- Extended thinking / citation support when available.
- No OpenRouter routing overhead for Claude models.

Tool-use format mapping (the main complexity here):
  OpenAI/neutral format: tools=[{"type":"function","function":{name, description, parameters}}]
  Anthropic SDK format:  tools=[{"name": ..., "description": ..., "input_schema": {...}}]

Response mapping:
  Anthropic: response.content = [TextBlock | ToolUseBlock, ...]
  Neutral:   ChatResponse with ChatMessage(content=..., tool_calls=[...])

If the `anthropic` package is not installed, creating this adapter
raises ImportError — the caller (registry) should fall back to
`openai_compat` in that case.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, ClassVar

from cod_doc.agent.adapters.base import (
    AdapterCapabilities,
    ChatChoice,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    FunctionCall,
    ToolCall,
)


class AnthropicAdapter:
    """Claude-native adapter via the Anthropic Python SDK.

    Requires: pip install anthropic
    """

    name: str = "anthropic"
    capabilities: AdapterCapabilities = AdapterCapabilities(
        tool_use=True,
        streaming=False,
        json_mode=False,
        vision=True,
        parallel_tool_calls=True,
    )

    # USD per 1k tokens (approximate, as of 2026-05).
    _PRICING: ClassVar[dict[str, tuple[float, float]]] = {
        "claude-opus-4-7": (0.015, 0.075),
        "claude-sonnet-4-6": (0.003, 0.015),
        "claude-haiku-4-5": (0.00025, 0.00125),
    }

    def __init__(self, *, api_key: str, model: str | None = None, **kwargs: Any) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package not installed; run: pip install anthropic"
            ) from exc
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._default_model = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> ChatResponse:
        import anthropic

        anthropic_tools = _to_anthropic_tools(tools)
        anthropic_messages = _to_anthropic_messages(messages)

        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            messages=anthropic_messages,
            tools=anthropic_tools,
        )
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            raw = await self._client.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            from cod_doc.agent.retry import LLMError

            raise LLMError(str(exc)) from exc

        return _from_anthropic(raw)

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def cost_estimate(self, input_tokens: int, output_tokens: int, *, model: str) -> Decimal:
        # Fuzzy match on model slug.
        for key, (inp_rate, out_rate) in self._PRICING.items():
            if key in model:
                cost = (input_tokens / 1000 * inp_rate) + (output_tokens / 1000 * out_rate)
                return Decimal(str(round(cost, 6)))
        return Decimal(0)


# --------------------------------------------------------------------------- #
# Format converters                                                             #
# --------------------------------------------------------------------------- #


def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-format tools → Anthropic tools."""
    out: list[dict[str, Any]] = []
    for t in tools:
        if t.get("type") == "function":
            fn = t["function"]
            out.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
    return out


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-format message list to Anthropic format.

    Key differences:
    - No "system" in messages[] (Anthropic separates it as a top-level param).
    - Tool results: role="tool" → role="user" with type="tool_result" content.
    - Tool calls in assistant messages: tool_calls → content=[{type:"tool_use",...}].
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            # Anthropic system is top-level; skip here (handled at call site).
            continue
        if role == "tool":
            # Convert tool result to Anthropic format.
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", ""),
                            "content": m.get("content", ""),
                        }
                    ],
                }
            )
            continue
        if role == "assistant" and m.get("tool_calls"):
            content: list[dict[str, Any]] = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                fn = tc["function"]
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": fn["name"],
                        "input": json.loads(fn["arguments"])
                        if isinstance(fn["arguments"], str)
                        else fn["arguments"],
                    }
                )
            out.append({"role": "assistant", "content": content})
            continue
        out.append(m)
    return out


def _from_anthropic(raw: Any) -> ChatResponse:
    """Convert Anthropic response to neutral ChatResponse."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in raw.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.id,
                    type="function",
                    function=FunctionCall(
                        name=block.name,
                        arguments=json.dumps(block.input, ensure_ascii=False),
                    ),
                )
            )

    finish_reason_map = {
        "end_turn": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
    }
    finish_reason = finish_reason_map.get(raw.stop_reason or "end_turn", "stop")

    usage = ChatUsage(
        prompt_tokens=raw.usage.input_tokens if raw.usage else 0,
        completion_tokens=raw.usage.output_tokens if raw.usage else 0,
        total_tokens=(raw.usage.input_tokens + raw.usage.output_tokens) if raw.usage else 0,
    )

    return ChatResponse(
        choices=[
            ChatChoice(
                message=ChatMessage(
                    content=" ".join(text_parts) if text_parts else None,
                    tool_calls=tool_calls or None,
                    role="assistant",
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
        model=raw.model or "",
    )
