"""COD-050: Property-based tests for frontmatter parser and task-plan validators.

Uses Hypothesis to check invariants over wide input spaces rather than fixed examples.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cod_doc.services import validation as v
from cod_doc.services.projection_service._frontmatter import (
    _parse_frontmatter,
    _render_frontmatter,
)

# ---------------------------------------------------------------------------
# Regex patterns mirroring _patterns.py — used to generate valid/invalid inputs.
# ---------------------------------------------------------------------------

_TASK_ID_RE = re.compile(r"^[A-Z]{2,5}-\d{3}[A-Z]?$")
_ID_PREFIX_RE = re.compile(r"^[A-Z]{2,5}$")
_STORY_ID_RE = re.compile(r"^[A-Z]{2,4}-\d{3}$")
_SECTION_SLUG_RE = re.compile(r"^[A-Z]-[A-Za-z0-9][A-Za-z0-9-]*$")

# ---------------------------------------------------------------------------
# Frontmatter parser properties
# ---------------------------------------------------------------------------


@given(st.text())
@settings(max_examples=500)
def test_parse_frontmatter_never_raises(s: str) -> None:
    """_parse_frontmatter must be a total function — never raises."""
    result = _parse_frontmatter(s)
    assert isinstance(result, dict)


@given(st.text().filter(lambda s: not s.startswith("---")))
@settings(max_examples=200)
def test_parse_frontmatter_no_fence_returns_empty(s: str) -> None:
    """Content without opening --- returns {}."""
    assert _parse_frontmatter(s) == {}


@given(
    st.dictionaries(
        keys=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu"), min_codepoint=65),
            min_size=1,
            max_size=20,
        ).filter(lambda k: k.strip() and "\n" not in k and ":" not in k),
        values=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), min_codepoint=65),
            max_size=40,
        ).filter(lambda v: "\n" not in v),
        min_size=1,
        max_size=8,
    )
)
@settings(max_examples=300)
def test_parse_frontmatter_roundtrip_string_values(d: dict[str, str]) -> None:
    """Round-trip: render then parse recovers all keys with their string values."""
    rendered = _render_frontmatter(d)
    parsed = _parse_frontmatter(rendered)
    assert isinstance(parsed, dict)
    for key, val in d.items():
        assert key in parsed, f"key {key!r} lost after round-trip"
        assert str(parsed[key]) == val, (
            f"value mismatch for key {key!r}: {parsed[key]!r} != {val!r}"
        )


@given(
    st.fixed_dictionaries(
        {
            "type": st.sampled_from(["module_spec", "guide", "architecture", "standard"]),
            "status": st.sampled_from(["active", "draft", "deprecated"]),
            "source_of_truth": st.booleans(),
            "sensitivity": st.sampled_from(["public", "internal", "confidential"]),
        }
    )
)
@settings(max_examples=200)
def test_parse_frontmatter_roundtrip_known_fields(d: dict) -> None:  # type: ignore[type-arg]
    """Round-trip for the canonical frontmatter fields used by DocumentModel."""
    rendered = _render_frontmatter(d)
    parsed = _parse_frontmatter(rendered)
    assert parsed.get("type") == d["type"]
    assert parsed.get("status") == d["status"]
    assert parsed.get("source_of_truth") == d["source_of_truth"]
    assert parsed.get("sensitivity") == d["sensitivity"]


# ---------------------------------------------------------------------------
# validate_task_id
# ---------------------------------------------------------------------------


@given(
    st.from_regex(r"^[A-Z]{2,5}-\d{3}[A-Z]?$", fullmatch=True)
)
@settings(max_examples=300)
def test_validate_task_id_accepts_all_valid(task_id: str) -> None:
    """Any string matching the task_id regex must pass without raising."""
    v.validate_task_id(task_id)  # no raise


@given(
    st.text().filter(lambda s: not _TASK_ID_RE.fullmatch(s))
)
@settings(max_examples=300)
def test_validate_task_id_rejects_all_invalid(s: str) -> None:
    """Any string NOT matching the task_id regex must raise ValidationError."""
    with pytest.raises(v.ValidationError) as exc:
        v.validate_task_id(s)
    assert exc.value.code == "TP-001"


# ---------------------------------------------------------------------------
# validate_id_prefix
# ---------------------------------------------------------------------------


@given(st.from_regex(r"^[A-Z]{2,5}$", fullmatch=True))
@settings(max_examples=200)
def test_validate_id_prefix_accepts_all_valid(prefix: str) -> None:
    v.validate_id_prefix(prefix)  # no raise


@given(st.text().filter(lambda s: not _ID_PREFIX_RE.fullmatch(s)))
@settings(max_examples=200)
def test_validate_id_prefix_rejects_all_invalid(s: str) -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_id_prefix(s)
    assert exc.value.code == "TP-002"


# ---------------------------------------------------------------------------
# validate_story_id
# ---------------------------------------------------------------------------


@given(st.from_regex(r"^[A-Z]{2,4}-\d{3}$", fullmatch=True))
@settings(max_examples=200)
def test_validate_story_id_accepts_all_valid(story_id: str) -> None:
    v.validate_story_id(story_id)  # no raise


@given(st.text().filter(lambda s: not _STORY_ID_RE.fullmatch(s)))
@settings(max_examples=200)
def test_validate_story_id_rejects_all_invalid(s: str) -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_story_id(s)
    assert exc.value.code == "US-001"


# ---------------------------------------------------------------------------
# validate_section_slug
# ---------------------------------------------------------------------------


@given(st.from_regex(r"^[A-Z]-[A-Za-z0-9][A-Za-z0-9-]*$", fullmatch=True))
@settings(max_examples=200)
def test_validate_section_slug_accepts_all_valid(slug: str) -> None:
    v.validate_section_slug(slug)  # no raise


@given(st.text().filter(lambda s: not _SECTION_SLUG_RE.fullmatch(s)))
@settings(max_examples=200)
def test_validate_section_slug_rejects_all_invalid(s: str) -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_section_slug(s)
    assert exc.value.code == "TP-003"


# ---------------------------------------------------------------------------
# validate_doc_path — path traversal guard
# ---------------------------------------------------------------------------


@given(
    st.lists(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="-_.",
            ),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip() and s != ".."),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=300)
def test_validate_doc_path_accepts_safe_relative_paths(parts: list[str]) -> None:
    """Paths built from safe segments (no '..', no leading '/') must pass."""
    path = "/".join(parts)
    assert not PurePosixPath(path).is_absolute()
    assert ".." not in parts
    v.validate_doc_path(path)  # no raise


@given(
    st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_."),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip() and s != ".."),
        min_size=1,
        max_size=4,
    )
)
@settings(max_examples=200)
def test_validate_doc_path_rejects_traversal_anywhere(parts: list[str]) -> None:
    """Inserting '..' anywhere in the path must trigger SD-100."""
    # Insert '..' at a random position (front, middle, or back)
    for insert_pos in range(len(parts) + 1):
        traversal_parts = parts[:insert_pos] + [".."] + parts[insert_pos:]
        path = "/".join(traversal_parts)
        with pytest.raises(v.ValidationError) as exc:
            v.validate_doc_path(path)
        assert exc.value.code == "SD-100"


@given(
    st.text(
        alphabet=st.sampled_from(" \t\n\r"),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=100)
def test_validate_doc_path_rejects_blank(s: str) -> None:
    """Whitespace-only paths must raise SD-100."""
    with pytest.raises(v.ValidationError) as exc:
        v.validate_doc_path(s)
    assert exc.value.code == "SD-100"


@given(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_."),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s.strip())
)
@settings(max_examples=200)
def test_validate_doc_path_rejects_posix_absolute(suffix: str) -> None:
    """Paths starting with '/' must raise SD-100."""
    path = "/" + suffix
    with pytest.raises(v.ValidationError) as exc:
        v.validate_doc_path(path)
    assert exc.value.code == "SD-100"
