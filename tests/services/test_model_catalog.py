"""COD-059: model catalog — describe formatting + lookup helpers."""

from __future__ import annotations

from cod_doc.services import model_catalog


def test_catalog_is_non_empty_and_unique() -> None:
    assert len(model_catalog.CATALOG) >= 4
    ids = [m.model_id for m in model_catalog.CATALOG]
    assert len(set(ids)) == len(ids)


def test_describe_formats_context_in_K_below_million() -> None:
    info = model_catalog.ModelInfo(
        model_id="x/y",
        label="Test 200K",
        context_window=200_000,
        input_per_million=3.0,
        output_per_million=15.0,
    )
    assert "200K ctx" in info.describe()
    assert "$3/$15" in info.describe()


def test_describe_formats_context_in_M_at_million() -> None:
    info = model_catalog.ModelInfo(
        model_id="x/big",
        label="Big",
        context_window=2_000_000,
        input_per_million=1.25,
        output_per_million=5.0,
    )
    assert "2M ctx" in info.describe()


def test_get_returns_entry_for_known_id() -> None:
    sonnet = model_catalog.get("anthropic/claude-sonnet-5.5")
    assert sonnet is not None
    assert sonnet.label == "Claude Sonnet 5.5"


def test_get_returns_none_for_unknown_id() -> None:
    assert model_catalog.get("vendor/never-existed") is None


def test_is_known_matches_get() -> None:
    assert model_catalog.is_known("anthropic/claude-sonnet-5.5")
    assert not model_catalog.is_known("vendor/never-existed")


def test_grouped_keeps_vendor_order() -> None:
    labels = [label for label, _models in model_catalog.grouped(model_catalog.CATALOG)]
    assert labels[0] == "Anthropic"
    assert "OpenAI" in labels
    assert len(labels) == len(set(labels))


def test_bundles_point_at_catalog_models() -> None:
    assert len(model_catalog.BUNDLES) >= 3
    for bundle in model_catalog.BUNDLES:
        assert model_catalog.is_known(bundle.model)
        assert model_catalog.is_lite_known(bundle.lite_model)
    match = model_catalog.matching_bundle(
        "anthropic/claude-sonnet-5.5",
        "~openai/gpt-luna-latest",
    )
    assert match is not None
    assert match.bundle_id == "balanced"
    assert model_catalog.matching_bundle("anthropic/claude-sonnet-5.5", "") is None


def test_matching_provider_uses_base_url() -> None:
    openrouter = model_catalog.matching_provider("https://openrouter.ai/api/v1/")
    assert openrouter.provider_id == "openrouter"
    assert openrouter.allows_bundles
    ollama = model_catalog.matching_provider("http://localhost:11434/v1")
    assert ollama.provider_id == "ollama-local"
    assert ollama.fallback_key == "ollama"
    assert not ollama.needs_key
    custom = model_catalog.matching_provider("https://example.internal/v1")
    assert custom.provider_id == "custom"
    assert model_catalog.find_source_model(custom, "gpt-5") is None
    assert model_catalog.find_source_model(openrouter, "anthropic/claude-sonnet-5.5") is not None


def test_openrouter_presets_are_the_2026_10_02_set() -> None:
    """Живой список OpenRouter на 2026-10-02, включая алиасы latest."""
    ids = [m.model_id for m in model_catalog.CATALOG]
    assert ids == [
        "anthropic/claude-sonnet-5.5",
        "anthropic/claude-opus-5.5",
        "z-ai/glm-5.3-flash",
        "~deepseek/deepseek-flash-latest",
        "~openai/gpt-luna-latest",
        "anthropic/claude-haiku-4.5",
    ]
    assert model_catalog.provider_label(model_catalog.CATALOG[3]) == "DeepSeek"
    assert model_catalog.provider_label(model_catalog.CATALOG[4]) == "OpenAI"
    assert model_catalog.provider_label(model_catalog.CATALOG[2]) == "Z.ai"
    lites = {m.model_id for m in model_catalog.LITE_CATALOG}
    assert lites == {
        "anthropic/claude-haiku-4.5",
        "~openai/gpt-luna-latest",
        "~deepseek/deepseek-flash-latest",
    }
    economy = model_catalog.matching_bundle(
        "z-ai/glm-5.3-flash",
        "~deepseek/deepseek-flash-latest",
    )
    assert economy is not None
    assert economy.bundle_id == "economy"


def test_ollama_cloud_models_match_cloud_catalog() -> None:
    """Снимок https://ollama.com/api/tags на 2026-10-02, не локальные теги."""
    cloud = model_catalog.matching_provider("https://ollama.com/v1")
    ids = [m.model_id for m in cloud.models]
    assert ids == [
        "glm-5.3",
        "glm-5.2",
        "glm-5.3-flash",
        "kimi-k3",
        "kimi-k2.6",
        "kimi-k2.7-code",
        "deepseek-v4-pro:0813",
        "deepseek-v4.1-flash",
        "minimax-m3",
        "minimax-m2.7",
        "mistral-large-3:675b",
        "nemotron-3-ultra",
        "nemotron-3-super",
        "gpt-oss:120b",
        "gemma4:31b",
        "nemotron-3-nano:30b",
        "gpt-oss:20b",
    ]
    lites = {m.model_id for m in cloud.models if m.lite}
    assert lites == {"gemma4:31b", "nemotron-3-nano:30b", "gpt-oss:20b"}
    local = model_catalog.matching_provider("http://127.0.0.1:11434/v1")
    assert "qwen3.5:397b" in {m.model_id for m in local.models}
    assert "kimi-k3" not in {m.model_id for m in local.models}
