"""Protocol validation, path safety, payload limits and additive compatibility."""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from cod_doc.services.structure_protocol import (
    MAX_UNCOMPRESSED_BYTES,
    StructureProtocolError,
    decompress_payload,
    normalize_repo_path,
    parse_structure_payload,
    validate_structure_facts,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "structure"


def test_canonical_fixtures_are_accepted() -> None:
    for name in (
        "structure-facts.v1.json",
        "structure-assessment.v1.json",
        "obligations-export.v1.json",
    ):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        payload = parse_structure_payload(text)
        assert payload["version"] == 1


def test_additive_fields_are_ignored() -> None:
    payload = json.loads((FIXTURES / "structure-facts.v1.json").read_text(encoding="utf-8"))
    payload["futureField"] = {"nested": True}
    payload["provenance"]["extraCapability"] = "ok"
    validated = validate_structure_facts(payload)
    assert validated["futureField"] == {"nested": True}


def test_unsupported_major_is_rejected() -> None:
    payload = json.loads((FIXTURES / "structure-facts.v1.json").read_text(encoding="utf-8"))
    payload["version"] = 2
    with pytest.raises(StructureProtocolError, match="unsupported"):
        validate_structure_facts(payload)


def test_path_normalization_rejects_escapes() -> None:
    assert normalize_repo_path("./src/x.ts") == "src/x.ts"
    assert normalize_repo_path(".") == "."
    with pytest.raises(StructureProtocolError):
        normalize_repo_path("../secret")
    with pytest.raises(StructureProtocolError):
        normalize_repo_path("/etc/passwd")
    with pytest.raises(StructureProtocolError):
        normalize_repo_path("C:\\secret")


def test_decompression_bomb_is_rejected() -> None:
    compressed = zlib.compress(b"A" * (MAX_UNCOMPRESSED_BYTES + 2048))
    with pytest.raises(StructureProtocolError):
        decompress_payload(compressed)
