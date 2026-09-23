"""AFT-001: `as_payload(hashes=...)` — full hashes by default, 12-char prefixes on request.

Expected dicts are literals on purpose: building them with `as_payload` would
check the implementation against itself.
"""

from __future__ import annotations

import pytest

from cod_doc.services.projection_service import DriftReport, DriftStatus, ProjectDriftItem

A64 = "a" * 64
B64 = "b" * 64
C64 = "c" * 64


def _report() -> DriftReport:
    return DriftReport(
        document_id=7,
        status=DriftStatus.EDITED_IN_PLACE,
        projection_hash=A64,
        db_content_hash=B64,
        file_hash=C64,
        metadata_mismatch=("status",),
        orphan_sections=("old-heading",),
    )


def _item() -> ProjectDriftItem:
    return ProjectDriftItem(doc_key="spec-x", path="docs/spec-x.md", report=_report())


def test_report_full_is_default_and_unchanged() -> None:
    expected = {
        "status": "edited_in_place",
        "projection_hash": A64,
        "db_content_hash": B64,
        "file_hash": C64,
        "metadata_mismatch": ["status"],
        "orphan_sections": ["old-heading"],
    }
    default = _report().as_payload()
    full = _report().as_payload(hashes="full")
    assert default == expected
    assert full == expected
    assert list(default) == list(expected)
    assert isinstance(default["metadata_mismatch"], list)
    assert isinstance(default["orphan_sections"], list)


def test_report_short_truncates_to_12() -> None:
    assert _report().as_payload(hashes="short") == {
        "status": "edited_in_place",
        "projection_hash": "a" * 12,
        "db_content_hash": "b" * 12,
        "file_hash": "c" * 12,
        "metadata_mismatch": ["status"],
        "orphan_sections": ["old-heading"],
    }


def test_report_short_keeps_none() -> None:
    report = DriftReport(
        document_id=8,
        status=DriftStatus.MISSING,
        projection_hash=None,
        db_content_hash=B64,
        file_hash=None,
    )
    assert report.as_payload(hashes="short") == {
        "status": "missing",
        "projection_hash": None,
        "db_content_hash": "b" * 12,
        "file_hash": None,
        "metadata_mismatch": [],
        "orphan_sections": [],
    }


def test_item_full_default_literal() -> None:
    expected = {
        "doc_key": "spec-x",
        "path": "docs/spec-x.md",
        "status": "edited_in_place",
        "projection_hash": A64,
        "db_content_hash": B64,
        "file_hash": C64,
        "metadata_mismatch": ["status"],
        "orphan_sections": ["old-heading"],
    }
    assert _item().as_payload() == expected
    assert _item().as_payload(hashes="full") == expected


def test_item_short_passes_through() -> None:
    assert _item().as_payload(hashes="short") == {
        "doc_key": "spec-x",
        "path": "docs/spec-x.md",
        "status": "edited_in_place",
        "projection_hash": "a" * 12,
        "db_content_hash": "b" * 12,
        "file_hash": "c" * 12,
        "metadata_mismatch": ["status"],
        "orphan_sections": ["old-heading"],
    }


def test_unknown_hashes_mode_raises() -> None:
    with pytest.raises(ValueError, match="medium"):
        _report().as_payload(hashes="medium")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="medium"):
        _item().as_payload(hashes="medium")  # type: ignore[arg-type]
