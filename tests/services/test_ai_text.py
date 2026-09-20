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


# ── _call_lite_raw: бюджет и reasoning ─────────────────────────────────────


def _fake_openai(monkeypatch, completion: object, captured: dict) -> None:  # type: ignore[no-untyped-def]
    """Подменить клиента OpenAI и запомнить аргументы вызова."""

    class FakeChat:
        def create(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return completion

    class FakeClient:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            self.chat = type("_C", (), {"completions": FakeChat()})()

    monkeypatch.setattr("openai.OpenAI", FakeClient)


def _completion(content: str, finish_reason: str, reasoning_tokens: int) -> object:
    message = type("_M", (), {"content": content})()
    choice = type("_Ch", (), {"message": message, "finish_reason": finish_reason})()
    details = type("_D", (), {"reasoning_tokens": reasoning_tokens})()
    usage = type("_U", (), {"completion_tokens_details": details})()
    return type("_Cm", (), {"choices": [choice], "usage": usage})()


def test_lite_call_turns_reasoning_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Иначе рассуждения съедят `max_tokens` до первого токена ответа."""
    captured: dict = {}
    _fake_openai(monkeypatch, _completion('{"ok": true}', "stop", 0), captured)

    assert ai_text._call_lite_raw("prompt", _cfg(), max_tokens=400) == '{"ok": true}'
    assert captured["extra_body"] == {"reasoning": {"enabled": False}}


def test_budget_spent_on_reasoning_is_named_as_such(monkeypatch) -> None:
    """Пустой ответ из-за рассуждений нельзя показывать как «пустой ответ».

    Замер на живой модели: 2740 токенов, все до единого reasoning,
    `finish_reason="length"`, `content` пуст. С прежним текстом ошибки причину
    ищут в сети или в ключе, а она в бюджете.
    """
    captured: dict = {}
    _fake_openai(monkeypatch, _completion("", "length", 2740), captured)

    with pytest.raises(ai_text.AIBackendError, match="рассуждения"):
        ai_text._call_lite_raw("prompt", _cfg(), max_tokens=1500)


def test_other_empty_answers_carry_the_finish_reason(monkeypatch) -> None:
    captured: dict = {}
    _fake_openai(monkeypatch, _completion("", "content_filter", 0), captured)

    with pytest.raises(ai_text.AIBackendError, match="content_filter"):
        ai_text._call_lite_raw("prompt", _cfg(), max_tokens=400)
