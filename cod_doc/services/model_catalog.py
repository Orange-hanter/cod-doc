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
# Prices and context are the OpenRouter listing on 2026-10-02. A leading ``~``
# is an alias that follows the vendor's latest flash/luna model.
# See docs/system/research/2026-10-02-openrouter-presets.md.
CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo(
        model_id="anthropic/claude-sonnet-5.5",
        label="Claude Sonnet 5.5",
        context_window=1_000_000,
        input_per_million=2.0,
        output_per_million=10.0,
        notes="Обычный выбор. Выход до 128K.",
    ),
    ModelInfo(
        model_id="anthropic/claude-opus-5.5",
        label="Claude Opus 5.5",
        context_window=1_000_000,
        input_per_million=4.0,
        output_per_million=20.0,
        notes="Верх списка. Выход до 128K.",
    ),
    ModelInfo(
        model_id="z-ai/glm-5.3-flash",
        label="GLM 5.3 Flash",
        context_window=1_048_576,
        input_per_million=0.15,
        output_per_million=0.5,
        notes="Дешёвый длинный выход. Лучший GDPval из тройки flash.",
    ),
    ModelInfo(
        model_id="~deepseek/deepseek-flash-latest",
        label="DeepSeek Flash latest",
        context_window=1_048_576,
        input_per_million=0.3,
        output_per_million=1.2,
        notes="Алиас DeepSeek V4.1 Flash. Лучший AA-LCR из тройки.",
    ),
    ModelInfo(
        model_id="~openai/gpt-luna-latest",
        label="GPT Luna latest",
        context_window=1_050_000,
        input_per_million=0.1,
        output_per_million=0.5,
        notes="Алиас GPT-6 Luna. Быстрые подсказки.",
    ),
    ModelInfo(
        model_id="anthropic/claude-haiku-4.5",
        label="Claude Haiku 4.5",
        context_window=200_000,
        input_per_million=1.0,
        output_per_million=5.0,
        notes="Быстрая. Потолок выхода ровно 64K.",
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
    "z-ai": "Z.ai",
}

_LITE_IDS = frozenset(
    {
        "anthropic/claude-haiku-4.5",
        "~openai/gpt-luna-latest",
        "~deepseek/deepseek-flash-latest",
    }
)


def provider_label(info: ModelInfo) -> str:
    """Human name of the vendor, for an ``<optgroup>``.

    Alias ids start with ``~`` (``~openai/gpt-luna-latest``). The tilde is
    not part of the vendor name.
    """
    slug = info.model_id.split("/", 1)[0].removeprefix("~")
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
        blurb="Opus 5.5 for the agent, Haiku for quick UI fills.",
        model="anthropic/claude-opus-5.5",
        lite_model="anthropic/claude-haiku-4.5",
    ),
    ModelBundle(
        bundle_id="balanced",
        label="Balanced",
        blurb="Sonnet 5.5 plus Luna for helpers. The usual default.",
        model="anthropic/claude-sonnet-5.5",
        lite_model="~openai/gpt-luna-latest",
    ),
    ModelBundle(
        bundle_id="economy",
        label="Economy",
        blurb="GLM 5.3 Flash for the agent, DeepSeek Flash for helpers.",
        model="z-ai/glm-5.3-flash",
        lite_model="~deepseek/deepseek-flash-latest",
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

# Снимок GET https://ollama.com/api/tags на 2026-10-02. Имена — те, которыми
# зовут облачный API (gemma4:31b), а не суффикс :cloud из приложения Ollama.
# qwen3.5:397b в этом списке больше нет.
_OLLAMA_CLOUD_MODELS: tuple[SourceModel, ...] = (
    SourceModel("glm-5.3", "GLM 5.3", "Текущий GLM в облаке Ollama."),
    SourceModel("glm-5.2", "GLM 5.2", "Предыдущий GLM."),
    SourceModel("glm-5.3-flash", "GLM 5.3 Flash", "Быстрее GLM 5.3."),
    SourceModel("kimi-k3", "Kimi K3", "Большая модель Kimi."),
    SourceModel("kimi-k2.6", "Kimi K2.6", "Kimi предыдущего поколения."),
    SourceModel("kimi-k2.7-code", "Kimi K2.7 Code", "Kimi для кода."),
    SourceModel("deepseek-v4-pro:0813", "DeepSeek V4 Pro", "Тег deepseek-v4-pro:0813."),
    SourceModel("deepseek-v4.1-flash", "DeepSeek V4.1 Flash", "Быстрый DeepSeek."),
    SourceModel("minimax-m3", "MiniMax M3", "Текущий MiniMax."),
    SourceModel("minimax-m2.7", "MiniMax M2.7", "Предыдущий MiniMax."),
    SourceModel("mistral-large-3:675b", "Mistral Large 3 675B", "Большая модель Mistral."),
    SourceModel("nemotron-3-ultra", "Nemotron 3 Ultra", "Старший Nemotron."),
    SourceModel("nemotron-3-super", "Nemotron 3 Super", "Nemotron меньше Ultra."),
    SourceModel("gpt-oss:120b", "GPT-OSS 120B", "Открытые веса, 120B."),
    SourceModel("gemma4:31b", "Gemma 4 31B", "Меньше флагманов, для подсказок.", lite=True),
    SourceModel(
        "nemotron-3-nano:30b",
        "Nemotron 3 Nano 30B",
        "Малый Nemotron, для подсказок.",
        lite=True,
    ),
    SourceModel("gpt-oss:20b", "GPT-OSS 20B", "Малый GPT-OSS, для подсказок.", lite=True),
)

# Локальный демон отдаёт то, что скачано. Это частые теги, не облачный каталог.
_OLLAMA_LOCAL_MODELS: tuple[SourceModel, ...] = (
    SourceModel("qwen3.5:397b", "Qwen3.5 397B", "Большая локальная модель."),
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
        blurb="ollama.com, тег вида gemma4:31b.",
        base_url="https://ollama.com/v1",
        needs_key=True,
        models=_OLLAMA_CLOUD_MODELS,
        key_note="Ключ Ollama Cloud.",
    ),
    ChatProvider(
        provider_id="ollama-local",
        family="ollama",
        label="Ollama local",
        blurb="Демон на этой машине. Ключ не нужен.",
        base_url="http://127.0.0.1:11434/v1",
        needs_key=False,
        models=_OLLAMA_LOCAL_MODELS,
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
