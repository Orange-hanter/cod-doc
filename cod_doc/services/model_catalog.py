"""Curated LLM-model catalog (COD-059).

A small static list with context-window and per-1M-token pricing, used by the
settings UI to render a dropdown with model descriptions. ``LITE_CATALOG`` is
the cheap subset for UI helpers, ``BUNDLES`` are one-click pairs of main +
lite model.

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


_PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "deepseek": "DeepSeek",
    "meta-llama": "Meta",
}

_LITE_IDS = frozenset(
    {
        "anthropic/claude-haiku-4-5",
        "openai/gpt-4.1-mini",
        "meta-llama/llama-3.3-70b-instruct",
    }
)


def provider_label(info: ModelInfo) -> str:
    """Human name of the vendor, for an ``<optgroup>``."""
    slug = info.model_id.split("/", 1)[0]
    return _PROVIDER_LABELS.get(slug, slug)


def grouped(
    catalog: tuple[ModelInfo, ...],
) -> tuple[tuple[str, tuple[ModelInfo, ...]], ...]:
    """Bucket a catalog by vendor, preserving first-seen order."""
    order: list[str] = []
    buckets: dict[str, list[ModelInfo]] = {}
    for info in catalog:
        label = provider_label(info)
        bucket = buckets.get(label)
        if bucket is None:
            order.append(label)
            bucket = []
            buckets[label] = bucket
        bucket.append(info)
    return tuple((label, tuple(buckets[label])) for label in order)


# Cheap models for doc-suggest and other UI helpers. Order follows CATALOG.
LITE_CATALOG: tuple[ModelInfo, ...] = tuple(m for m in CATALOG if m.model_id in _LITE_IDS)


def is_lite_known(model_id: str) -> bool:
    """True if ``model_id`` is one of the lite presets."""
    return any(m.model_id == model_id for m in LITE_CATALOG)


@dataclass(frozen=True, slots=True)
class ModelBundle:
    """A main-model + lite-model pair the settings page can apply in one click."""

    bundle_id: str
    label: str
    blurb: str
    model: str
    lite_model: str


BUNDLES: tuple[ModelBundle, ...] = (
    ModelBundle(
        bundle_id="quality",
        label="Quality",
        blurb="Opus for the agent, Haiku for quick UI fills.",
        model="anthropic/claude-opus-4",
        lite_model="anthropic/claude-haiku-4-5",
    ),
    ModelBundle(
        bundle_id="balanced",
        label="Balanced",
        blurb="Sonnet 4.6 plus a cheap helper. The usual default.",
        model="anthropic/claude-sonnet-4-6",
        lite_model="openai/gpt-4.1-mini",
    ),
    ModelBundle(
        bundle_id="economy",
        label="Economy",
        blurb="Llama 3.3 for the agent, GPT-4.1 mini for helpers.",
        model="meta-llama/llama-3.3-70b-instruct",
        lite_model="openai/gpt-4.1-mini",
    ),
)


def matching_bundle(model: str, lite_model: str) -> ModelBundle | None:
    """Return the bundle whose pair equals the current config, else None."""
    for bundle in BUNDLES:
        if bundle.model == model and bundle.lite_model == lite_model:
            return bundle
    return None


@dataclass(frozen=True, slots=True)
class SourceModel:
    """One model id as a given provider's API actually expects it."""

    model_id: str
    label: str
    notes: str = ""
    lite: bool = False


@dataclass(frozen=True, slots=True)
class ProviderFamily:
    """Top-level API shape. Picking one drops providers that don't speak it."""

    family_id: str
    label: str
    blurb: str


@dataclass(frozen=True, slots=True)
class ChatProvider:
    """A concrete endpoint: base URL, key requirement and the ids it accepts.

    ``fallback_key`` is written on save when the provider needs no real secret
    but the OpenAI client refuses an empty key (local Ollama).
    """

    provider_id: str
    family: str
    label: str
    blurb: str
    base_url: str
    needs_key: bool
    models: tuple[SourceModel, ...]
    allows_bundles: bool = False
    key_note: str = ""
    fallback_key: str = ""


FAMILIES: tuple[ProviderFamily, ...] = (
    ProviderFamily(
        "openrouter",
        "OpenRouter",
        "Один ключ на все вендоры. Id вида vendor/model.",
    ),
    ProviderFamily(
        "vendor",
        "Direct API",
        "Прямой OpenAI-совместимый API. Id без префикса вендора.",
    ),
    ProviderFamily(
        "ollama",
        "Ollama",
        "Облако или локальный демон. Id — тег модели.",
    ),
    ProviderFamily(
        "custom",
        "Custom",
        "Свой адрес и произвольный id.",
    ),
)

_OLLAMA_MODELS: tuple[SourceModel, ...] = (
    SourceModel("qwen3.5:397b", "Qwen3.5 397B", "Большая модель Ollama."),
    SourceModel("gemma4:31b", "Gemma 4 31B", "Легче 397B, для подсказок.", lite=True),
    SourceModel("llama3.3", "Llama 3.3", "Открытые веса."),
    SourceModel("deepseek-r1", "DeepSeek R1", "Рассуждения."),
)


