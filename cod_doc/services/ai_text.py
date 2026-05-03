"""COD-067: small LLM helper for "improve this text" UI actions.

Stays intentionally minimal — one prompt, one return value. Larger AI flows
(task generation, story extraction) live in dedicated services.

Tests monkey-patch ``improve_text`` directly rather than mocking the OpenAI
client, so the prompt text in this module is free to evolve.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cod_doc.config import Config


class AIBackendError(RuntimeError):
    """The LLM backend is unavailable or returned an error."""


_SYSTEM_PROMPT = (
    "You are a senior technical-documentation editor. The user gives you a "
    "passage of project documentation and a short intent describing how it "
    "should be improved. Rewrite the passage according to that intent.\n\n"
    "Rules:\n"
    "- Preserve markdown formatting (lists, fences, headings, links).\n"
    "- Preserve the original language of the passage (do not translate).\n"
    "- Return ONLY the improved text — no explanation, no apology, no preface.\n"
    "- If the intent is empty, default to: tighten wording, fix grammar, "
    "keep the meaning."
)


def improve_text(text: str, intent: str, *, cfg: Config) -> str:
    """Send ``text`` + ``intent`` to the configured LLM and return improved text.

    Raises ``AIBackendError`` on any backend failure or when no API key is
    configured. The caller is expected to surface the message to the user.
    """
    if not cfg.api_key:
        raise AIBackendError(
            "LLM backend not configured: set the API key in /settings."
        )
    if not text.strip():
        raise AIBackendError("Nothing to improve — text is empty.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIBackendError("openai package is not installed.") from exc

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    user_msg = f"Intent: {intent.strip() or '(default)'}\n\nText:\n{text}"

    try:
        completion = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=cfg.max_tokens,
        )
    except Exception as exc:  # openai.OpenAIError, network errors, etc.
        raise AIBackendError(f"LLM call failed: {exc}") from exc

    content = (completion.choices[0].message.content or "").strip()
    if not content:
        raise AIBackendError("LLM returned empty content.")
    return content
