"""SYM-010: drift-гейт PR — `cod-doc ctx drift --changed-files/--pr/--comment`.

Проверяется то, чем гейт ценен для Orakul: он находит детерминированные
дефекты документации (битая ссылка, дрейф проекции, frontmatter), сужает
выборку до файлов PR и кладёт результат в **один** комментарий, который
повторный прогон правит на месте.

`gh` подменён фейком — тесты не ходят в сеть и ничего не пишут в чужие репо.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.services import drift_gate_service, gh_service

if TYPE_CHECKING:
    from pathlib import Path


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _import_corpus(root: Path, name: str = "p") -> None:
    """alpha.md — со сломанной ссылкой; beta.md — чистый."""
    runner = CliRunner()
    (root / "alpha.md").write_text(
        "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"
        "# Alpha\n\n## Details\n\nSee [[doc:missing]].\n",
        encoding="utf-8",
    )
    (root / "beta.md").write_text(
        "---\ntype: standard\nstatus: active\nowner: dakh\n---\n# Beta\n\nBeta body content.\n",
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", "docs", name])
    assert result.exit_code == 0, result.output


class FakePr:
    """Фейковый `gh`: список файлов PR + один хранимый комментарий."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        self.comments: list[dict] = []
        self.posts = 0
        self.patches = 0
        self.next_id = 500

    def pr_changed_files(self, pr: int, *, repo=None, cwd=None) -> list[str]:
        return list(self.files)

    def upsert(self, pr, body, marker, *, repo=None, cwd=None):
        existing = next((c for c in self.comments if marker in c["body"]), None)
        if existing is None:
            self.next_id += 1
            self.comments.append({"id": self.next_id, "body": body})
            self.posts += 1
            return gh_service.CommentRef(self.next_id, f"https://x/#{self.next_id}", "created")
        if existing["body"] == body:
            return gh_service.CommentRef(
                existing["id"], f"https://x/#{existing['id']}", "unchanged"
            )
        existing["body"] = body
        self.patches += 1
        return gh_service.CommentRef(existing["id"], f"https://x/#{existing['id']}", "updated")


@pytest.fixture
def fake_pr(monkeypatch) -> FakePr:
    fake = FakePr(["alpha.md", "app/src/x.ts"])
    monkeypatch.setattr(gh_service, "pr_changed_files", fake.pr_changed_files)
    monkeypatch.setattr(gh_service, "upsert_marker_comment", fake.upsert)
    return fake


def test_changed_files_narrows_scope(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    result = CliRunner().invoke(
        main, ["ctx", "drift", "-p", "p", "--changed-files", "beta.md", "--json"]
    )
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["changed_files"] == ["beta.md"]
    assert data["total_docs"] == 1, "дрейф обязан считаться только по файлам выборки"
    assert data["gate"]["scanned_docs"] == 1
    assert all(f["file"] == "beta.md" for f in data["gate"]["findings"])


def test_gate_reports_broken_link_in_engine_shape(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    result = CliRunner().invoke(
        main, ["ctx", "drift", "-p", "p", "--changed-files", "alpha.md", "--json"]
    )
    assert result.exit_code == 0, result.output

    gate = json.loads(result.output)["gate"]
    assert gate["model"] == "cod-doc/drift"
    assert gate["prescan"] is True
    links = [f for f in gate["findings"] if f["rule"] == "link"]
    assert links, "сломанная [[doc:missing]] обязана быть найдена"
    finding = links[0]
    assert finding["model"] == "cod-doc/drift"
    assert finding["prescan"] is True
    assert finding["file"] == "alpha.md"
    assert finding["fp"], "у находки обязан быть фингерпринт для дедупа на стороне движка"


def test_gate_reports_projection_drift(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)
    # Правка файла мимо БД — ровно тот дрейф, который гейт обязан назвать.
    (root / "beta.md").write_text(
        "---\ntype: standard\nstatus: active\nowner: dakh\n---\n# Beta\n\nEdited on disk.\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main, ["ctx", "drift", "-p", "p", "--changed-files", "beta.md", "--json"]
    )
    assert result.exit_code == 0, result.output

    gate = json.loads(result.output)["gate"]
    drift = [f for f in gate["findings"] if f["rule"] == "drift"]
    assert drift, "edited_in_place обязан попасть в находки гейта"
    assert drift[0]["code"] == "DRIFT-EDITED_IN_PLACE"


def test_empty_selection_is_an_empty_run(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    result = CliRunner().invoke(
        main, ["ctx", "drift", "-p", "p", "--changed-files", "app/src/x.ts", "--json"]
    )
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["gate"]["scanned_docs"] == 0
    assert data["gate"]["findings"] == []


def test_pr_number_supplies_changed_files(
    tmp_path: Path, isolated_cod_doc_home: Path, fake_pr: FakePr
) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    result = CliRunner().invoke(main, ["ctx", "drift", "-p", "p", "--pr", "42", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["changed_files"] == ["alpha.md", "app/src/x.ts"]
    assert data["gate"]["scanned_docs"] == 1, "не-markdown файлы PR не дают документов"


def test_comment_is_idempotent_across_runs(
    tmp_path: Path, isolated_cod_doc_home: Path, fake_pr: FakePr
) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)
    runner = CliRunner()
    args = ["ctx", "drift", "-p", "p", "--pr", "42", "--comment", "--json"]

    first = runner.invoke(main, args)
    assert first.exit_code == 0, first.output
    second = runner.invoke(main, args)
    assert second.exit_code == 0, second.output

    c1 = json.loads(first.output)["comment"]
    c2 = json.loads(second.output)["comment"]
    assert c1["action"] == "created"
    assert c2["comment_id"] == c1["comment_id"], "второй прогон обязан попасть в тот же комментарий"
    assert fake_pr.posts == 1, "комментарии не должны множиться"
    assert len(fake_pr.comments) == 1
    assert drift_gate_service.marker("p") in fake_pr.comments[0]["body"]


def test_comment_requires_pr(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    result = CliRunner().invoke(main, ["ctx", "drift", "-p", "p", "--comment"])
    assert result.exit_code != 0
    assert "--pr" in result.output


def test_dry_run_posts_nothing(
    tmp_path: Path, isolated_cod_doc_home: Path, fake_pr: FakePr
) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    result = CliRunner().invoke(
        main, ["ctx", "drift", "-p", "p", "--pr", "42", "--comment", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output

    comment = json.loads(result.output)["comment"]
    assert comment["action"] == "dry_run"
    assert drift_gate_service.marker("p") in comment["body"]
    assert fake_pr.comments == [], "dry-run не имеет права трогать чужой PR"


def test_plain_drift_keeps_legacy_shape(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """Без --changed-files/--pr вывод обязан остаться прежним (SYM-006D)."""
    root = _init_project(tmp_path)
    _import_corpus(root)

    result = CliRunner().invoke(main, ["ctx", "drift", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert set(data) == {"project", "total_docs", "problem_count", "counts", "issues"}


def test_gate_writes_nothing_to_the_db(
    tmp_path: Path, isolated_cod_doc_home: Path, fake_pr: FakePr
) -> None:
    """Ключевая гарантия для чужого репозитория: прогон гейта — read-only."""
    root = _init_project(tmp_path)
    _import_corpus(root)
    runner = CliRunner()

    result = runner.invoke(main, ["ctx", "drift", "-p", "p", "--pr", "42", "--comment", "--json"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["import", "docs", "p", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Skipped (already in DB):" in result.output
