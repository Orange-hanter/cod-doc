"""ADO-232: рутина adr_health — пробелы реестра ADR.

Эталоны — литералы и прямые SELECT по ``finding``: ожидание не строится через
``adr_health.assess``, иначе тест повторял бы дефект, который должен ловить.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import FindingModel, ProjectModel
from cod_doc.services import adr_service, curator_service
from cod_doc.services import routine_service as routines
from cod_doc.services.adr_health import ADRRow, assess_rows

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def _row(status: str, *, dated: bool = True, decision: str | None = "x") -> ADRRow:
    return ADRRow(
        adr_id="ADR-001", title="T", status=status, has_decided_at=dated, decision=decision
    )


def test_rules() -> None:
    assert [i.code for i in assess_rows([_row("accepted", dated=False)])] == [
        "adr_missing_decided_at"
    ]
    assert [i.code for i in assess_rows([_row("proposed", decision="  ")])] == [
        "adr_empty_decision"
    ]
    # proposed без даты — норма: решение ещё не принято.
    assert assess_rows([_row("proposed", dated=False)]) == []
    # Недействующему решению пробелы не предъявляем.
    for status in ("superseded", "deprecated", "rejected"):
        assert assess_rows([_row(status, dated=False, decision=None)]) == []


def _project(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="ah", title="ah", root_path=str(root), config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    adr_service.create(
        session, project_id=proj.row_id, title="Без даты", status="accepted", decision="d"
    )
    routines.create(session, proj.row_id, name="adr", check_name="adr_health", trigger="manual")
    return proj.row_id


def _open_rows(session: Session, pid: int) -> list[FindingModel]:
    return list(
        session.execute(
            select(FindingModel).where(
                FindingModel.project_id == pid,
                FindingModel.source_ref == "adr_health",
                FindingModel.status == "open",
            )
        ).scalars()
    )


def test_adr_health_in_catalog() -> None:
    assert "adr_health" in routines.CHECK_CATALOG
    assert routines.__doc__ is not None
    assert "adr_health" in routines.__doc__


def test_routine_writes_and_closes_finding(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session, tmp_path)
        run = routines.run_now(session, pid, "adr")
        assert run.findings_count == 1

    with transactional(factory) as session:
        rows = _open_rows(session, pid)
        assert [r.kind for r in rows] == ["adr_missing_decided_at"]
        assert "ADR-001" in rows[0].title
        adr_service.sync_body(
            session, project_id=pid, adr_id="ADR-001", decided_at=date(2026, 9, 1)
        )
        routines.run_now(session, pid, "adr")

    with transactional(factory) as session:
        assert _open_rows(session, pid) == [], "вылеченный пробел закрывается сверкой"


def test_finding_visible_in_curator_next(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session, tmp_path)
        routines.run_now(session, pid, "adr")

    with transactional(factory, commit=False) as session:
        payload = curator_service.next(
            session,
            project_id=pid,
            root_path=tmp_path,
            master_path=tmp_path / "MASTER.md",
            project_slug="ah",
            skip_links=True,
        )
    titles = [str(f.get("title")) for f in payload["card"]["findings"]]
    assert any("ADR-001" in t and "без даты" in t for t in titles), titles
