"""Existence check for question edges — the half ``link`` defers.

Each edge is resolved against its own table; ``code`` edges go through
``link_service.resolve_code_ref`` — the same rule as a ``code`` link in a
document (file under the project root, ``L10-L20`` within the file, symbol as
a substring). ``url`` edges are not checked: no network on write paths
(document-link standard §7).

The result is stamped on the edge (``resolved`` / ``broken_reason`` /
``last_checked``). This is derived state, like ``link.resolved``, so it writes
neither a revision nor an activity event: a nightly verify would otherwise
bury the question's real history under check-stamps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import QuestionLinkKind
from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    FindingModel,
    OpenQuestionModel,
    QuestionLinkModel,
    ScenarioModel,
    SectionModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.services import link_service

from ._internals import _require_question, split_code_ref
from ._types import LinkCheck, VerifyReport

if TYPE_CHECKING:
    from sqlalchemy import Select
    from sqlalchemy.orm import Session


def _exists(session: Session, stmt: Select[int]) -> bool:
    return session.execute(stmt.limit(1)).first() is not None


def _check_section(session: Session, project_id: int, to_ref: str) -> str | None:
    doc_key, _, anchor = to_ref.partition("#")
    doc_id = session.execute(
        select(DocumentModel.row_id).where(
            DocumentModel.project_id == project_id, DocumentModel.doc_key == doc_key
        )
    ).scalar_one_or_none()
    if doc_id is None:
        return f"document not found: {doc_key!r}"
    if not _exists(
        session,
        select(SectionModel.row_id).where(
            SectionModel.document_id == doc_id, SectionModel.anchor == anchor
        ),
    ):
        return f"section #{anchor} not found in {doc_key!r}"
    return None


def _check_entity(
    session: Session, project_id: int, kind: QuestionLinkKind, to_ref: str
) -> str | None:
    """Return ``broken_reason`` for a DB-entity edge, ``None`` when it resolves."""
    lookups: dict[QuestionLinkKind, Select[int]] = {
        QuestionLinkKind.DOCUMENT: select(DocumentModel.row_id).where(
            DocumentModel.project_id == project_id, DocumentModel.doc_key == to_ref
        ),
        QuestionLinkKind.TASK: select(TaskModel.row_id).where(
            TaskModel.project_id == project_id, TaskModel.task_id == to_ref
        ),
        QuestionLinkKind.ADR: select(ADRModel.row_id).where(
            ADRModel.project_id == project_id, ADRModel.adr_id == to_ref
        ),
        QuestionLinkKind.STORY: select(UserStoryModel.row_id).where(
            UserStoryModel.project_id == project_id, UserStoryModel.story_id == to_ref
        ),
        QuestionLinkKind.SCENARIO: select(ScenarioModel.row_id).where(
            ScenarioModel.project_id == project_id, ScenarioModel.scenario_id == to_ref
        ),
        QuestionLinkKind.FINDING: select(FindingModel.row_id).where(
            FindingModel.project_id == project_id, FindingModel.finding_uid == to_ref
        ),
    }
    if _exists(session, lookups[kind]):
        return None
    return f"{kind.value} not found: {to_ref!r}"


def check_edge(
    session: Session, project_id: int, kind: QuestionLinkKind, to_ref: str
) -> tuple[bool | None, str | None]:
    """(resolved, broken_reason) for one edge; ``(None, None)`` for ``url``."""
    if kind is QuestionLinkKind.URL:
        return None, None
    if kind is QuestionLinkKind.CODE:
        path, fragment = split_code_ref(to_ref)
        ok, _, reason = link_service.resolve_code_ref(session, project_id, path, fragment)
        return ok, None if ok else reason
    if kind is QuestionLinkKind.SECTION:
        reason = _check_section(session, project_id, to_ref)
    else:
        reason = _check_entity(session, project_id, kind, to_ref)
    return reason is None, reason


def verify_links(
    session: Session,
    *,
    project_id: int,
    question_id: str | None = None,
) -> VerifyReport:
    """Re-check the edges of one question, or of every question in the project."""
    stmt = (
        select(QuestionLinkModel, OpenQuestionModel.question_id)
        .join(OpenQuestionModel, OpenQuestionModel.row_id == QuestionLinkModel.question_row_id)
        .where(OpenQuestionModel.project_id == project_id)
        .order_by(OpenQuestionModel.question_id, QuestionLinkModel.row_id)
    )
    if question_id is not None:
        _require_question(session, project_id, question_id)
        stmt = stmt.where(OpenQuestionModel.question_id == question_id)

    report = VerifyReport()
    now = datetime.now(UTC)
    for edge, qid in session.execute(stmt).all():
        kind = QuestionLinkKind(edge.to_kind)
        resolved, reason = check_edge(session, project_id, kind, edge.to_ref)
        edge.resolved = resolved
        edge.broken_reason = reason
        edge.last_checked = now
        report.checked += 1
        if resolved is None:
            report.unchecked += 1
        elif resolved:
            report.ok += 1
        else:
            report.broken += 1
            report.broken_links.append(
                LinkCheck(
                    question_id=qid,
                    to_kind=edge.to_kind,
                    to_ref=edge.to_ref,
                    relation=edge.relation,
                    resolved=False,
                    broken_reason=reason,
                )
            )
    session.flush()
    return report


def broken_links(session: Session, *, project_id: int) -> list[LinkCheck]:
    """Edges the last verify marked broken, on open questions only (curator input)."""
    stmt = (
        select(QuestionLinkModel, OpenQuestionModel.question_id)
        .join(OpenQuestionModel, OpenQuestionModel.row_id == QuestionLinkModel.question_row_id)
        .where(
            OpenQuestionModel.project_id == project_id,
            OpenQuestionModel.status == "open",
            QuestionLinkModel.resolved.is_(False),
        )
        .order_by(OpenQuestionModel.question_id, QuestionLinkModel.row_id)
    )
    return [
        LinkCheck(
            question_id=qid,
            to_kind=edge.to_kind,
            to_ref=edge.to_ref,
            relation=edge.relation,
            resolved=False,
            broken_reason=edge.broken_reason,
        )
        for edge, qid in session.execute(stmt).all()
    ]
