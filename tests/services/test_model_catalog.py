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
    sonnet = model_catalog.get("anthropic/claude-sonnet-4-6")
    assert sonnet is not None
    assert sonnet.label == "Claude Sonnet 4.6"


def test_get_returns_none_for_unknown_id() -> None:
    assert model_catalog.get("vendor/never-existed") is None


def test_is_known_matches_get() -> None:
    assert model_catalog.is_known("anthropic/claude-sonnet-4-6")
    assert not model_catalog.is_known("vendor/never-existed")
