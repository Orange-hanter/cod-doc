"""Тесты cod_doc.core.hash_calc"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from cod_doc.core.hash_calc import (
    calc_hash,
    check_hash,
    check_stale_refs,
    make_ref,
    update_hashes,
)


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
        "📁 /specs/auth.md | 🗃️ doc:specs_auth_md | 🔑 sha:000000000000\n",
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


# ── ADO-174: «файла нет» и «файл под .gitignore» — разные вещи ──────────
#
# `models/domain.md` лежит в основном чекауте и его хэш совпадает с реестром,
# но `/models/` стоит в `.gitignore`, а git не переносит игнорируемые файлы в
# новый worktree. Поэтому `cod-doc hash update` из worktree печатал BROKEN на
# совершенно исправной записи, а из основного чекаута — молчал. Диагноз
# зависел от места запуска.


def _git_repo(root: Path, ignore: str) -> None:
    """Минимальный репозиторий: нужен только рабочий `git check-ignore`."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text(ignore, encoding="utf-8")


def test_ignored_missing_file_is_not_broken(tmp_path: Path) -> None:
    """Отсутствующая проекция под `.gitignore` молчит."""
    _git_repo(tmp_path, "/models/\n")
    master = tmp_path / "MASTER.md"
    master.write_text(
        "📁 /models/domain.md | 🗃️ doc:models_domain_md | 🔑 sha:8ce613932ac9\n",
        encoding="utf-8",
    )

    n, warns = update_hashes(master)

    assert n == 0
    assert warns == [], f"игнорируемый путь не должен поднимать тревогу: {warns}"
    # Запись цела: пересчитать хэш не из чего, обнулять нельзя.
    assert "8ce613932ac9" in master.read_text(encoding="utf-8")


def test_untracked_missing_file_is_still_broken(tmp_path: Path) -> None:
    """Настоящая поломка остаётся поломкой — иначе правка бесполезна."""
    _git_repo(tmp_path, "/models/\n")
    master = tmp_path / "MASTER.md"
    master.write_text(
        "📁 /docs/gone.md | 🗃️ doc:docs_gone_md | 🔑 sha:000000000000\n",
        encoding="utf-8",
    )

    _n, warns = update_hashes(master)

    assert any("BROKEN" in w for w in warns), "удалённый документ обязан остаться BROKEN"


def test_stale_refs_skips_ignored_paths(tmp_path: Path) -> None:
    """Та же развилка во второй точке: её читают рутина и куратор."""
    _git_repo(tmp_path, "/models/\n")
    master = tmp_path / "MASTER.md"
    master.write_text(
        "📁 /models/domain.md | 🗃️ doc:models_domain_md | 🔑 sha:8ce613932ac9\n"
        "📁 /docs/gone.md | 🗃️ doc:docs_gone_md | 🔑 sha:000000000000\n",
        encoding="utf-8",
    )

    findings = check_stale_refs(master, repo_root=tmp_path)

    paths = {f["path"] for f in findings}
    assert "docs/gone.md" in paths
    assert "models/domain.md" not in paths, (
        "куратор из worktree видел бы находку, которой из основного чекаута нет"
    )


def test_without_git_behaviour_is_unchanged(tmp_path: Path) -> None:
    """Без репозитория (Docker, sdist) предикат не притворяется знающим."""
    master = tmp_path / "MASTER.md"
    master.write_text(
        "📁 /models/domain.md | 🗃️ doc:models_domain_md | 🔑 sha:8ce613932ac9\n",
        encoding="utf-8",
    )

    _n, warns = update_hashes(master)

    assert any("BROKEN" in w for w in warns)
