"""ACU-007: отчёт прогона в `agent_run` и `pending_proposals` в `curator_next`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import AgentRunModel, RevisionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import (
    approval_service,
    curator_service,
    curator_sweep_service,
    question_service,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = "runrep"
_FM = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[sessionmaker, Path]]:
    root = tmp_path / _PROJECT
    root.mkdir()
    (root / "alpha.md").write_text(f"{_FM}# Alpha\n\n## Details\n\nBody.\n", encoding="utf-8")
    assert (
        CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT]).exit_code == 0
    )
    assert CliRunner().invoke(main, ["import", "docs", _PROJECT]).exit_code == 0
    with (root / "alpha.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка на диске.\n")
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        yield factory, root
    finally:
        engine.dispose()


def _pid(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


def _sweep(session: Session, root: Path, *, apply: bool) -> curator_sweep_service.SweepReport:
    return curator_sweep_service.sweep(
        session,
        _pid(session),
        root_path=root,
        master_path=root / "MASTER.md",
        slug=_PROJECT,
        apply=apply,
    )


def _runs(session: Session) -> list[AgentRunModel]:
    return list(
        session.execute(
            select(AgentRunModel).where(
                AgentRunModel.wake_reason == curator_sweep_service.WAKE_REASON
            )
        ).scalars()
    )


def test_an_applying_sweep_leaves_a_run_row_with_its_summary(project) -> None:
    factory, root = project
    with transactional(factory) as session:
        report = _sweep(session, root, apply=True)

    with transactional(factory, commit=False) as session:
        runs = _runs(session)
        assert [r.run_id for r in runs] == [report.run_id]
        run = runs[0]
        assert run.status == "done"
        assert run.finished_at is not None
        summary: dict[str, Any] = json.loads(run.summary or "{}")
        assert summary["applied"] >= 1
        assert summary["changes"], "ревизии прогона обязаны попасть в сводку"
        stamped = session.execute(
            select(RevisionModel.run_id).where(
                RevisionModel.author == curator_sweep_service.DEFAULT_AUTHOR
            )
        ).scalars()
        assert set(stamped) == {report.run_id}


def test_a_dry_run_leaves_no_run_row(project) -> None:
    factory, root = project
    with transactional(factory) as session:
        report = _sweep(session, root, apply=False)

    assert report.run_id is None
    with transactional(factory, commit=False) as session:
        assert _runs(session) == []


def _next(session: Session, root: Path) -> dict[str, Any]:
    return curator_service.next(
        session,
        project_id=_pid(session),
        root_path=root,
        master_path=root / "MASTER.md",
        limit=50,
        project_slug=_PROJECT,
    )


def test_curator_next_counts_only_the_curators_pending_proposals(project) -> None:
    factory, root = project
    with transactional(factory) as session:
        pid = _pid(session)
        approval_service.request(
            session, pid, approval_type="manual", requested_by=curator_service.CURATOR_AUTHOR
        )
        approval_service.request(session, pid, approval_type="manual", requested_by="human:dakh")
        question_service.create(
            session,
            project_id=pid,
            title="Док или код?",
            question="Символ исчез — править документ или завести задачу?",
            author=curator_service.CURATOR_AUTHOR,
        )
        question_service.create(
            session, project_id=pid, title="Чужой", question="Не куратора.", author="human:dakh"
        )

    with transactional(factory, commit=False) as session:
        payload = _next(session, root)

    assert payload["meta"]["counts"]["pending_proposals"] == 2
    items = [i for i in payload["priority"] if i["kind"] == "proposals"]
    assert [i["ref"] for i in items] == ["2 pending"]
    assert f'approval_list(project="{_PROJECT}"' in items[0]["suggested_action"]


def test_no_proposals_means_no_queue_item(project) -> None:
    factory, root = project
    with transactional(factory, commit=False) as session:
        payload = _next(session, root)

    assert payload["meta"]["counts"]["pending_proposals"] == 0
    assert not [i for i in payload["priority"] if i["kind"] == "proposals"]
