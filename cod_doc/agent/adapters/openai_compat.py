"""openai_compat adapter — wraps AsyncOpenAI (PCA-301, proposal 10).

This is the default adapter; it preserves the exact existing behavior
by delegating to `openai.AsyncOpenAI`. The `chat()` method returns a
:class:`ChatResponse` built from the OpenAI SDK response object, which
the orchestrator then consumes through the neutral types.

Works with any OpenAI-compatible endpoint: OpenRouter, vLLM, LM Studio,
llama-cpp-python, etc.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cod_doc.agent.adapters.base import (
    AdapterCapabilities,
    ChatChoice,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    FunctionCall,
    ToolCall,
)
from cod_doc.agent.retry import with_retry


class OpenAICompatAdapter:
    """OpenAI-compatible adapter (OpenRouter / vLLM / llama.cpp / etc.).

    This is the default adapter and preserves the exact existing
    orchestrator behavior — no behavioral change at all for users who
    don't configure a different adapter.
    """

    name: str = "openai_compat"
    capabilities: AdapterCapabilities = AdapterCapabilities(
        tool_use=True,
        streaming=False,
        json_mode=True,
        vision=False,
        parallel_tool_calls=False,
    )

    def __init__(self, *, api_key: str, base_url: str, **kwargs: Any) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/cod-doc",
                "X-Title": "COD-DOC Orchestrator",
            },
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> ChatResponse:
        """Call the OpenAI-compatible chat endpoint and normalise the response."""
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=max_tokens,
        )
        if temperature is not None:
            kwargs["temperature"] = temperature

        raw = await with_retry(
            lambda: self._client.chat.completions.create(**kwargs)  # type: ignore[call-overload]
        )
        return _from_openai(raw)

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def cost_estimate(
        self, input_tokens: int, output_tokens: int, *, model: str
    ) -> Decimal:
        """PCA-925: Estimate USD cost from a static pricing table.

        Falls back to ``Decimal(0)`` for unknown models.  The table is
        manually maintained — see ``_PRICING_USD_PER_MTOK`` below.
        Prices represent USD per 1 million tokens.
        """
        rates = _PRICING_USD_PER_MTOK.get(model)
        if rates is None:
            # Strip provider prefix (e.g. "anthropic/claude-3-5-sonnet" → "claude-3-5-sonnet")
            stripped = model.rsplit("/", 1)[-1]
            rates = _PRICING_USD_PER_MTOK.get(stripped)
        if rates is None:
            return Decimal(0)
        in_rate, out_rate = rates
        per_mtok = Decimal(1_000_000)
        return (
            (Decimal(input_tokens) * in_rate / per_mtok)
            + (Decimal(output_tokens) * out_rate / per_mtok)
        )


# PCA-925: USD per 1M tokens (input, output). Manually maintained snapshot
# of OpenRouter / OpenAI / Anthropic pricing.  Update as needed.
_PRICING_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    # OpenRouter routes
    "anthropic/claude-sonnet-4-6":  (Decimal("3.00"),  Decimal("15.00")),
    "anthropic/claude-opus-4":      (Decimal("15.00"), Decimal("75.00")),
    "anthropic/claude-3-5-sonnet":  (Decimal("3.00"),  Decimal("15.00")),
    "anthropic/claude-3-5-haiku":   (Decimal("0.80"),  Decimal("4.00")),
    "openai/gpt-4o":                (Decimal("2.50"),  Decimal("10.00")),
    "openai/gpt-4o-mini":           (Decimal("0.15"),  Decimal("0.60")),
    "openai/o1":                    (Decimal("15.00"), Decimal("60.00")),
    "google/gemini-2.0-flash":      (Decimal("0.10"),  Decimal("0.40")),
    "google/gemini-pro":            (Decimal("1.25"),  Decimal("5.00")),
    # Bare model names (no provider prefix)
    "claude-sonnet-4-6":            (Decimal("3.00"),  Decimal("15.00")),
    "claude-3-5-sonnet":            (Decimal("3.00"),  Decimal("15.00")),
    "gpt-4o":                       (Decimal("2.50"),  Decimal("10.00")),
    "gpt-4o-mini":                  (Decimal("0.15"),  Decimal("0.60")),
}


def _from_openai(raw: Any) -> ChatResponse:
    """Convert an OpenAI SDK response to the neutral ChatResponse."""
    choices: list[ChatChoice] = []
    for ch in raw.choices:
        msg = ch.message
        tool_calls: list[ToolCall] | None = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    type=tc.type,
                    function=FunctionCall(
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )
                for tc in msg.tool_calls
            ]
        choices.append(
            ChatChoice(
                message=ChatMessage(
                    content=msg.content,
                    tool_calls=tool_calls,
                    role=msg.role,
                ),
                finish_reason=ch.finish_reason or "stop",
                index=ch.index,
            )
        )
    usage = ChatUsage()
    if raw.usage:
        usage = ChatUsage(
            prompt_tokens=raw.usage.prompt_tokens or 0,
            completion_tokens=raw.usage.completion_tokens or 0,
            total_tokens=raw.usage.total_tokens or 0,
        )
    return ChatResponse(choices=choices, usage=usage, model=raw.model or "")
