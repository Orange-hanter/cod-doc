"""ACU-003: выгрузка куратора в собственный клон и PR синхронизации.

Настоящий git на tmp: bare-remote, чекаут владельца, клон куратора. PR-клиент
подменён фейком. Главный инвариант — чекаут владельца не меняется ни в одном
сценарии: ни HEAD, ни рабочее дерево, ни базовая линия ``projection_hash``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import curator_sync_service as sync
from cod_doc.services import doc_service, projection_service
from cod_doc.services.curator_sync_service import run_git

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


@dataclass
class FakePullRequests:
    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, *, head: str, base: str, title: str, body: str, cwd: Path) -> str:
        del cwd
        self.calls.append({"head": head, "base": base, "title": title, "body": body})
        return "https://example.invalid/pr/1"


@dataclass
class Stand:
    remote: Path
    owner: Path
    clone: Path
    engine: Engine
    project_id: int
    prs: FakePullRequests

    def sync(self) -> sync.SyncReport:
        with transactional(make_session_factory(self.engine)) as session:
            return sync.export_sync(
                session,
                self.project_id,
                repo_root=self.owner,
                clone_dir=self.clone,
                pull_requests=self.prs,
            )

    def branch_file(self, path: str) -> str | None:
        try:
            return run_git(["show", f"{sync.SYNC_BRANCH}:{path}"], self.remote)
        except sync.GitError:
            return None

    def ahead(self) -> int:
        count = run_git(["rev-list", "--count", f"main..{sync.SYNC_BRANCH}"], self.remote)
        return int(count.strip())

    def owner_state(self) -> tuple[str, str, dict[str, bytes]]:
        head = run_git(["rev-parse", "HEAD"], self.owner).strip()
        status = run_git(["status", "--porcelain"], self.owner)
        files = {p.name: p.read_bytes() for p in self.owner.glob("*.md")}
        return head, status, files


def _commit_all(repo: Path, message: str) -> None:
    run_git(["add", "--all"], repo)
    run_git(["commit", "--quiet", "-m", message], repo)
    run_git(["push", "--quiet", "origin", "HEAD:main"], repo)


def _add_section(session: Session, project_id: int, doc_key: str, anchor: str) -> None:
    doc = doc_service.get(session, project_id, doc_key)
    assert doc is not None and doc.row_id is not None
    doc_service.add_section(
        session,
        document_id=doc.row_id,
        anchor=anchor,
        heading=anchor.title(),
        level=2,
        position=0,
        body=f"Правка {anchor} в БД.\n",
        author="human:test",
    )


@pytest.fixture
def stand(tmp_path: Path, engine_with_schema: Engine) -> Stand:
    remote = tmp_path / "remote.git"
    run_git(["init", "--quiet", "--bare", "--initial-branch=main", str(remote)], tmp_path)
    owner = tmp_path / "owner"
    run_git(["clone", "--quiet", str(remote), str(owner)], tmp_path)
    (owner / "README.txt").write_text("repo\n", encoding="utf-8")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(slug="sync", title="Sync", root_path=str(owner), config_json={})
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()
        project_id = project.row_id
        for key in ("a", "b"):
            doc = doc_service.create(
                session,
                project_id=project_id,
                doc_key=key,
                type=DocumentType.GUIDE,
                status=DocumentStatus.ACTIVE,
                title=key.upper(),
                owner="human:test",
                author="human:test",
            )
            assert doc.row_id is not None
            projection_service.export_document(
                session, doc.row_id, root_path=owner, author="human:test"
            )
    _commit_all(owner, "init")
    return Stand(
        remote=remote,
        owner=owner,
        clone=tmp_path / "curator" / "sync",
        engine=engine_with_schema,
        project_id=project_id,
        prs=FakePullRequests(),
    )


def _db(stand: Stand) -> Session:
    return transactional(make_session_factory(stand.engine))  # type: ignore[return-value]


def test_nothing_stale_means_no_branch_and_no_pr(stand: Stand) -> None:
    report = stand.sync()

    assert report.commit_sha is None
    assert report.exported == []
    assert stand.prs.calls == []
    assert stand.branch_file("a.md") is None


def test_first_sync_creates_the_branch_and_leaves_the_owner_alone(stand: Stand) -> None:
    before = stand.owner_state()
    with _db(stand) as session:
        _add_section(session, stand.project_id, "a", "first")
        doc = doc_service.get(session, stand.project_id, "a")
        assert doc is not None and doc.row_id is not None
        baseline = projection_service.detect_drift(
            session, doc.row_id, root_path=stand.owner
        ).projection_hash

    report = stand.sync()

    assert report.exported == ["a.md"]
    assert not report.rebuilt
    assert stand.ahead() == 1
    branch_a = stand.branch_file("a.md")
    assert branch_a is not None and "Правка first в БД." in branch_a
    assert [c["head"] for c in stand.prs.calls] == [sync.SYNC_BRANCH]
    assert stand.owner_state() == before
    with _db(stand) as session:
        drift = projection_service.detect_drift(session, doc.row_id, root_path=stand.owner)
        assert drift.projection_hash == baseline
        assert drift.status is projection_service.DriftStatus.STALE_EXPORT


def test_second_sync_appends_to_the_same_branch(stand: Stand) -> None:
    with _db(stand) as session:
        _add_section(session, stand.project_id, "a", "first")
    stand.sync()
    with _db(stand) as session:
        _add_section(session, stand.project_id, "a", "second")

    report = stand.sync()

    assert not report.rebuilt
    assert stand.ahead() == 2
    branch_a = stand.branch_file("a.md")
    assert branch_a is not None and "Правка second в БД." in branch_a


def test_deleted_document_is_removed_from_the_branch(stand: Stand) -> None:
    before = stand.owner_state()
    with _db(stand) as session:
        doc_service.delete(session, project_id=stand.project_id, doc_key="b", author="human:test")

    report = stand.sync()

    assert report.deleted == ["b.md"]
    assert stand.branch_file("b.md") is None
    assert stand.owner_state() == before


def test_branch_is_rebuilt_when_the_base_moves(stand: Stand) -> None:
    with _db(stand) as session:
        _add_section(session, stand.project_id, "a", "first")
    stand.sync()
    (stand.owner / "NOTES.txt").write_text("новое в main\n", encoding="utf-8")
    _commit_all(stand.owner, "main moved")
    before = stand.owner_state()

    report = stand.sync()

    assert report.rebuilt
    assert report.exported == ["a.md"]
    assert stand.ahead() == 1
    main = run_git(["rev-parse", "main"], stand.remote).strip()
    merge_base = run_git(["merge-base", "main", sync.SYNC_BRANCH], stand.remote).strip()
    assert merge_base == main
    assert stand.owner_state() == before


def test_a_human_edit_committed_to_main_is_not_overwritten(stand: Stand) -> None:
    with (stand.owner / "a.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка человека прямо в main.\n")
    _commit_all(stand.owner, "hand edit")
    with _db(stand) as session:
        _add_section(session, stand.project_id, "a", "first")

    report = stand.sync()

    assert [s["path"] for s in report.skipped] == ["a.md"]
    assert report.commit_sha is None
    assert stand.branch_file("a.md") is None


def test_sync_refuses_to_use_its_own_branch_as_base(stand: Stand) -> None:
    with _db(stand) as session, pytest.raises(ValueError, match="база"):
        sync.export_sync(
            session,
            stand.project_id,
            repo_root=stand.owner,
            clone_dir=stand.clone,
            base=sync.SYNC_BRANCH,
            pull_requests=stand.prs,
        )
