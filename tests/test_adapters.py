"""PCA-300/301/302: LLMAdapter Protocol + adapters + registry."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from cod_doc.agent.adapters.base import (
    AdapterCapabilities,
    ChatMessage,
    LLMAdapter,
)
from cod_doc.agent.adapters.mock import MockAdapter
from cod_doc.agent.adapters.registry import (
    get_adapter,
    list_adapters,
    register_adapter,
)

# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.get_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- #
# ChatMessage.model_dump                                                        #
# --------------------------------------------------------------------------- #


class TestChatMessageModelDump:
    def test_text_only(self) -> None:
        msg = ChatMessage(content="hello", tool_calls=None)
        d = msg.model_dump()
        assert d["content"] == "hello"
        assert "tool_calls" not in d  # excluded when None + exclude_none=True

    def test_tool_calls_serialised(self) -> None:
        from cod_doc.agent.adapters.base import FunctionCall, ToolCall

        tc = ToolCall(id="c1", type="function", function=FunctionCall(name="foo", arguments='{"x":1}'))
        msg = ChatMessage(content=None, tool_calls=[tc])
        d = msg.model_dump()
        assert d["tool_calls"][0]["id"] == "c1"
        assert d["tool_calls"][0]["function"]["name"] == "foo"
        assert "content" not in d  # excluded when None

    def test_exclude_none_false_includes_none_fields(self) -> None:
        msg = ChatMessage(content=None, tool_calls=None)
        d = msg.model_dump(exclude_none=False)
        assert "content" in d
        assert "tool_calls" in d


# --------------------------------------------------------------------------- #
# MockAdapter                                                                   #
# --------------------------------------------------------------------------- #


class TestMockAdapter:
    def test_text_response_helper(self) -> None:
        r = MockAdapter.text_response("hi there")
        assert r.choices[0].message.content == "hi there"
        assert r.choices[0].message.tool_calls is None
        assert r.choices[0].finish_reason == "stop"

    def test_tool_call_response_helper(self) -> None:
        r = MockAdapter.tool_call_response("task_complete", {"project": "x", "task_id": "T1"})
        tc = r.choices[0].message.tool_calls[0]
        assert tc.function.name == "task_complete"
        args = json.loads(tc.function.arguments)
        assert args["task_id"] == "T1"
        assert r.choices[0].finish_reason == "tool_calls"

    def test_queue_consumed_in_order(self) -> None:
        adapter = MockAdapter(responses=[
            MockAdapter.text_response("first"),
            MockAdapter.text_response("second"),
        ])
        r1 = run(adapter.chat([], [], model="m", max_tokens=100))
        r2 = run(adapter.chat([], [], model="m", max_tokens=100))
        assert r1.choices[0].message.content == "first"
        assert r2.choices[0].message.content == "second"

    def test_empty_queue_returns_default(self) -> None:
        adapter = MockAdapter()
        r = run(adapter.chat([], [], model="m", max_tokens=100))
        assert r.choices[0].message.content == "Task complete."

    def test_calls_recorded(self) -> None:
        adapter = MockAdapter()
        run(adapter.chat([{"role": "user", "content": "go"}], [], model="x", max_tokens=10))
        assert len(adapter.calls) == 1
        assert adapter.calls[0]["model"] == "x"

    def test_cost_estimate_is_zero(self) -> None:
        adapter = MockAdapter()
        assert adapter.cost_estimate(100, 50, model="any") == Decimal(0)

    def test_estimate_tokens(self) -> None:
        adapter = MockAdapter()
        assert adapter.estimate_tokens("1234") == 1

    def test_satisfies_protocol(self) -> None:
        adapter = MockAdapter()
        assert isinstance(adapter, LLMAdapter)

    def test_capabilities(self) -> None:
        assert MockAdapter.capabilities.tool_use is True
        assert MockAdapter.capabilities.parallel_tool_calls is True
        # PCA-924: MockAdapter advertises streaming for tests of stream_chat.
        assert MockAdapter.capabilities.streaming is True

    def test_stream_chat_yields_chunks(self) -> None:
        # PCA-924
        from cod_doc.agent.adapters.base import ChatChunk, supports_streaming

        adapter = MockAdapter([MockAdapter.text_response("ABCDEFGHIJK")])
        assert supports_streaming(adapter)

        async def collect() -> list[ChatChunk]:
            out = []
            async for c in adapter.stream_chat([], [], model="m", max_tokens=100):
                out.append(c)
            return out

        chunks = run(collect())
        text = "".join(c.content_delta or "" for c in chunks)
        assert text == "ABCDEFGHIJK"
        assert chunks[-1].finish_reason == "stop"

    def test_stream_chat_tool_call(self) -> None:
        # PCA-924
        from cod_doc.agent.adapters.base import ChatChunk

        adapter = MockAdapter([
            MockAdapter.tool_call_response("read_file", {"path": "x.md"}, call_id="c1"),
        ])

        async def collect() -> list[ChatChunk]:
            out = []
            async for c in adapter.stream_chat([], [], model="m", max_tokens=100):
                out.append(c)
            return out

        chunks = run(collect())
        # Expect: 1 tool_call chunk + 1 finish chunk.
        assert chunks[0].tool_call_delta is not None
        assert chunks[0].tool_call_delta.function.name == "read_file"
        assert chunks[-1].finish_reason == "tool_calls"


# --------------------------------------------------------------------------- #
# AdapterCapabilities                                                           #
# --------------------------------------------------------------------------- #


class TestAdapterCapabilities:
    def test_defaults(self) -> None:
        caps = AdapterCapabilities()
        assert caps.tool_use is True
        assert caps.streaming is False
        assert caps.vision is False

    def test_custom(self) -> None:
        caps = AdapterCapabilities(streaming=True, vision=True)
        assert caps.streaming is True


# --------------------------------------------------------------------------- #
# Registry                                                                      #
# --------------------------------------------------------------------------- #


class TestRegistry:
    def test_built_in_adapters_registered(self) -> None:
        names = list_adapters()
        assert "openai_compat" in names
        assert "anthropic" in names
        assert "mock" in names

    def test_get_mock_adapter(self) -> None:
        from unittest.mock import MagicMock
        config = MagicMock()
        config.llm_adapter = "mock"
        adapter = get_adapter("mock", config)
        assert adapter.name == "mock"

    def test_get_unknown_raises_key_error(self) -> None:
        from unittest.mock import MagicMock
        config = MagicMock()
        with pytest.raises(KeyError, match="no_such_adapter"):
            get_adapter("no_such_adapter", config)

    def test_register_and_retrieve_custom(self) -> None:
        from unittest.mock import MagicMock
        config = MagicMock()
        register_adapter("test_custom", lambda _cfg: MockAdapter())
        adapter = get_adapter("test_custom", config)
        assert adapter.name == "mock"
        # cleanup
        from cod_doc.agent.adapters import registry
        del registry._REGISTRY["test_custom"]


# --------------------------------------------------------------------------- #
# Anthropic format converters (unit, no SDK call)                              #
# --------------------------------------------------------------------------- #


class TestAnthropicFormatConversion:
    def test_to_anthropic_tools(self) -> None:
        from cod_doc.agent.adapters.anthropic import _to_anthropic_tools

        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "task_complete",
                    "description": "Mark task done",
                    "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}},
                },
            }
        ]
        result = _to_anthropic_tools(oai_tools)
        assert result[0]["name"] == "task_complete"
        assert result[0]["input_schema"]["type"] == "object"
        assert "description" in result[0]

    def test_to_anthropic_messages_skips_system(self) -> None:
        from cod_doc.agent.adapters.anthropic import _to_anthropic_messages

        msgs = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi"},
        ]
        out = _to_anthropic_messages(msgs)
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert out[0]["content"] == "hi"

    def test_to_anthropic_messages_converts_tool_result(self) -> None:
        from cod_doc.agent.adapters.anthropic import _to_anthropic_messages

        msgs = [{"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'}]
        out = _to_anthropic_messages(msgs)
        assert out[0]["role"] == "user"
        assert out[0]["content"][0]["type"] == "tool_result"
        assert out[0]["content"][0]["tool_use_id"] == "c1"

    def test_to_anthropic_messages_converts_assistant_tool_calls(self) -> None:
        from cod_doc.agent.adapters.anthropic import _to_anthropic_messages

        msgs = [{
            "role": "assistant",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "fn", "arguments": '{"x":1}'},
            }],
        }]
        out = _to_anthropic_messages(msgs)
        assert out[0]["content"][0]["type"] == "tool_use"
        assert out[0]["content"][0]["name"] == "fn"
        assert out[0]["content"][0]["input"] == {"x": 1}

    def test_from_anthropic_text_block(self) -> None:
        from unittest.mock import MagicMock

        from cod_doc.agent.adapters.anthropic import _from_anthropic

        block = MagicMock()
        block.type = "text"
        block.text = "hello world"

        raw = MagicMock()
        raw.content = [block]
        raw.stop_reason = "end_turn"
        raw.usage.input_tokens = 10
        raw.usage.output_tokens = 5
        raw.model = "claude-test"

        resp = _from_anthropic(raw)
        assert resp.choices[0].message.content == "hello world"
        assert resp.choices[0].message.tool_calls is None
        assert resp.choices[0].finish_reason == "stop"

    def test_from_anthropic_tool_use_block(self) -> None:
        from unittest.mock import MagicMock

        from cod_doc.agent.adapters.anthropic import _from_anthropic

        block = MagicMock()
        block.type = "tool_use"
        block.id = "call_abc"
        block.name = "task_complete"
        block.input = {"project": "p", "task_id": "T1"}

        raw = MagicMock()
        raw.content = [block]
        raw.stop_reason = "tool_use"
        raw.usage.input_tokens = 20
        raw.usage.output_tokens = 10
        raw.model = "claude-test"

        resp = _from_anthropic(raw)
        assert resp.choices[0].finish_reason == "tool_calls"
        tc = resp.choices[0].message.tool_calls[0]
        assert tc.id == "call_abc"
        assert tc.function.name == "task_complete"
        assert json.loads(tc.function.arguments)["task_id"] == "T1"


# --------------------------------------------------------------------------- #
# Orchestrator DI (smoke)                                                       #
# --------------------------------------------------------------------------- #


class TestOrchestratorAdapterDI:
    def test_orchestrator_accepts_adapter_kwarg(self) -> None:
        """Orchestrator.__init__ wires adapter attribute when passed."""
        from unittest.mock import MagicMock, patch

        mock_project = MagicMock()
        mock_config = MagicMock()
        mock_config.api_key = ""
        mock_config.base_url = ""
        mock_config.chroma_path = "/tmp/chroma"
        mock_config.embedding_backend = "local"
        mock_config.embedding_model = "all-MiniLM-L6-v2"
        mock_config.llm_adapter = "mock"

        adapter = MockAdapter()
        with patch("cod_doc.agent.orchestrator.ToolExecutor"):
            from cod_doc.agent.orchestrator import Orchestrator
            orch = Orchestrator(mock_project, mock_config, adapter=adapter)

        assert orch.adapter is adapter
        assert orch.adapter.name == "mock"

    def test_orchestrator_picks_mock_from_config(self) -> None:
        """get_adapter_from_config selects 'mock' when config.llm_adapter='mock'."""
        from unittest.mock import MagicMock, patch

        mock_config = MagicMock()
        mock_config.llm_adapter = "mock"

        with patch("cod_doc.agent.orchestrator.ToolExecutor"):
            from cod_doc.agent.adapters.registry import get_adapter_from_config
            adapter = get_adapter_from_config(mock_config)

        assert adapter.name == "mock"
