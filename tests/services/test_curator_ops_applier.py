"""ACU-009: одобренный doc_patch исполняется из закрытого реестра CURATOR_OPS."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    QuestionLinkKind,
    QuestionRelation,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import (
    approval_service,
    curator_ops,
    doc_service,
    doc_tree_service,
    question_service,
    revision_service,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_BODY = "См. [гайд](old.md) и [[old.md]]. Слово old.md в прозе не трогаем.\n"


def _project(session: Session) -> int:
    now = datetime.now(UTC)
    project = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    project.created = now
    project.updated = now
    session.add(project)
    session.flush()
    return project.row_id


def _doc(session: Session, pid: int, key: str = "guide") -> int:
    doc = doc_service.create(
        session,
        project_id=pid,
        doc_key=key,
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        title=key.title(),
        owner="human:test",
        author="human:test",
    )
    assert doc.row_id is not None
    doc_service.add_section(
        session,
        document_id=doc.row_id,
        anchor="setup",
        heading="Setup",
        level=2,
        position=0,
        body=_BODY,
        author="human:test",
    )
    return doc.row_id


def _section_head(session: Session, pid: int) -> str | None:
    return curator_ops.CURATOR_OPS["link_retarget"].head(
        session, pid, {"doc_key": "guide", "anchor": "setup"}
    )


def _propose_retarget(session: Session, pid: int, *, base: str | None, old: str = "old.md") -> str:
    result = approval_service.request_doc_patch(
        session,
        pid,
        op="link_retarget",
        args={"doc_key": "guide", "anchor": "setup", "old": old, "new": "new.md"},
        diff="-old.md\n+new.md\n",
        rationale="файл переехал",
        base_revision_id=base,
    )
    assert result.approval is not None
    return result.approval.approval_id


def _body(session: Session, doc_id: int) -> str:
    return next(s.body for s in doc_service.get_sections(session, doc_id) if s.anchor == "setup")


def _status(session: Session, pid: int, approval_id: str) -> str:
    approval = approval_service.get(session, pid, approval_id)
    assert approval is not None
    return approval.status


def test_retarget_touches_link_targets_only() -> None:
    out = curator_ops.retarget_links(_BODY, "old.md", "new.md")

    assert "[гайд](new.md)" in out
    assert "[[new.md]]" in out
    assert "Слово old.md в прозе" in out


def test_approving_applies_the_patch_by_the_approver_and_revert_undoes_it(
    engine_with_schema: Engine,
) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        doc_id = _doc(session, pid)
        approval_id = _propose_retarget(session, pid, base=_section_head(session, pid))

        result = approval_service.resolve(
            session, pid, approval_id, decision="approve", resolved_by="human:dakh"
        )

        assert result["approval"]["status"] == "approved"
        assert result["applied"]["new"] == "new.md"
        assert "[гайд](new.md)" in _body(session, doc_id)
        head = _section_head(session, pid)
        assert head is not None
        revisions = revision_service.list_for_entity(
            session, EntityKind.SECTION, _section_row_id(session, doc_id)
        )
        assert next(r.author for r in revisions if r.revision_id == head) == "human:dakh"

        revision_service.revert(session, head, author="human:dakh")
        assert _body(session, doc_id) == _BODY


def _section_row_id(session: Session, doc_id: int) -> int:
    row_id = next(
        s.row_id for s in doc_service.get_sections(session, doc_id) if s.anchor == "setup"
    )
    assert row_id is not None
    return row_id


def test_a_stale_base_expires_the_approval_and_writes_nothing(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        doc_id = _doc(session, pid)
        approval_id = _propose_retarget(session, pid, base=_section_head(session, pid))
        # Человек поправил секцию после того, как куратор составил предложение.
        doc_service.patch_section(
            session,
            document_id=doc_id,
            anchor="setup",
            new_body=_BODY + "Правка человека.\n",
            author="human:dakh",
        )
        edited = _body(session, doc_id)

        result = approval_service.resolve(
            session, pid, approval_id, decision="approve", resolved_by="human:dakh"
        )

        assert result["approval"]["status"] == "expired"
        assert result["applied"] is None
        assert _body(session, doc_id) == edited


def test_denying_writes_nothing(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        doc_id = _doc(session, pid)
        approval_id = _propose_retarget(session, pid, base=_section_head(session, pid))

        result = approval_service.resolve(
            session, pid, approval_id, decision="deny", resolved_by="human:dakh"
        )

        assert result["approval"]["status"] == "denied"
        assert _body(session, doc_id) == _BODY


def test_a_failing_op_rolls_the_approval_back_too(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session)
        _doc(session, pid)
        approval_id = _propose_retarget(session, pid, base=None, old="absent.md")

    with pytest.raises(curator_ops.CuratorOpError), transactional(factory) as session:
        approval_service.resolve(
            session, pid, approval_id, decision="approve", resolved_by="human:dakh"
        )

    with transactional(factory, commit=False) as session:
        assert _status(session, pid, approval_id) == "pending"


def test_an_unknown_op_is_refused_before_anything_changes(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session)
        result = approval_service.request_doc_patch(
            session, pid, op="rm_rf", args={}, diff="-всё\n", rationale="r"
        )
        assert result.approval is not None
        approval_id = result.approval.approval_id

    with pytest.raises(curator_ops.CuratorOpError, match="rm_rf"), transactional(factory) as s:
        approval_service.resolve(s, pid, approval_id, decision="approve", resolved_by="human:dakh")

    with transactional(factory, commit=False) as session:
        assert _status(session, pid, approval_id) == "pending"


def test_doc_set_node_places_the_document(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        _doc(session, pid)
        doc_tree_service.init_tree(session, project_id=pid, author="system:init")
        node_key = doc_tree_service.list_nodes(session, pid)[0].node_key
        args: dict[str, Any] = {"doc_key": "guide", "node_key": node_key}
        result = approval_service.request_doc_patch(
            session,
            pid,
            op="doc_set_node",
            args=args,
            diff=f"+раздел {node_key}\n",
            rationale="по смыслу",
            base_revision_id=curator_ops.CURATOR_OPS["doc_set_node"].head(session, pid, args),
        )
        assert result.approval is not None

        approval_service.resolve(
            session, pid, result.approval.approval_id, decision="approve", resolved_by="human:dakh"
        )

        placed = doc_tree_service.classify_project(
            session, project_id=pid, author="t", dry_run=True
        ).placements
        assert "guide" not in {p.doc_key for p in placed}


def test_question_link_retarget_moves_the_edge(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        question = question_service.create(
            session, project_id=pid, title="Где код?", question="Куда переехал?", author="human:t"
        )
        question_service.link(
            session,
            project_id=pid,
            question_id=question.question_id,
            to_kind=QuestionLinkKind.CODE,
            to_ref="old/a.py",
            relation=QuestionRelation.ABOUT,
            author="human:t",
        )
        args: dict[str, Any] = {
            "question_id": question.question_id,
            "to_kind": "code",
            "relation": "about",
            "old": "old/a.py",
            "new": "new/a.py",
        }
        result = approval_service.request_doc_patch(
            session,
            pid,
            op="question_link_retarget",
            args=args,
            diff="-old/a.py\n+new/a.py\n",
            rationale="файл переехал",
            base_revision_id=curator_ops.CURATOR_OPS["question_link_retarget"].head(
                session, pid, args
            ),
        )
        assert result.approval is not None

        approval_service.resolve(
            session, pid, result.approval.approval_id, decision="approve", resolved_by="human:dakh"
        )

        refreshed = question_service.get(session, pid, question.question_id)
        assert refreshed is not None and refreshed.row_id is not None
        refs = {link.to_ref for link in question_service.list_links(session, refreshed.row_id)}
        assert refs == {"new/a.py"}
