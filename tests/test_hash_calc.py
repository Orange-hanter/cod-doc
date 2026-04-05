"""Тесты cod_doc.core.hash_calc"""

import hashlib
from pathlib import Path

import pytest

from cod_doc.core.hash_calc import calc_hash, check_hash, make_ref, update_hashes


@pytest.fixture
def tmp_file(tmp_path: Path) -> Path:
    f = tmp_path / "test.md"
    f.write_text("# Hello\ncontent", encoding="utf-8")
    return f


def test_calc_hash_returns_12_chars(tmp_file: Path) -> None:
    h = calc_hash(tmp_file)
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_calc_hash_matches_sha256(tmp_file: Path) -> None:
    expected = hashlib.sha256(tmp_file.read_bytes()).hexdigest()[:12]
    assert calc_hash(tmp_file) == expected


def test_calc_hash_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        calc_hash("/nonexistent/file.md")


def test_check_hash_valid(tmp_file: Path) -> None:
    h = calc_hash(tmp_file)
    assert check_hash(tmp_file, h) is True
    assert check_hash(tmp_file, f"sha:{h}") is True


def test_check_hash_invalid(tmp_file: Path) -> None:
    assert check_hash(tmp_file, "000000000000") is False


def test_make_ref(tmp_file: Path) -> None:
    repo_root = tmp_file.parent
    ref = make_ref(tmp_file, repo_root)
    assert "📁" in ref
    assert "🗃️" in ref
    assert "🔑 sha:" in ref
    h = calc_hash(tmp_file)
    assert h in ref


def test_update_hashes(tmp_path: Path) -> None:
    # Создать файл спецификации
    spec = tmp_path / "specs" / "auth.md"
    spec.parent.mkdir()
    spec.write_text("# Auth spec", encoding="utf-8")
    real_hash = calc_hash(spec)

    # Создать MASTER.md со старым хэшем
    master = tmp_path / "MASTER.md"
    master.write_text(
        f"📁 /specs/auth.md | 🗃️ doc:specs_auth_md | 🔑 sha:000000000000\n",
        encoding="utf-8",
    )

    n, warns = update_hashes(master)
    assert n == 1
    assert warns == []
    assert real_hash in master.read_text()


def test_update_hashes_broken_link(tmp_path: Path) -> None:
    master = tmp_path / "MASTER.md"
    master.write_text(
        "📁 /specs/missing.md | 🗃️ doc:specs_missing_md | 🔑 sha:000000000000\n",
        encoding="utf-8",
    )
    n, warns = update_hashes(master)
    assert n == 0
    assert any("BROKEN" in w for w in warns)
