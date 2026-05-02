"""Static config — supported LLM models + step labels + widget-id helper."""

from __future__ import annotations

# (model_id, display_label)
MODELS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6  ⭐ рекомендуется"),
    ("anthropic/claude-opus-4-6", "Claude Opus 4.6    💪 мощнее, дороже"),
    ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5   ⚡ быстрее, дешевле"),
    ("openai/gpt-4o", "GPT-4o"),
    ("openai/gpt-4o-mini", "GPT-4o Mini"),
    ("meta-llama/llama-3.1-70b-instruct", "Llama 3.1 70B (open-source)"),
    ("google/gemini-pro-1.5", "Gemini Pro 1.5"),
]

STEPS = ["Добро пожаловать", "API & модель", "Проект", "Готово"]


def _model_widget_id(model_id: str) -> str:
    """Build a textual-safe widget id from a model identifier."""
    return "model-" + model_id.replace("/", "_").replace(".", "_")
