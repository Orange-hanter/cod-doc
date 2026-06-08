"""OBI-010: commit_link_service — parser + git-log import."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskMetricsModel,
)
from cod_doc.services import commit_link_service, task_service

if TYPE_CHECKING:
    from pathlib import Path

# ----------------------------------------------------------------- #
# parse_task_refs                                                    #
# ----------------------------------------------------------------- #


def test_parse_single_task_id() -> None:
    refs = commit_link_service.parse_task_refs("fix(COD-042): align frontmatter")
    assert refs == ["COD-042"]


def test_parse_multiple_task_ids_unique_and_ordered() -> None:
    msg = "feat(OBI-010): import git log. Closes OBI-001 + ADR-002. (OBI-010)"
    refs = commit_link_service.parse_task_refs(msg)
    assert refs == ["OBI-010", "OBI-001", "ADR-002"]


def test_parse_ignores_non_task_tokens() -> None:
    msg = "see #1234 or https://example.com/foo (PR-bar)"
    assert commit_link_service.parse_task_refs(msg) == []


def test_parse_handles_optional_subtask_letter() -> None:
    refs = commit_link_service.parse_task_refs("docs(PCA-902A): subtask")
    assert refs == ["PCA-902A"]


def test_parse_empty_message_returns_empty_list() -> None:
    assert commit_link_service.parse_task_refs("") == []
    assert commit_link_service.parse_task_refs(None) == []  # type: ignore[arg-type]


# ----------------------------------------------------------------- #
# import_from_git_log — integration                                  #
# ----------------------------------------------------------------- #


def _seed(session) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="cmtp", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="cmtp-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(session, pid, plid, sid, tid):
    return task_service.create(
        session,
        project_id=pid,
        plan_id=plid,
        section_id=sid,
        task_id=tid,
        title=f"Task {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="t",
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with 3 commits, two of which reference task IDs."""
    repo = tmp_path / "demo-repo"
    repo.mkdir()

    def _run(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    _run("init", "-q", "--initial-branch=main")
    _run("config", "user.email", "t@example.com")
    _run("config", "user.name", "Tester")
    _run("config", "commit.gpgsign", "false")

    (repo / "a.txt").write_text("a")
    _run("add", "-A")
    _run("commit", "-q", "-m", "chore: initial setup")

    (repo / "b.txt").write_text("b")
    _run("add", "-A")
    _run("commit", "-q", "-m", "feat(CMT-001): implement endpoint")

    (repo / "c.txt").write_text("c")
    _run("add", "-A")
    _run("commit", "-q", "-m", "fix(CMT-001): edge case; refs CMT-002")
    return repo


def test_import_links_commits_to_known_tasks(engine_with_schema, git_repo) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "CMT-001")
        _make(session, pid, plid, sid, "CMT-002")

    with transactional(factory) as session:
        result = commit_link_service.import_from_git_log(
            session,
            project_id=1,
            repo_path=git_repo,
        )
    assert result["scanned"] == 3
    assert result["linked"] == 3  # 2 for CMT-001 + 1 for CMT-002
    assert result["skipped_existing"] == 0

    with transactional(factory) as session:
        cmt1 = commit_link_service.list_for_task(session, 1, "CMT-001")
        cmt2 = commit_link_service.list_for_task(session, 1, "CMT-002")
    assert len(cmt1) == 2
    assert len(cmt2) == 1


def test_import_idempotent(engine_with_schema, git_repo) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "CMT-001")
        _make(session, pid, plid, sid, "CMT-002")
    with transactional(factory) as session:
        commit_link_service.import_from_git_log(session, project_id=1, repo_path=git_repo)
    with transactional(factory) as session:
        result = commit_link_service.import_from_git_log(session, project_id=1, repo_path=git_repo)
    assert result["linked"] == 0
    assert result["skipped_existing"] == 3


def test_import_skips_unknown_task_ids(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Commit refs a task that's not in DB → not linked."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "CMT-001")
    # Build a repo with ONE commit referencing a non-existent task.
    repo = tmp_path / "lonely-repo"
    repo.mkdir()

    def _run(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    _run("init", "-q", "--initial-branch=main")
    _run("config", "user.email", "t@example.com")
    _run("config", "user.name", "Tester")
    _run("config", "commit.gpgsign", "false")
    (repo / "x.txt").write_text("x")
    _run("add", "-A")
    _run("commit", "-q", "-m", "feat(GHOST-999): not in DB")

    with transactional(factory) as session:
        result = commit_link_service.import_from_git_log(
            session,
            project_id=1,
            repo_path=repo,
        )
    assert result["scanned"] == 1
    assert result["linked"] == 0


def test_import_updates_commit_count_in_metrics(engine_with_schema, git_repo) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "CMT-001")
        _make(session, pid, plid, sid, "CMT-002")
    # Complete CMT-001 so it has a task_metrics row.
    with transactional(factory) as session:
        task_service.complete(session, task_id="CMT-001", author="t")

    with transactional(factory) as session:
        commit_link_service.import_from_git_log(
            session,
            project_id=1,
            repo_path=git_repo,
        )

    with transactional(factory) as session:
        from cod_doc.infra.models import TaskModel

        task_row = session.execute(
            select(TaskModel).where(TaskModel.task_id == "CMT-001")
        ).scalar_one()
        m = session.execute(
            select(TaskMetricsModel).where(TaskMetricsModel.task_id == task_row.row_id)
        ).scalar_one()
    assert m.commit_count == 2  # CMT-001 referenced by 2 commits


def test_import_rejects_non_git_path(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with pytest.raises(ValueError, match="not a git"), transactional(factory) as session:
        commit_link_service.import_from_git_log(
            session,
            project_id=1,
            repo_path=tmp_path,
        )
