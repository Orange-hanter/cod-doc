"""Curated LLM-model catalog (COD-059).

A small static list with context-window and per-1M-token pricing, used by the
settings UI to render a dropdown with model descriptions.

Pricing/context numbers reflect publicly-listed OpenRouter values at the time
the entry was added. Treat them as informational — the actual cost on a
provider's invoice always wins. Update when a model materially changes
(price drop, deprecation, context bump).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """One row in the model catalog."""

    model_id: str  # OpenRouter route slug
    label: str  # human-readable name shown in the dropdown
    context_window: int  # max input tokens
    input_per_million: float  # USD per 1M input tokens
    output_per_million: float  # USD per 1M output tokens
    notes: str = ""  # short capability hint

    def describe(self) -> str:
        """Format a one-line label for the <option> text."""
        ctx = (
            f"{self.context_window // 1000}K"
            if self.context_window < 1_000_000
            else f"{self.context_window // 1_000_000}M"
        )
        return (
            f"{self.label} — {ctx} ctx · "
            f"${self.input_per_million:g}/${self.output_per_million:g} per 1M tok"
        )


# Order = recommended order in the dropdown (most common / capable first).
CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo(
        model_id="anthropic/claude-sonnet-4-6",
        label="Claude Sonnet 4.6",
        context_window=200_000,
        input_per_million=3.0,
        output_per_million=15.0,
        notes="Default — strong reasoning, large context.",
    ),
    ModelInfo(
        model_id="anthropic/claude-opus-4",
        label="Claude Opus 4",
        context_window=200_000,
        input_per_million=15.0,
        output_per_million=75.0,
        notes="Highest capability; expensive.",
    ),
    ModelInfo(
        model_id="anthropic/claude-haiku-4-5",
        label="Claude Haiku 4.5",
        context_window=200_000,
        input_per_million=1.0,
        output_per_million=5.0,
        notes="Fast/cheap; good for routine edits.",
    ),
    ModelInfo(
        model_id="openai/gpt-5",
        label="GPT-5",
        context_window=400_000,
        input_per_million=5.0,
        output_per_million=20.0,
        notes="Large context window.",
    ),
    ModelInfo(
        model_id="openai/gpt-4.1-mini",
        label="GPT-4.1 mini",
        context_window=1_000_000,
        input_per_million=0.4,
        output_per_million=1.6,
        notes="Cheap with 1M-token context.",
    ),
    ModelInfo(
        model_id="google/gemini-2.5-pro",
        label="Gemini 2.5 Pro",
        context_window=2_000_000,
        input_per_million=1.25,
        output_per_million=5.0,
        notes="Massive context; multimodal-capable.",
    ),
    ModelInfo(
        model_id="deepseek/deepseek-r1",
        label="DeepSeek R1",
        context_window=128_000,
        input_per_million=0.55,
        output_per_million=2.19,
        notes="Strong reasoning at low cost.",
    ),
    ModelInfo(
        model_id="meta-llama/llama-3.3-70b-instruct",
        label="Llama 3.3 70B",
        context_window=128_000,
        input_per_million=0.13,
        output_per_million=0.4,
        notes="Open weights; bargain pricing.",
    ),
)


def get(model_id: str) -> ModelInfo | None:
    """Return the catalog entry matching ``model_id``, else None."""
    for m in CATALOG:
        if m.model_id == model_id:
            return m
    return None


def is_known(model_id: str) -> bool:
    """True if ``model_id`` is in the catalog (used to decide preset vs custom)."""
    return get(model_id) is not None
