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
from typing import TYPE_CHECKING, Any

from cod_doc.agent.adapters.base import (
    AdapterCapabilities,
    ChatChoice,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    FunctionCall,
    ToolCall,
)
from cod_doc.agent.retry import ContextLengthExceededError, LLMError, with_retry

if TYPE_CHECKING:
    pass


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
        # Per-model pricing is backend-specific; return 0 as a safe default.
        return Decimal(0)


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
