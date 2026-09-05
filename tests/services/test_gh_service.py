"""SYM-010: `gh`-клиент — идемпотентность PR-комментария и разбор ответов.

Никакой сети: единственная точка выхода наружу (``gh_service._run_gh``)
подменяется фейком, который ведёт себя как GitHub — хранит комментарии в
списке, отдаёт их на GET и правит на PATCH.
"""

from __future__ import annotations

import json

import pytest

from cod_doc.services import gh_service


class FakeGh:
    """Мини-GitHub: список комментариев + счётчик POST/PATCH."""

    def __init__(self, comments: list[dict] | None = None) -> None:
        self.comments = comments or []
        self.calls: list[list[str]] = []
        self.next_id = 1000

    def __call__(self, args: list[str], *, cwd=None) -> str:
        self.calls.append(args)
        if args[:2] == ["repo", "view"]:
            return "owner/repo\n"
        if args[0] == "pr" and args[1] == "view":
            return json.dumps({"files": [{"path": "docs/a.md"}, {"path": "src/x.ts"}]})
        if args[0] == "api" and "--method" not in args:
            return json.dumps(self.comments)
        if args[0] == "api" and "--method" in args:
            method = args[args.index("--method") + 1]
            endpoint = args[args.index("--method") + 2]
            body = self._read_body(args)
            if method == "POST":
                self.next_id += 1
                created = {
                    "id": self.next_id,
                    "body": body,
                    "html_url": f"https://github.com/owner/repo#c{self.next_id}",
                }
                self.comments.append(created)
                return json.dumps(created)
            comment_id = int(endpoint.rsplit("/", 1)[-1])
            for c in self.comments:
                if c["id"] == comment_id:
                    c["body"] = body
                    return json.dumps(c)
            raise AssertionError(f"PATCH to unknown comment {comment_id}")
        raise AssertionError(f"unexpected gh call: {args}")

    @staticmethod
    def _read_body(args: list[str]) -> str:
        field = args[args.index("-F") + 1]
        assert field.startswith("body=@")
        from pathlib import Path

        return Path(field[len("body=@") :]).read_text(encoding="utf-8")

    @property
    def posts(self) -> int:
        return sum(1 for a in self.calls if "--method" in a and "POST" in a)

    @property
    def patches(self) -> int:
        return sum(1 for a in self.calls if "--method" in a and "PATCH" in a)


@pytest.fixture
def fake_gh(monkeypatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(gh_service, "_run_gh", fake)
    return fake


def test_pr_changed_files_returns_paths(fake_gh: FakeGh) -> None:
    assert gh_service.pr_changed_files(7, repo="owner/repo") == ["docs/a.md", "src/x.ts"]


def test_resolve_repo_prefers_explicit_value(fake_gh: FakeGh) -> None:
    assert gh_service.resolve_repo(repo="a/b") == "a/b"
    assert fake_gh.calls == []


def test_upsert_creates_then_updates_same_comment(fake_gh: FakeGh) -> None:
    marker = "<!-- cod-doc:drift-gate:p -->"

    first = gh_service.upsert_marker_comment(7, f"{marker}\nfirst", marker, repo="owner/repo")
    assert first.action == "created"

    second = gh_service.upsert_marker_comment(7, f"{marker}\nsecond", marker, repo="owner/repo")
    assert second.action == "updated"
    assert second.comment_id == first.comment_id, "повторный прогон обязан править свой комментарий"
    assert fake_gh.posts == 1, "второй прогон не имеет права создавать новый комментарий"
    assert fake_gh.patches == 1
    assert len(fake_gh.comments) == 1


def test_upsert_identical_body_is_unchanged(fake_gh: FakeGh) -> None:
    marker = "<!-- cod-doc:drift-gate:p -->"
    body = f"{marker}\nsame"

    first = gh_service.upsert_marker_comment(7, body, marker, repo="owner/repo")
    second = gh_service.upsert_marker_comment(7, body, marker, repo="owner/repo")

    assert second.action == "unchanged"
    assert second.comment_id == first.comment_id
    assert fake_gh.patches == 0, "байт-в-байт совпавшее тело не должно писаться повторно"


def test_upsert_ignores_foreign_comments(fake_gh: FakeGh) -> None:
    fake_gh.comments.append({"id": 1, "body": "чей-то чужой комментарий", "html_url": "u"})
    marker = "<!-- cod-doc:drift-gate:p -->"

    ref = gh_service.upsert_marker_comment(7, f"{marker}\nx", marker, repo="owner/repo")

    assert ref.action == "created"
    assert ref.comment_id != 1
    assert fake_gh.comments[0]["body"] == "чей-то чужой комментарий"


def test_upsert_requires_marker_in_body(fake_gh: FakeGh) -> None:
    with pytest.raises(gh_service.GhError, match="маркер"):
        gh_service.upsert_marker_comment(7, "без маркера", "<!-- m -->", repo="owner/repo")


def test_missing_gh_binary_raises_gh_error(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(gh_service.subprocess, "run", _boom)
    with pytest.raises(gh_service.GhError, match="gh"):
        gh_service.pr_changed_files(1, repo="a/b")
