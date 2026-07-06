"""ADR-001: smoke for adr / adr_diagram / adr_supersedes / adr_task tables."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ADRDiagramModel,
    ADRModel,
    ADRSupersedeModel,
    ADRTaskModel,
    ProjectModel,
)


def _seed_project(session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="adrp", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_adr_create_and_read(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        adr = ADRModel(
            project_id=pid,
            adr_id="ADR-001",
            title="Layered architecture",
            status="accepted",
            decided_at=date(2026, 4, 5),
            context="Need pluggable infra",
            decision="4-layer + DIP",
            alternatives="Clean Architecture",
            consequences="+ testable; − boilerplate",
        )
        session.add(adr)
        session.flush()
        assert adr.row_id is not None

    with transactional(factory) as session:
        row = session.execute(select(ADRModel).where(ADRModel.adr_id == "ADR-001")).scalar_one()
        assert row.status == "accepted"
        assert row.decided_at == date(2026, 4, 5)
        assert "DIP" in row.decision


def test_adr_id_unique_per_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        session.add(ADRModel(project_id=pid, adr_id="ADR-007", title="A", status="proposed"))
    with pytest.raises(Exception):  # IntegrityError
        with transactional(factory) as session:
            pid = session.execute(select(ProjectModel.row_id)).scalar_one()
            session.add(ADRModel(project_id=pid, adr_id="ADR-007", title="B", status="proposed"))


def test_adr_status_check_constraint(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with pytest.raises(Exception):  # CheckConstraint
        with transactional(factory) as session:
            pid = _seed_project(session)
            session.add(ADRModel(project_id=pid, adr_id="ADR-010", title="X", status="weird"))


def test_adr_diagram_unique_position(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        adr = ADRModel(project_id=pid, adr_id="ADR-020", title="X", status="proposed")
        session.add(adr)
        session.flush()
        session.add(
            ADRDiagramModel(adr_id=adr.row_id, position=0, title="layers", mermaid="graph TD;A-->B")
        )
        session.add(
            ADRDiagramModel(adr_id=adr.row_id, position=1, title="flow", mermaid="graph LR;X-->Y")
        )
        session.flush()
    with pytest.raises(Exception):
        with transactional(factory) as session:
            adr = session.execute(select(ADRModel).where(ADRModel.adr_id == "ADR-020")).scalar_one()
            session.add(ADRDiagramModel(adr_id=adr.row_id, position=0, title="dup", mermaid="x"))


def test_adr_supersede_no_self_loop(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        a = ADRModel(project_id=pid, adr_id="ADR-030", title="Old", status="superseded")
        session.add(a)
        session.flush()
    with pytest.raises(Exception):  # ck_adr_supersedes_no_self_loop
        with transactional(factory) as session:
            a = session.execute(select(ADRModel).where(ADRModel.adr_id == "ADR-030")).scalar_one()
            session.add(ADRSupersedeModel(superseding_id=a.row_id, superseded_id=a.row_id))


def test_adr_supersede_dag(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        old = ADRModel(project_id=pid, adr_id="ADR-040", title="PostgreSQL", status="superseded")
        new = ADRModel(project_id=pid, adr_id="ADR-041", title="SQLite", status="accepted")
        session.add_all([old, new])
        session.flush()
        session.add(
            ADRSupersedeModel(
                superseding_id=new.row_id,
                superseded_id=old.row_id,
                reason="local-first; no docker dep",
            )
        )
        session.flush()

    with transactional(factory) as session:
        edges = list(session.execute(select(ADRSupersedeModel)).scalars())
        assert len(edges) == 1
        e = edges[0]
        assert "local-first" in (e.reason or "")


def test_adr_task_link_relation_check(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        adr = ADRModel(project_id=pid, adr_id="ADR-050", title="X", status="accepted")
        session.add(adr)
        session.flush()
        session.add(ADRTaskModel(adr_row_id=adr.row_id, task_id="COD-001", relation="implements"))
        session.flush()
    with pytest.raises(Exception):
        with transactional(factory) as session:
            adr = session.execute(select(ADRModel).where(ADRModel.adr_id == "ADR-050")).scalar_one()
            session.add(ADRTaskModel(adr_row_id=adr.row_id, task_id="COD-002", relation="weird"))
