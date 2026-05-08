"""Mock adapter — deterministic responses for testing (PCA-301, proposal 10).

Eliminates real LLM calls from orchestrator unit-tests. The mock is
configured with a sequence of responses; each call to `chat()` pops the
next response from the queue. If the queue is empty, it returns a
default "done" message.

Usage in tests::

    from cod_doc.agent.adapters.mock import MockAdapter

    adapter = MockAdapter(responses=[
        MockAdapter.tool_call_response("task_complete", {"project": "...", "task_id": "X"}),
        MockAdapter.text_response("Task completed."),
    ])
    orchestrator = Orchestrator(project, config, adapter=adapter)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from cod_doc.agent.adapters.base import (
    AdapterCapabilities,
    ChatChoice,
    ChatChunk,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    FunctionCall,
    ToolCall,
)


class MockAdapter:
    """Deterministic LLM adapter for tests.

    Pre-configure with a list of :class:`ChatResponse` objects (or use
    the class-method helpers :meth:`text_response` and
    :meth:`tool_call_response`). Each `chat()` call consumes the next
    response from the queue; when exhausted, a plain text "done" response
    is returned so the agent loop terminates cleanly.
    """

    name: str = "mock"
    # PCA-924: streaming=True so the orchestrator can opt into stream_chat.
    capabilities: AdapterCapabilities = AdapterCapabilities(
        tool_use=True,
        streaming=True,
        json_mode=True,
        vision=False,
        parallel_tool_calls=True,
    )

    def __init__(self, responses: list[ChatResponse] | None = None) -> None:
        self._queue: list[ChatResponse] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> ChatResponse:
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        if self._queue:
            return self._queue.pop(0)
        return self.text_response("Task complete.")

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """PCA-924: yield the next queued response as a sequence of chunks.

        For text responses, splits content into ~5-char chunks so tests
        can assert incremental delivery.  For tool calls, yields the
        whole tool_call as one chunk followed by a finish chunk.
        """
        self.calls.append({"messages": messages, "tools": tools, "model": model, "_streaming": True})
        resp = self._queue.pop(0) if self._queue else self.text_response("Task complete.")
        choice = resp.choices[0]
        msg = choice.message

        if msg.tool_calls:
            for tc in msg.tool_calls:
                yield ChatChunk(tool_call_delta=tc)
            yield ChatChunk(finish_reason=choice.finish_reason)
            return

        text = msg.content or ""
        # Stream in 5-char chunks for testability.
        for i in range(0, len(text), 5):
            yield ChatChunk(content_delta=text[i : i + 5])
        yield ChatChunk(finish_reason=choice.finish_reason)

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def cost_estimate(
        self, input_tokens: int, output_tokens: int, *, model: str
    ) -> Decimal:
        return Decimal(0)

    # ------------------------------------------------------------------ #
    # Helpers for building test responses                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def text_response(content: str, *, finish_reason: str = "stop") -> ChatResponse:
        """Return a response with text content (no tool calls)."""
        return ChatResponse(
            choices=[
                ChatChoice(
                    message=ChatMessage(content=content, tool_calls=None),
                    finish_reason=finish_reason,
                )
            ],
            usage=ChatUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    @staticmethod
    def tool_call_response(
        tool_name: str, arguments: dict[str, Any], *, call_id: str | None = None
    ) -> ChatResponse:
        """Return a response containing a single tool call."""
        return ChatResponse(
            choices=[
                ChatChoice(
                    message=ChatMessage(
                        content=None,
                        tool_calls=[
                            ToolCall(
                                id=call_id or f"call_{uuid.uuid4().hex[:8]}",
                                type="function",
                                function=FunctionCall(
                                    name=tool_name,
                                    arguments=json.dumps(arguments, ensure_ascii=False),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=ChatUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )
