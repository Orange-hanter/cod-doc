"""Module-internal helpers — lookup, id allocation, doc anchoring, diff fragments."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import DocumentModel, ScenarioModel, SectionModel

from ._types import ScenarioNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SCENARIO_ID_RE = re.compile(r"^SCN-(\d{3})$")
_ID_WIDTH = 3


def _require_scenario(session: Session, project_id: int, scenario_id: str) -> ScenarioModel:
    stmt = select(ScenarioModel).where(
        ScenarioModel.project_id == project_id,
        ScenarioModel.scenario_id == scenario_id,
    )
    m = session.execute(stmt).scalar_one_or_none()
    if m is None:
        raise ScenarioNotFoundError(scenario_id)
    return m


def _next_scenario_id(session: Session, project_id: int) -> str:
    """Allocate ``SCN-NNN`` as max+1 within the project (mirrors ``_next_adr_id``)."""
    stmt = select(ScenarioModel.scenario_id).where(ScenarioModel.project_id == project_id)
    highest = 0
    for value in session.execute(stmt).scalars():
        match = _SCENARIO_ID_RE.match(value)
        if match is not None:
            highest = max(highest, int(match.group(1)))
    return f"SCN-{highest + 1:0{_ID_WIDTH}d}"


def _derive_group_key(doc_key: str) -> str:
    """Group scenarios by the anchored document's basename.

    ``docs/system/capabilities/plan-management`` -> ``plan-management``.
    """
    return doc_key.rsplit("/", 1)[-1]


def _resolve_doc_anchor(
    session: Session,
    *,
    project_id: int,
    doc_key: str | None,
    section_anchor: str | None,
) -> tuple[int | None, str | None]:
    """Resolve ``doc_key`` to a document row and snapshot the anchored content hash.

    Returns ``(document_id, doc_content_hash)``. A missing document is not an
    error here — it is reported advisorily by ``audit_scenario_anchor``, so a
    scenario can be authored before its capability document is imported.
    """
    if doc_key is None:
        return None, None

    doc = session.execute(
        select(DocumentModel).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()
    if doc is None:
        return None, None

    if section_anchor is None:
        return doc.row_id, doc.content_sha256_head

    section_hash = session.execute(
        select(SectionModel.content_hash).where(
            SectionModel.document_id == doc.row_id,
            SectionModel.anchor == section_anchor,
        )
    ).scalar_one_or_none()
    return doc.row_id, section_hash


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields})
