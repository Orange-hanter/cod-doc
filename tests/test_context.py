"""Тесты cod_doc.core.context"""

from pathlib import Path

import pytest

from cod_doc.core.context import get_context, parse_ref
from cod_doc.core.hash_calc import calc_hash, make_ref


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "auth.md"
    spec.parent.mkdir()
    spec.write_text("# Auth\nContent here.", encoding="utf-8")
    return tmp_path


def _make_valid_ref(repo: Path) -> str:
    return make_ref(repo / "specs" / "auth.md", repo)


def test_parse_ref_valid() -> None:
    ref = "📁 /specs/auth.md | 🗃️ doc:specs_auth_md | 🔑 sha:a1b2c3d4e5f6"
    parsed = parse_ref(ref)
    assert parsed["path"] == "/specs/auth.md"
    assert parsed["vec_id"] == "doc:specs_auth_md"
    assert parsed["hash"] == "a1b2c3d4e5f6"


def test_parse_ref_invalid() -> None:
    with pytest.raises(ValueError, match="Неверный формат"):
        parse_ref("не ссылка")


def test_get_context_valid(repo: Path) -> None:
    ref = _make_valid_ref(repo)
    result = get_context(ref, repo)
    assert result["status"] == "VALID"
    assert "Auth" in result["content"]
    assert result["metadata"]["depth"] == "L1"


def test_get_context_stale(repo: Path) -> None:
    # Ссылка с неверным хэшем
    ref = "📁 /specs/auth.md | 🗃️ doc:specs_auth_md | 🔑 sha:000000000000"
    result = get_context(ref, repo)
    assert result["status"] == "STALE"
    assert result["content"] is None
    assert "actual_hash" in result["metadata"]


def test_get_context_broken(repo: Path) -> None:
    ref = "📁 /specs/missing.md | 🗃️ doc:specs_missing_md | 🔑 sha:000000000000"
    result = get_context(ref, repo)
    assert result["status"] == "BROKEN"


def test_get_context_pagination(repo: Path) -> None:
    # Файл с ровно 201 строкой
    big = repo / "big.md"
    big.write_text("\n".join(f"line {i}" for i in range(201)), encoding="utf-8")
    ref = make_ref(big, repo)

    p1 = get_context(ref, repo, page=1)
    assert p1["metadata"]["total_pages"] == 2
    assert p1["metadata"]["has_more"] is True

    p2 = get_context(ref, repo, page=2)
    assert p2["metadata"]["has_more"] is False


def test_get_context_l2_dependencies(repo: Path) -> None:
    # Файл содержит вложенную ссылку
    inner = repo / "models" / "user.md"
    inner.parent.mkdir()
    inner.write_text("# User model", encoding="utf-8")
    inner_ref = make_ref(inner, repo)

    outer = repo / "specs" / "auth.md"
    outer.write_text(f"# Auth\nDepends on: {inner_ref}", encoding="utf-8")
    outer_ref = make_ref(outer, repo)

    result = get_context(outer_ref, repo, depth="L2")
    assert result["status"] == "VALID"
    assert len(result["metadata"]["dependencies"]) == 1
