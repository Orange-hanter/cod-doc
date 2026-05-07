"""PCA-003: skill.list / skill.get MCP tools — catalog of cod_doc/skills/."""

from __future__ import annotations

from cod_doc.mcp.tools.skill_tools import (
    SKILLS_ROOT,
    _parse_frontmatter,
    get_skill_record,
    iter_skill_records,
)

# --------------------------------------------------------------------------- #
# Frontmatter parser                                                            #
# --------------------------------------------------------------------------- #


def test_parse_frontmatter_extracts_top_level_keys() -> None:
    raw = "---\nname: foo\ndescription: bar baz\n---\n\nbody\n"
    meta, body = _parse_frontmatter(raw)
    assert meta == {"name": "foo", "description": "bar baz"}
    assert body == "body\n"


def test_parse_frontmatter_handles_pipe_block_scalar() -> None:
    raw = (
        "---\n"
        "name: x\n"
        "description: |\n"
        "  multi-line\n"
        "  description\n"
        "---\n\nbody\n"
    )
    meta, body = _parse_frontmatter(raw)
    assert meta["name"] == "x"
    assert "multi-line" in meta["description"]
    assert "description" in meta["description"]
    assert body == "body\n"


def test_parse_frontmatter_no_fence_returns_empty_meta() -> None:
    raw = "no frontmatter\nbody\n"
    meta, body = _parse_frontmatter(raw)
    assert meta == {}
    assert body == raw


# --------------------------------------------------------------------------- #
# Catalog walking                                                              #
# --------------------------------------------------------------------------- #


def test_iter_skill_records_includes_orchestrator_base_skill() -> None:
    """The orchestrator base skill (PCA-001) is always present in cod_doc/."""
    records = iter_skill_records()
    names = [r["name"] for r in records]
    assert "orchestrator" in names


def test_iter_skill_records_returns_sorted_unique() -> None:
    records = iter_skill_records()
    names = [r["name"] for r in records]
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_iter_skill_records_paths_resolve_against_repo(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Every reported `path` must resolve to an existing file."""
    records = iter_skill_records()
    repo_root = SKILLS_ROOT.parent.parent
    for r in records:
        target = repo_root / r["path"]
        assert target.is_file(), f"missing file: {target}"


# --------------------------------------------------------------------------- #
# Single skill fetch                                                            #
# --------------------------------------------------------------------------- #


def test_get_skill_record_returns_full_body_for_orchestrator() -> None:
    rec = get_skill_record("orchestrator")
    assert rec is not None
    assert rec["name"] == "orchestrator"
    # Body is the markdown after the frontmatter — must contain known sections.
    assert "Snowball Protocol" in rec["body"]
    assert "Fail-Fast" in rec["body"]
    assert "self_check" in rec["body"]


def test_get_skill_record_unknown_returns_none() -> None:
    assert get_skill_record("does-not-exist") is None
