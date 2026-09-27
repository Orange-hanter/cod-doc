"""OQM-005: open questions wired into search, context_get, the curator card and tasks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import update

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Priority,
    QuestionLinkKind,
    QuestionRelation,
    TaskType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import OpenQuestionModel, PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import (
    context_service,
    curator_service,
    doc_service,
    question_service,
    task_service,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

AUTHOR = "human:test"


@pytest.fixture
def factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _seed(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="oq", title="P", root_path=str(root), config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    pid = proj.row_id
    plan = PlanModel(project_id=pid, scope="oq-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
    session.add(sec)
    session.flush()
    task_service.create(
        session,
        project_id=pid,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id="PAY-001",
        title="Integrate provider",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=AUTHOR,
    )
    doc = doc_service.create(
        session,
        project_id=pid,
        doc_key="docs/billing",
        title="Billing",
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        owner="core",
        author=AUTHOR,
    )
    assert doc.row_id is not None
    doc_service.add_section(
        session,
        document_id=doc.row_id,
        anchor="providers",
        heading="Providers",
        level=2,
        position=0,
        body="text",
        author=AUTHOR,
    )
    return pid


def _q(session: Session, pid: int, title: str, **kw: Any) -> str:
    return question_service.create(
        session, project_id=pid, title=title, question="?", author=AUTHOR, **kw
    ).question_id


def _link(session: Session, pid: int, qid: str, kind: QuestionLinkKind, ref: str) -> None:
    question_service.link(
        session,
        project_id=pid,
        question_id=qid,
        to_kind=kind,
        to_ref=ref,
        relation=QuestionRelation.ABOUT,
        author=AUTHOR,
    )


# --------------------------------------------------------------------------- #
# context_get hints                                                            #
# --------------------------------------------------------------------------- #


def test_context_hints_linked_first_then_urgent_capped(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        on_section = _q(session, pid, "про секцию")
        _link(session, pid, on_section, QuestionLinkKind.SECTION, "docs/billing#providers")
        on_doc = _q(session, pid, "про документ")
        _link(session, pid, on_doc, QuestionLinkKind.DOCUMENT, "docs/billing")
        closed = _q(session, pid, "закрыт")
        _link(session, pid, closed, QuestionLinkKind.DOCUMENT, "docs/billing")
        question_service.drop(
            session, project_id=pid, question_id=closed, author=AUTHOR, resolution="x"
        )
        high = _q(session, pid, "срочный", priority=Priority.HIGH)
        critical = _q(session, pid, "горит", priority=Priority.CRITICAL)
        _q(session, pid, "обычный")

        packet = context_service.context_get(session, pid, "document", "docs/billing")
        l0 = context_service.context_get(session, pid, "document", "docs/billing", depth="L0")
        task_packet = context_service.context_get(session, pid, "task", "PAY-001")

    hints = packet["hints"]["open_questions"]
    assert [h["question_id"] for h in hints] == [on_section, on_doc, critical]
    assert [h["relation"] for h in hints] == ["about", "about", None]
    assert l0["hints"]["open_questions"] == []
    assert [h["question_id"] for h in task_packet["hints"]["open_questions"]] == [
        critical,
        high,
    ]


# --------------------------------------------------------------------------- #
# curator card                                                                 #
# --------------------------------------------------------------------------- #


def test_curator_card_lists_broken_links_and_stale_questions(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    master = tmp_path / "MASTER.md"
    master.write_text("# M\n", encoding="utf-8")
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        broken = _q(session, pid, "битая ссылка")
        _link(session, pid, broken, QuestionLinkKind.CODE, "gone.py")
        question_service.verify_links(session, project_id=pid)
        old = _q(session, pid, "давний", priority=Priority.HIGH)
        session.execute(
            update(OpenQuestionModel)
            .where(OpenQuestionModel.question_id == old)
            .values(last_updated=datetime.now(UTC) - timedelta(days=45))
        )
        card = curator_service.next(
            session,
            project_id=pid,
            root_path=tmp_path,
            master_path=master,
            project_slug="oq",
            skip_links=True,
            limit=50,
        )

    questions = card["card"]["questions"]
    assert [b["question_id"] for b in questions["broken_links"]] == [broken]
    assert [s["question_id"] for s in questions["stale"]] == [old]
    assert card["meta"]["counts"]["question_links"] == 1
    assert card["meta"]["counts"]["questions_stale"] == 1
    kinds = [(p["kind"], p["ref"]) for p in card["priority"]]
    assert ("question_link", f"{broken} → code:gone.py") in kinds
    assert ("question", old) in kinds
    # битая ссылка вопроса ранжируется выше застоявшегося вопроса
    assert kinds.index(("question_link", f"{broken} → code:gone.py")) < kinds.index(
        ("question", old)
    )
    stale_item = next(p for p in card["priority"] if p["kind"] == "question")
    assert stale_item["suggested_action"] == f"cod-doc question show {old} -p oq"


# --------------------------------------------------------------------------- #
# reverse navigation                                                           #
# --------------------------------------------------------------------------- #


def test_linked_questions_include_sections_and_all_statuses(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        a = _q(session, pid, "A")
        _link(session, pid, a, QuestionLinkKind.SECTION, "docs/billing#providers")
        _link(session, pid, a, QuestionLinkKind.DOCUMENT, "docs/billing")
        b = _q(session, pid, "B")
        _link(session, pid, b, QuestionLinkKind.DOCUMENT, "docs/billing")
        question_service.resolve(
            session, project_id=pid, question_id=a, author=AUTHOR, resolution="да"
        )
        found = question_service.linked_questions(
            session, pid, target_kind=QuestionLinkKind.DOCUMENT, target_ref="docs/billing"
        )
        other = question_service.linked_questions(
            session, pid, target_kind=QuestionLinkKind.DOCUMENT, target_ref="docs/bill"
        )
    assert [(f["question_id"], f["status"]) for f in found] == [(b, "open"), (a, "resolved")]
    assert other == []
