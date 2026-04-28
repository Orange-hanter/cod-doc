"""Изоляция COD-DOC config для api-тестов: исключаем запись в ~/.cod-doc."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_cod_doc_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "cod-doc-home"
    home.mkdir()
    monkeypatch.setattr("cod_doc.config.CONFIG_DIR", home)
    monkeypatch.setattr("cod_doc.config.CONFIG_FILE", home / "config.yaml")
    yield home
