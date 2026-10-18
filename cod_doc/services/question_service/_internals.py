"""Module-internal helpers — lookup, id allocation, ref shape, audit trail."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, QuestionLinkKind
from cod_doc.infra.models import OpenQuestionModel
from cod_doc.services.sync_service import leases
from cod_doc.services.validation import ValidationError

from ._types import QuestionNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_QUESTION_ID_RE = re.compile(r"^Q-(\d{3,})$")
_ID_WIDTH = 3
_PREFIX = "Q"

# Форма ссылки проверяется при записи, существование цели — в ``verify``:
# вопрос часто пишут раньше задачи, которая его закроет (та же логика, что у
# ``scenario_link``).
_REF_SHAPES: dict[QuestionLinkKind, re.Pattern[str]] = {
    # CLO-030: и провизорный ``PREFIX-~xxxx`` — push закрепит его вместе с ссылкой.
    QuestionLinkKind.TASK: re.compile(r"^[A-Z]{2,5}-(?:\d{3}[A-Z]?|~[a-z2-7]{4})$"),
    QuestionLinkKind.ADR: re.compile(r"^ADR-(?:\d{3,}|~[a-z2-7]{4})$"),
    QuestionLinkKind.STORY: re.compile(r"^[A-Z]{2,4}-(?:\d{3}|~[a-z2-7]{4})$"),
    QuestionLinkKind.SCENARIO: re.compile(r"^SCN-(?:\d{3,}|~[a-z2-7]{4})$"),
    QuestionLinkKind.SECTION: re.compile(r"^[^#\s][^#]*#[^#\s]+$"),
    QuestionLinkKind.URL: re.compile(r"^https?://\S+$"),
}
_MAX_REF_LEN = 512


def _find_question(session: Session, project_id: int, question_id: str) -> OpenQuestionModel | None:
    """Вопрос проекта; закреплённый провизорный ``Q-~xxxx`` — по алиасу (CLO-030)."""
    for value in (
        question_id,
        leases.current_id(session, project_id, "open_question", question_id),
    ):
        if value is None:
            continue
        stmt = select(OpenQuestionModel).where(
            OpenQuestionModel.project_id == project_id,
            OpenQuestionModel.question_id == value,
        )
        m = session.execute(stmt).scalar_one_or_none()
        if m is not None:
            return m
    return None


def _require_question(session: Session, project_id: int, question_id: str) -> OpenQuestionModel:
    m = _find_question(session, project_id, question_id)
    if m is None:
        raise QuestionNotFoundError(question_id)
    return m


def _next_question_id(session: Session, project_id: int) -> str:
    """Allocate ``Q-NNN`` as max+1 within the project (mirrors ``_next_scenario_id``).

    CLO-030: с настроенным remote — номер из аренды или провизорный ``Q-~xxxx``.
    """
    if leases.enabled(session, project_id):
        return leases.lease_number(session, project_id, _PREFIX, kind="open_question")
    stmt = select(OpenQuestionModel.question_id).where(OpenQuestionModel.project_id == project_id)
    highest = 0
    for value in session.execute(stmt).scalars():
        match = _QUESTION_ID_RE.match(value)
        if match is not None:
            highest = max(highest, int(match.group(1)))
    return f"Q-{highest + 1:0{_ID_WIDTH}d}"


def validate_question_id(question_id: str) -> None:
    if not _QUESTION_ID_RE.fullmatch(question_id):
        raise ValidationError(
            "OQ-001",
            f"invalid question_id {question_id!r}: expected 'Q-NNN'",
            question_id=question_id,
        )


def validate_text(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValidationError("OQ-002", f"{field_name} must not be empty", field=field_name)


def split_code_ref(to_ref: str) -> tuple[str, str | None]:
    """``path#fragment`` → (path, fragment); фрагмент — символ или ``L10-L20``."""
    path, sep, fragment = to_ref.partition("#")
    return path, (fragment if sep and fragment else None)


def validate_ref(to_kind: QuestionLinkKind, to_ref: str) -> None:
    """Проверить форму ``to_ref`` для вида ссылки; существование — не здесь."""
    if not to_ref.strip() or len(to_ref) > _MAX_REF_LEN or to_ref != to_ref.strip():
        raise ValidationError("OQ-003", f"invalid ref {to_ref!r}", to_kind=to_kind.value)
    if to_kind is QuestionLinkKind.CODE:
        path, _ = split_code_ref(to_ref)
        posix = PurePosixPath(path)
        if not path or posix.is_absolute() or ".." in posix.parts:
            raise ValidationError(
                "OQ-003",
                f"code ref must be a project-relative path[#symbol|#L10-L20], got {to_ref!r}",
                to_kind=to_kind.value,
            )
        return
    shape = _REF_SHAPES.get(to_kind)
    if shape is not None and not shape.fullmatch(to_ref):
        raise ValidationError(
            "OQ-003",
            f"ref {to_ref!r} does not look like a {to_kind.value} reference",
            to_kind=to_kind.value,
        )


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields}, ensure_ascii=False, default=str)


def audit_kwargs(
    model: OpenQuestionModel,
    *,
    author: str,
    op: str,
    reason: str | None,
    summary: str,
    payload: dict[str, object],
) -> dict[str, Any]:
    """Аргументы ``write_revision_and_emit_event`` для одной мутации вопроса.

    Сам вызов делает ``_record`` в каждом модуле мутаций: детектор паритета
    поверхностей (``tests/services/_surface_parity.py``) узнаёт мутацию по
    вызову audit-trail внутри модуля, через module-local хелперы.
    """
    return {
        "project_id": model.project_id,
        "entity_kind": EntityKind.QUESTION,
        "entity_id": model.row_id,
        "author": author,
        "diff": _diff(op, question_id=model.question_id, **payload),
        "reason": reason or op,
        "activity_kind": f"question.{op}",
        "activity_scope_kind": "question",
        "activity_scope_id": model.question_id,
        "activity_payload": payload,
        "activity_summary": summary,
    }
