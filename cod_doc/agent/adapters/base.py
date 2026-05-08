"""LLMAdapter Protocol + neutral response types (PCA-300, proposal 10).

The orchestrator uses these types so it is fully decoupled from any
specific LLM SDK. Each adapter converts FROM its native SDK format TO
these types.

Design choice: the neutral format mirrors the OpenAI response structure
(it is already the de-facto standard via `openai-compat`). This means
the `openai_compat` adapter is a trivial pass-through; the `anthropic`
adapter converts Anthropic's response INTO this format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


# --------------------------------------------------------------------------- #
# Neutral response types                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class FunctionCall:
    name: str
    arguments: str  # JSON string


@dataclass
class ToolCall:
    id: str
    type: str  # always "function"
    function: FunctionCall


@dataclass
class ChatMessage:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    role: str = "assistant"

    def model_dump(self, *, exclude_none: bool = True) -> dict[str, Any]:
        """Serialise to a dict compatible with the OpenAI messages list."""
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None or not exclude_none:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        elif not exclude_none:
            d["tool_calls"] = None
        return d


@dataclass
class ChatChoice:
    message: ChatMessage
    finish_reason: str = "stop"
    index: int = 0


@dataclass
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResponse:
    choices: list[ChatChoice]
    usage: ChatUsage = field(default_factory=ChatUsage)
    model: str = ""


# --------------------------------------------------------------------------- #
# Capabilities                                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class AdapterCapabilities:
    tool_use: bool = True
    streaming: bool = False
    json_mode: bool = False
    vision: bool = False
    parallel_tool_calls: bool = False


# --------------------------------------------------------------------------- #
# Protocol                                                                      #
# --------------------------------------------------------------------------- #


@runtime_checkable
class LLMAdapter(Protocol):
    """Backend-agnostic interface for a chat-completion provider.

    Implement this protocol and register in :mod:`.registry` to add a
    new LLM backend without touching the orchestrator.
    """

    name: str
    capabilities: AdapterCapabilities

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> ChatResponse:
        """Single-shot (non-streaming) chat completion."""
        ...

    def estimate_tokens(self, text: str) -> int:
        """Rough token count — used for budget checks."""
        ...

    def cost_estimate(
        self, input_tokens: int, output_tokens: int, *, model: str
    ) -> Decimal:
        """Estimated cost in USD (may be zero if unknown)."""
        ...
