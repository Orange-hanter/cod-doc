"""Пресеты эмбеддингов для страницы настроек."""

from __future__ import annotations

from cod_doc.core.embeddings.catalog import (
    NATIVE_DIMENSIONS,
    PRESETS,
    RECOMMENDED_DIMENSIONS,
    RECOMMENDED_MODEL,
    matching_preset,
)


def test_recommended_preset_is_the_qwen_cut() -> None:
    recommended = [preset for preset in PRESETS if preset.recommended]
    assert len(recommended) == 1
    assert recommended[0].model_id == RECOMMENDED_MODEL
    assert recommended[0].dimensions == RECOMMENDED_DIMENSIONS
    assert "openrouter" in recommended[0].backends


def test_cloud_presets_have_known_native_dimensions() -> None:
    for preset in PRESETS:
        if "local" in preset.backends:
            continue
        assert preset.model_id in NATIVE_DIMENSIONS


def test_matching_preset_is_backend_specific() -> None:
    assert matching_preset(RECOMMENDED_MODEL, "openrouter") is not None
    assert matching_preset(RECOMMENDED_MODEL, "openai") is None
    assert matching_preset("all-MiniLM-L6-v2", "local") is not None
    assert matching_preset("vendor/never", "openai") is None
