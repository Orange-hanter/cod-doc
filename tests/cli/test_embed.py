"""ADO-071: CLI-диагностика провайдера эмбеддингов."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config

if TYPE_CHECKING:
    from pathlib import Path


def _write_config(home: Path, **fields: object) -> None:
    cfg = Config(api_key="sk-llm", base_url="https://ollama.com/v1", **fields)
    cfg.save()
    assert (home / "config.yaml").exists()


def test_status_reports_backend_and_model(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home, embedding_model="openai/text-embedding-ada-002")
    result = CliRunner().invoke(main, ["embed", "status"])
    assert "openai" in result.output
    assert "text-embedding-ada-002" in result.output


def test_status_masks_keys(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home, embedding_api_key="sk-or-secret-value")
    result = CliRunner().invoke(main, ["embed", "status"])
    assert "sk-or-secret-value" not in result.output
    assert "alue" in result.output  # последние 4 символа


def test_status_flags_missing_key_for_openrouter(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home, embedding_backend="openrouter")
    result = CliRunner().invoke(main, ["embed", "status"])
    assert result.exit_code == 1
    assert "не наследуется" in result.output


def test_status_warns_when_llm_provider_has_no_embeddings(isolated_cod_doc_home: Path) -> None:
    """Диагноз ровно того инцидента, который породил ADO-071."""
    _write_config(isolated_cod_doc_home)
    result = CliRunner().invoke(main, ["embed", "status"])
    assert "ollama.com" in result.output
    assert "openrouter" in result.output


def test_status_json_shape(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home, embedding_api_key="sk-emb")
    result = CliRunner().invoke(main, ["embed", "status", "--json"])
    payload = json.loads(result.output)
    assert payload["settings"]["backend"] == "openai"
    assert payload["settings"]["api_key_set"] is True
    assert "sk-emb" not in result.output
    assert payload["signature"].endswith("@native")


def test_probe_reports_dimension_and_cost(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home, embedding_backend="mock")
    result = CliRunner().invoke(main, ["embed", "probe", "--json"])
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["dimensions"] > 0


def test_probe_exit_code_1_on_error(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home, embedding_backend="openrouter")
    result = CliRunner().invoke(main, ["embed", "probe"])
    assert result.exit_code == 1
    assert "не задан ключ" in result.output or "не наследуется" in result.output


def test_models_rejects_backend_without_catalog(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home, embedding_backend="mock")
    result = CliRunner().invoke(main, ["embed", "models"])
    assert result.exit_code == 1
    assert "не умеет перечислять" in result.output


def test_reset_requires_yes(isolated_cod_doc_home: Path) -> None:
    _write_config(isolated_cod_doc_home)
    result = CliRunner().invoke(main, ["embed", "reset"])
    assert result.exit_code == 1
    assert "--yes" in result.output
