"""ACU-013: LLM-предлагатель раздела для документов, которым не подошло ни одно правило."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import AgentRunModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import (
    approval_service,
    curator_proposers,
    curator_sweep_service,
    doc_tree_service,
)
from cod_doc.services.ai_text import LiteReply

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = "nodes"
_FM = "---\ntype: analysis\nstatus: active\nowner: dakh\n---\n"


@dataclass
class FakeModel:
    choice: int = 1
    prompts: list[str] = field(default_factory=list)

    def __call__(self, prompt: str) -> LiteReply:
        self.prompts.append(prompt)
        return LiteReply(
            text=f'{{"choice": {self.choice}, "why": "по смыслу"}}', tokens_in=50, tokens_out=5
        )


def _pid(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[sessionmaker, Path]]:
    root = tmp_path / _PROJECT
    (root / "notes").mkdir(parents=True)
    for name in ("random-thoughts", "meeting-log"):
        (root / "notes" / f"{name}.md").write_text(
            f"{_FM}# {name}\n\nЗаметки без явного места.\n", encoding="utf-8"
        )
    runner = CliRunner()
    assert runner.invoke(main, ["project", "add", str(root), "--name", _PROJECT]).exit_code == 0
    assert runner.invoke(main, ["import", "docs", _PROJECT]).exit_code == 0
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        yield factory, root
    finally:
        engine.dispose()


def _seed_tree(factory: sessionmaker) -> list[str]:
    with transactional(factory) as session:
        pid = _pid(session)
        if not doc_tree_service.list_nodes(session, pid):
            doc_tree_service.init_tree(session, project_id=pid, author="system:init")
        return [n.node_key for n in doc_tree_service.list_nodes(session, pid) if not n.is_inbox]


def _run(
    factory: sessionmaker, model: FakeModel, *, cap: int = 10
) -> curator_proposers.ProposerStats:
    stats = curator_proposers.ProposerStats()
    with transactional(factory) as session:
        curator_proposers.propose_node_placements(
            session,
            _pid(session),
            chooser=model,
            max_proposals=cap,
            author="agent:curator",
            stats=stats,
        )
    return stats


def _pending(factory: sessionmaker) -> list[dict[str, Any]]:
    with transactional(factory, commit=False) as session:
        page = approval_service.list_approvals(session, _pid(session), status="pending")
    return page["items"]


def test_a_chosen_section_becomes_a_proposal_and_approval_places_the_doc(project) -> None:
    factory, _root = project
    keys = _seed_tree(factory)

    stats = _run(factory, FakeModel(choice=2))

    pending = _pending(factory)
    assert stats.created == 2
    assert {p["payload"]["args"]["node_key"] for p in pending} == {keys[1]}
    first = pending[0]
    with transactional(factory) as session:
        approval_service.resolve(
            session, _pid(session), first["approval_id"], decision="approve", resolved_by="human:d"
        )
    with transactional(factory, commit=False) as session:
        left = {
            p.doc_key
            for p in doc_tree_service.classify_project(
                session, project_id=_pid(session), author="t", dry_run=True
            ).unplaced
        }
    assert first["payload"]["args"]["doc_key"] not in left


def test_a_section_outside_the_list_is_reported(project) -> None:
    factory, _root = project
    _seed_tree(factory)

    stats = _run(factory, FakeModel(choice=99))

    assert _pending(factory) == []
    assert all("вне списка" in r["reason"] for r in stats.reported)


def test_the_cap_limits_new_proposals(project) -> None:
    factory, _root = project
    _seed_tree(factory)

    stats = _run(factory, FakeModel(), cap=1)

    assert stats.created == 1


def test_without_a_tree_the_model_is_not_asked(project, monkeypatch: pytest.MonkeyPatch) -> None:
    """Дерева нет — засев решает человек; модель не спрашивают зря."""
    factory, _root = project
    monkeypatch.setattr(doc_tree_service, "list_nodes", lambda *_a, **_k: [])
    model = FakeModel()

    _run(factory, model)

    assert model.prompts == []


def test_the_sweep_proposes_sections_and_records_tokens(project) -> None:
    factory, root = project
    _seed_tree(factory)

    with transactional(factory) as session:
        report = curator_sweep_service.sweep(
            session,
            _pid(session),
            root_path=root,
            master_path=root / "MASTER.md",
            slug=_PROJECT,
            chooser=FakeModel(),
        )

    unplaced_proposals = [p for p in report.proposed if p["kind"] == "unplaced"]
    assert len(unplaced_proposals) == 2
    assert "unplaced" not in {r["kind"] for r in report.reported}
    with transactional(factory, commit=False) as session:
        run = session.execute(
            select(AgentRunModel).where(AgentRunModel.run_id == report.run_id)
        ).scalar_one()
        assert run.llm_calls >= 2
        assert run.llm_tokens_in >= 100
