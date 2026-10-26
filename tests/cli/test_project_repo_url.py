"""ADO-148: `project repo-url` — веб-адрес репозитория проекта в реестре.

Из `repo_url` карточка done-задачи строит ссылки на коммит закрытия, PR и
файлы. Адрес живёт в реестре (`config.yaml`), а не берётся из `git remote`:
на узле каталог проекта — снапшот без `.git`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config, ProjectEntry, normalize_repo_url

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://github.com/Orange-hanter/cod-doc", "https://github.com/Orange-hanter/cod-doc"),
        (
            "https://github.com/Orange-hanter/cod-doc.git",
            "https://github.com/Orange-hanter/cod-doc",
        ),
        ("https://github.com/o/r/", "https://github.com/o/r"),
        ("git@github.com:Orange-hanter/cod-doc.git", "https://github.com/Orange-hanter/cod-doc"),
        (
            "  https://gitlab.example.com/group/sub/repo.git  ",
            "https://gitlab.example.com/group/sub/repo",
        ),
        # Query и фрагмент к адресу репозитория не относятся.
        ("https://github.com/o/r?tab=readme-ov-file", "https://github.com/o/r"),
        ("https://github.com/o/r#readme", "https://github.com/o/r"),
        ("https://github.com/o/r.git?x=1", "https://github.com/o/r"),
    ],
)
def test_normalize_repo_url(raw: str, expected: str) -> None:
    assert normalize_repo_url(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "github.com/o/r",
        "ftp://host/o/r",
        "https://github.com",
        "not a url",
        # Адрес идёт в href карточки: только http(s) и алфавит имён репозиториев.
        "javascript:alert(1)//x/y",
        'https://github.com/o/r)"x',
        "https://github.com/o/<r>",
        # У scp-формы `#` — часть пути: отказ, а не молча потерянный хвост.
        "git@github.com:o/r#x",
    ],
)
def test_normalize_repo_url_rejects(bad: str) -> None:
    with pytest.raises(ValueError, match="не веб-адрес"):
        normalize_repo_url(bad)


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name="proj", path=str(root)))
    return "proj"


def _repo_url(name: str) -> str | None:
    entry = Config.load().get_project(name)
    assert entry is not None
    return entry.repo_url


def test_cli_sets_shows_and_clears_repo_url(registry: str) -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["project", "repo-url", registry, "git@github.com:o/r.git"])
    assert r.exit_code == 0, r.output
    assert _repo_url(registry) == "https://github.com/o/r"

    shown = runner.invoke(main, ["project", "repo-url", registry])
    assert shown.exit_code == 0
    assert shown.output.strip() == "https://github.com/o/r"

    cleared = runner.invoke(main, ["project", "repo-url", registry, "--clear"])
    assert cleared.exit_code == 0
    assert _repo_url(registry) is None


def test_cli_rejects_bad_url_and_keeps_old(registry: str) -> None:
    runner = CliRunner()
    runner.invoke(main, ["project", "repo-url", registry, "https://github.com/o/r"])
    r = runner.invoke(main, ["project", "repo-url", registry, "github.com/o/r"])
    assert r.exit_code == 1
    assert "не веб-адрес" in r.output
    assert _repo_url(registry) == "https://github.com/o/r"


def test_cli_unknown_project(registry: str) -> None:
    r = CliRunner().invoke(main, ["project", "repo-url", "nope", "https://github.com/o/r"])
    assert r.exit_code == 1
    assert "не найден" in r.output