def _openrouter_models() -> tuple[SourceModel, ...]:
    return tuple(
        SourceModel(m.model_id, m.describe(), m.notes, lite=m.model_id in _LITE_IDS)
        for m in CATALOG
    )


PROVIDERS: tuple[ChatProvider, ...] = (
    ChatProvider(
        provider_id="openrouter",
        family="openrouter",
        label="OpenRouter",
        blurb="Маршруты vendor/model.",
        base_url="https://openrouter.ai/api/v1",
        needs_key=True,
        models=_openrouter_models(),
        allows_bundles=True,
        key_note="Ключ с openrouter.ai.",
    ),
    ChatProvider(
        provider_id="openai",
        family="vendor",
        label="OpenAI",
        blurb="api.openai.com, id вида gpt-4.1-mini.",
        base_url="https://api.openai.com/v1",
        needs_key=True,
        models=(
            SourceModel("gpt-5", "GPT-5", "Флагман OpenAI."),
            SourceModel("gpt-4.1", "GPT-4.1", "Сильная общая модель."),
            SourceModel("gpt-4.1-mini", "GPT-4.1 mini", "Дешёвая, для подсказок.", lite=True),
        ),
        key_note="Ключ с platform.openai.com.",
    ),
    ChatProvider(
        provider_id="anthropic",
        family="vendor",
        label="Anthropic",
        blurb="Совместимый endpoint, id вида claude-sonnet-4-6.",
        base_url="https://api.anthropic.com/v1",
        needs_key=True,
        models=(
            SourceModel("claude-sonnet-4-6", "Claude Sonnet 4.6", "Основная рабочая модель."),
            SourceModel("claude-opus-4", "Claude Opus 4", "Максимум качества."),
            SourceModel(
                "claude-haiku-4-5", "Claude Haiku 4.5", "Быстрая, для подсказок.", lite=True
            ),
        ),
        key_note="Ключ с console.anthropic.com.",
    ),
    ChatProvider(
        provider_id="gemini",
        family="vendor",
        label="Gemini",
        blurb="OpenAI-совместимый вход Google.",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        needs_key=True,
        models=(
            SourceModel("gemini-2.5-pro", "Gemini 2.5 Pro", "Длинный контекст."),
            SourceModel(
                "gemini-2.5-flash", "Gemini 2.5 Flash", "Быстрая, для подсказок.", lite=True
            ),
        ),
        key_note="Ключ из Google AI Studio.",
    ),
    ChatProvider(
        provider_id="deepseek",
        family="vendor",
        label="DeepSeek",
        blurb="Прямой API DeepSeek.",
        base_url="https://api.deepseek.com/v1",
        needs_key=True,
        models=(
            SourceModel("deepseek-reasoner", "DeepSeek Reasoner", "Рассуждения."),
            SourceModel("deepseek-chat", "DeepSeek Chat", "Обычный чат, дешевле.", lite=True),
        ),
        key_note="Ключ с platform.deepseek.com.",
    ),
    ChatProvider(
        provider_id="groq",
        family="vendor",
        label="Groq",
        blurb="Быстрый инференс, id без префикса.",
        base_url="https://api.groq.com/openai/v1",
        needs_key=True,
        models=(
            SourceModel("llama-3.3-70b-versatile", "Llama 3.3 70B", "Основная модель Groq."),
            SourceModel(
                "llama-3.1-8b-instant",
                "Llama 3.1 8B",
                "Мгновенные подсказки.",
                lite=True,
            ),
        ),
        key_note="Ключ с console.groq.com.",
    ),
    ChatProvider(
        provider_id="ollama-cloud",
        family="ollama",
        label="Ollama Cloud",
        blurb="ollama.com, тег вида qwen3.5:397b.",
        base_url="https://ollama.com/v1",
        needs_key=True,
        models=_OLLAMA_MODELS,
        key_note="Ключ Ollama Cloud.",
    ),
    ChatProvider(
        provider_id="ollama-local",
        family="ollama",
        label="Ollama local",
        blurb="Демон на этой машине. Ключ не нужен.",
        base_url="http://127.0.0.1:11434/v1",
        needs_key=False,
        models=_OLLAMA_MODELS,
        fallback_key="ollama",
    ),
    ChatProvider(
        provider_id="custom",
        family="custom",
        label="Custom",
        blurb="Адрес и id задаются вручную.",
        base_url="",
        needs_key=True,
        models=(),
        key_note="Ключ этого endpoint.",
    ),
)


def _norm_base_url(url: str) -> str:
    value = url.strip().rstrip("/").lower()
    return value.replace("://localhost", "://127.0.0.1")


def matching_provider(base_url: str) -> ChatProvider:
    """Provider whose endpoint equals ``base_url``, else the custom one."""
    norm = _norm_base_url(base_url)
    for provider in PROVIDERS:
        if provider.base_url and _norm_base_url(provider.base_url) == norm:
            return provider
    return PROVIDERS[-1]


def providers_in(family_id: str) -> tuple[ChatProvider, ...]:
    """Providers that belong to one API family, in catalog order."""
    return tuple(provider for provider in PROVIDERS if provider.family == family_id)


def find_source_model(provider: ChatProvider, model_id: str) -> SourceModel | None:
    """Preset of this provider with ``model_id``, else None."""
    for model in provider.models:
        if model.model_id == model_id:
            return model
    return None
