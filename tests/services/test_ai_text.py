"""COD-067: ai_text.improve_text — guards + backend integration."""

from __future__ import annotations

from typing import ClassVar

import pytest

from cod_doc.config import Config
from cod_doc.services import ai_text


def _cfg(api_key: str = "sk-test") -> Config:
    return Config(
        api_key=api_key,
        model="test/model",
        base_url="https://openrouter.ai/api/v1",
    )


def test_raises_when_api_key_missing() -> None:
    with pytest.raises(ai_text.AIBackendError, match="API key"):
        ai_text.improve_text("some text", "make formal", cfg=_cfg(api_key=""))


def test_raises_on_empty_text() -> None:
    with pytest.raises(ai_text.AIBackendError, match="empty"):
        ai_text.improve_text("   ", "make formal", cfg=_cfg())


def test_calls_openai_and_returns_content(monkeypatch) -> None:
    """The service forwards messages to OpenAI and returns the .content string."""

    captured: dict = {}

    class FakeMessage:
        content = "Improved text."

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletion:
        choices: ClassVar[list[FakeChoice]] = [FakeChoice()]

    class FakeChat:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeCompletion()

    class FakeChatNamespace:
        completions = FakeChat()

    class FakeClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.chat = FakeChatNamespace()

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    out = ai_text.improve_text("Original.", "be concise", cfg=_cfg())
    assert out == "Improved text."
    # init args carry api key + base url from the cfg
    assert captured["init"]["api_key"] == "sk-test"
    assert captured["init"]["base_url"] == "https://openrouter.ai/api/v1"
    # The user prompt embeds intent + the original text
    user_msg = captured["messages"][1]["content"]
    assert "Intent: be concise" in user_msg
    assert "Original." in user_msg
    # Empty intent falls back to "(default)"
    captured.clear()
    ai_text.improve_text("Original.", "", cfg=_cfg())
    assert "Intent: (default)" in captured["messages"][1]["content"]


def test_wraps_backend_exception_in_aibackend_error(monkeypatch) -> None:
    class Boom:
        def __init__(self, **kwargs):
            self.chat = type(
                "_C",
                (),
                {
                    "completions": type(
                        "_C2",
                        (),
                        {
                            "create": lambda *a, **kw: (_ for _ in ()).throw(
                                RuntimeError("network down")
                            ),
                        },
                    )()
                },
            )()

    monkeypatch.setattr("openai.OpenAI", Boom)

    with pytest.raises(ai_text.AIBackendError, match="network down"):
        ai_text.improve_text("Some text.", "", cfg=_cfg())


def test_raises_when_completion_is_empty(monkeypatch) -> None:
    class FakeMessage:
        content = "   "

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletion:
        choices: ClassVar[list[FakeChoice]] = [FakeChoice()]

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type(
                "_C",
                (),
                {"completions": type("_C2", (), {"create": lambda *a, **kw: FakeCompletion()})()},
            )()

    monkeypatch.setattr("openai.OpenAI", FakeClient)

    with pytest.raises(ai_text.AIBackendError, match="empty"):
        ai_text.improve_text("Some text.", "", cfg=_cfg())
