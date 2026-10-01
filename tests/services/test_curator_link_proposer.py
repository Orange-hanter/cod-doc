"""ACU-012: LLM-предлагатель новой цели для битых ссылок — на подменённой модели.

Модель только выбирает из найденных кандидатов: ответ вне списка, «ни один»
и ошибка бэкенда уходят в отчёт и не роняют прогон.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import QuestionLinkKind, QuestionRelation
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import AgentRunModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import (
    approval_service,
    curator_proposers,
    curator_sweep_service,
    question_service,
    repo_index_service,
)
from cod_doc.services.ai_text import AIBackendError, LiteReply

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = "props"
_FM = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"


@dataclass
class FakeModel:
    """Подменённая модель: отвечает заданным текстом и считает вызовы."""

    answer: str = '{"choice": 1, "why": "файл переехал"}'
    error: bool = False
    prompts: list[str] = field(default_factory=list)

    def __call__(self, prompt: str) -> LiteReply:
        self.prompts.append(prompt)
        if self.error:
            raise AIBackendError("backend down")
        return LiteReply(text=self.answer, tokens_in=100, tokens_out=7)


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[sessionmaker, Path]]:
    root = tmp_path / _PROJECT
    (root / "guides").mkdir(parents=True)
    (root / "src" / "billing").mkdir(parents=True)
    (root / "src" / "billing" / "pay.py").write_text("def charge(): ...\n", encoding="utf-8")
    (root / "guides" / "install-guide.md").write_text(
        f"{_FM}# Install guide\n\nКак поставить.\n", encoding="utf-8"
    )
    (root / "alpha.md").write_text(
        f"{_FM}# Alpha\n\n## Setup\n\nСм. [гайд](install-guide.md) и [код](pay.py#charge).\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    assert runner.invoke(main, ["project", "add", str(root), "--name", _PROJECT]).exit_code == 0
    assert runner.invoke(main, ["import", "docs", _PROJECT]).exit_code == 0
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    with transactional(factory) as session:
        repo_index_service.scan_project(session, project_id=_pid(session), repo_path=root)
    try:
        yield factory, root
    finally:
        engine.dispose()


def _pid(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


def _run(factory: sessionmaker, model: FakeModel, **kw: Any) -> curator_proposers.ProposerStats:
    stats = curator_proposers.ProposerStats()
    with transactional(factory) as session:
        curator_proposers.propose_link_retargets(
            session,
            _pid(session),
            chooser=model,
            max_proposals=kw.get("max_proposals", 10),
            author="agent:curator",
            stats=stats,
        )
    return stats


def _pending(factory: sessionmaker) -> list[dict[str, Any]]:
    with transactional(factory, commit=False) as session:
        page = approval_service.list_approvals(session, _pid(session), status="pending")
    return [item["payload"] for item in page["items"]]


def test_link_target_reads_the_written_target_without_anchor() -> None:
    assert curator_proposers.link_target("[x](a/b.md#sec)") == "a/b.md"
    assert curator_proposers.link_target("[[doc:guide#setup]]") == "doc:guide"
    assert curator_proposers.link_target("[[doc:guide|подпись]]") == "doc:guide"


def test_choices_from_the_list_become_doc_patch_proposals(project) -> None:
    factory, _root = project

    stats = _run(factory, FakeModel())

    payloads = {p["args"]["old"]: p for p in _pending(factory)}
    assert payloads["install-guide.md"]["args"]["new"] == "guides/install-guide.md"
    assert payloads["pay.py"]["args"]["new"] == "src/billing/pay.py"
    # Якорь кода сохраняется: меняется только путь, «#charge» остаётся в diff.
    # Каждое предложение меняет только свою ссылку.
    assert (
        "+См. [гайд](install-guide.md) и [код](src/billing/pay.py#charge)."
        in (payloads["pay.py"]["diff"])
    )
    assert (
        "+См. [гайд](guides/install-guide.md) и [код](pay.py#charge)."
        in (payloads["install-guide.md"]["diff"])
    )
    assert stats.created == 2
    assert stats.llm_calls == 2 and stats.tokens_in == 200 and stats.tokens_out == 14
    assert ("link", "alpha#setup") in stats.handled


def test_an_answer_outside_the_list_is_reported_not_proposed(project) -> None:
    factory, _root = project

    stats = _run(factory, FakeModel(answer='{"choice": 9, "why": "выдумал"}'))

    assert _pending(factory) == []
    assert all("вне списка" in r["reason"] for r in stats.reported)


def test_none_fits_is_reported(project) -> None:
    factory, _root = project

    stats = _run(factory, FakeModel(answer='{"choice": 0, "why": "ни один не подходит"}'))

    assert _pending(factory) == []
    assert {r["reason"] for r in stats.reported} == {"ни один не подходит"}


def test_a_backend_error_is_reported_and_does_not_stop_the_rest(project) -> None:
    factory, _root = project

    stats = _run(factory, FakeModel(error=True))

    assert _pending(factory) == []
    assert len(stats.reported) == 2
    assert all(r["reason"].startswith("LLM:") for r in stats.reported)


def test_the_cap_limits_new_proposals(project) -> None:
    factory, _root = project

    stats = _run(factory, FakeModel(), max_proposals=1)

    assert stats.created == 1
    assert len(_pending(factory)) == 1


def test_a_question_code_link_keeps_its_fragment(project) -> None:
    factory, _root = project
    with transactional(factory) as session:
        pid = _pid(session)
        q = question_service.create(
            session, project_id=pid, title="Где charge?", question="Переехал?", author="human:t"
        )
        question_service.link(
            session,
            project_id=pid,
            question_id=q.question_id,
            to_kind=QuestionLinkKind.CODE,
            to_ref="pay.py#charge",
            relation=QuestionRelation.ABOUT,
            author="human:t",
        )
        question_service.verify_links(session, project_id=pid)

    _run(factory, FakeModel())

    question_patches = [p for p in _pending(factory) if p["op"] == "question_link_retarget"]
    assert [p["args"]["new"] for p in question_patches] == ["src/billing/pay.py#charge"]


def test_the_sweep_runs_proposers_and_records_tokens_on_the_run(project) -> None:
    factory, root = project
    model = FakeModel()

    with transactional(factory) as session:
        report = curator_sweep_service.sweep(
            session,
            _pid(session),
            root_path=root,
            master_path=root / "MASTER.md",
            slug=_PROJECT,
            chooser=model,
        )

    assert sum(1 for p in report.proposed if p["outcome"] == "created") == 2
    assert ("link", "alpha#setup") not in {(r["kind"], r["ref"]) for r in report.reported}
    with transactional(factory, commit=False) as session:
        run = session.execute(
            select(AgentRunModel).where(AgentRunModel.run_id == report.run_id)
        ).scalar_one()
        assert run.llm_calls == 2
        assert run.llm_tokens_in == 200
        assert run.llm_tokens_out == 14
