"""ARG-001 (RFC 34 §3.1): связи ADR «уточняет» и «опирается на»."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    ADRModel,
    ADRRelationModel,
    ProjectModel,
    RevisionModel,
)
from cod_doc.services import adr_service
from cod_doc.services.adr_service import (
    ADRNotFoundError,
    ADRRelationExistsError,
    ADRRelationNotFoundError,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def _seed(session: Session) -> int:
    """Проект и три ADR в форме живого реестра: 005 и 010 приняты, 016 — черновик."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="adrs", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    pid = proj.row_id
    adr_service.create(
        session, project_id=pid, title="PostgreSQL", adr_id="ADR-005", status="accepted"
    )
    adr_service.create(
        session, project_id=pid, title="Два профиля", adr_id="ADR-010", status="accepted"
    )
    adr_service.create(session, project_id=pid, title="Реплики", adr_id="ADR-016")
    return pid


def _revisions(session: Session, adr_id: str, op: str) -> list[dict[str, object]]:
    row_id = session.execute(select(ADRModel.row_id).where(ADRModel.adr_id == adr_id)).scalar_one()
    diffs = (
        session.execute(
            select(RevisionModel.diff).where(
                RevisionModel.entity_kind == EntityKind.ADR, RevisionModel.entity_id == row_id
            )
        )
        .scalars()
        .all()
    )
    return [d for d in (json.loads(x) for x in diffs) if d["op"] == op]


def test_relate_writes_edge_revision_and_event(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        edge = adr_service.relate(
            session,
            project_id=pid,
            from_adr_id="ADR-010",
            to_adr_id="ADR-005",
            kind="amends",
            reason="уточняет две вещи, которых в ADR-005 не было",
            author="human:dakh",
        )
        assert edge.kind == "amends"
        assert _revisions(session, "ADR-010", "relate") == [
            {
                "op": "relate",
                "adr_id": "ADR-010",
                "to": "ADR-005",
                "kind": "amends",
                "reason": "уточняет две вещи, которых в ADR-005 не было",
            }
        ]
        # Ревизия — на заявившем связь, у адресата ничего не пишется.
        assert _revisions(session, "ADR-005", "relate") == []
        session.flush()  # emit() кладёт событие без flush, а autoflush выключен
        events = (
            session.execute(
                select(ActivityEventModel.payload).where(ActivityEventModel.kind == "adr.related")
            )
            .scalars()
            .all()
        )
        assert events == [
            {
                "to": "ADR-005",
                "kind": "amends",
                "reason": "уточняет две вещи, которых в ADR-005 не было",
            }
        ]


def test_relate_keeps_both_statuses(engine_with_schema: Engine) -> None:
    """В отличие от supersede, связь статусов не трогает — и разрешена от accepted."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-010", to_adr_id="ADR-005", kind="amends"
        )
        statuses = dict(session.execute(select(ADRModel.adr_id, ADRModel.status)).all())
    assert statuses == {"ADR-005": "accepted", "ADR-010": "accepted", "ADR-016": "proposed"}


def test_relate_repeat_is_a_conflict(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-010", to_adr_id="ADR-005", kind="amends"
        )
        with pytest.raises(ADRRelationExistsError, match="already amends ADR-005"):
            adr_service.relate(
                session,
                project_id=pid,
                from_adr_id="ADR-010",
                to_adr_id="ADR-005",
                kind="amends",
            )
        # Другой вид той же пары — другая связь, не повтор.
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-010", to_adr_id="ADR-005", kind="depends_on"
        )
        assert session.query(ADRRelationModel).count() == 2


@pytest.mark.parametrize(
    ("from_id", "to_id", "kind", "error", "match"),
    [
        ("ADR-010", "ADR-010", "amends", ValueError, "itself"),
        ("ADR-010", "ADR-005", "relates", ValueError, "invalid relation kind"),
        ("ADR-010", "ADR-999", "amends", ADRNotFoundError, "ADR-999"),
    ],
)
def test_relate_rejects_bad_input(
    engine_with_schema: Engine,
    from_id: str,
    to_id: str,
    kind: str,
    error: type[Exception],
    match: str,
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(error, match=match):
            adr_service.relate(
                session, project_id=pid, from_adr_id=from_id, to_adr_id=to_id, kind=kind
            )


def test_relate_rejects_cycle_within_kind_only(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-016", to_adr_id="ADR-010", kind="depends_on"
        )
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-010", to_adr_id="ADR-005", kind="depends_on"
        )
        with pytest.raises(ValueError, match="cycle"):
            adr_service.relate(
                session,
                project_id=pid,
                from_adr_id="ADR-005",
                to_adr_id="ADR-016",
                kind="depends_on",
            )
        # Обратное ребро другого вида циклом не считается.
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-005", to_adr_id="ADR-016", kind="amends"
        )


def test_unrelate_removes_edge_and_writes_revision(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.relate(
            session,
            project_id=pid,
            from_adr_id="ADR-010",
            to_adr_id="ADR-005",
            kind="amends",
            reason="r",
        )
        adr_service.unrelate(
            session,
            project_id=pid,
            from_adr_id="ADR-010",
            to_adr_id="ADR-005",
            kind="amends",
            reason="ошибочная связь",
        )
        assert session.query(ADRRelationModel).count() == 0
        assert _revisions(session, "ADR-010", "unrelate") == [
            {
                "op": "unrelate",
                "adr_id": "ADR-010",
                "to": "ADR-005",
                "kind": "amends",
                "old_reason": "r",
            }
        ]
        with pytest.raises(ADRRelationNotFoundError):
            adr_service.unrelate(
                session,
                project_id=pid,
                from_adr_id="ADR-010",
                to_adr_id="ADR-005",
                kind="amends",
            )


def test_adr_to_dict_carries_relations_both_ways(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-010", to_adr_id="ADR-005", kind="amends"
        )
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-016", to_adr_id="ADR-010", kind="amends"
        )
        adr_010 = adr_service.get(session, pid, "ADR-010")
        assert adr_010 is not None
        rels = adr_service.adr_to_dict(session, adr_010)["relations"]
    assert rels == {
        "outgoing": [
            {
                "adr_id": "ADR-005",
                "title": "PostgreSQL",
                "status": "accepted",
                "kind": "amends",
                "reason": None,
            }
        ],
        "incoming": [
            {
                "adr_id": "ADR-016",
                "title": "Реплики",
                "status": "proposed",
                "kind": "amends",
                "reason": None,
            }
        ],
    }


def test_graph_lists_relations_apart_from_supersede_edges(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.relate(
            session,
            project_id=pid,
            from_adr_id="ADR-016",
            to_adr_id="ADR-010",
            kind="depends_on",
            reason="x",
        )
        g = adr_service.graph(session, pid)
    assert g["edges"] == []
    assert g["relations"] == [
        {"from": "ADR-016", "to": "ADR-010", "kind": "depends_on", "reason": "x"}
    ]
