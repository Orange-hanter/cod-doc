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
    assert [i.code for i in assess_rows([_row("proposed", dated=False, decision="  ")])] == [
        "adr_empty_decision"
    ]
    # proposed без даты — норма: решение ещё не принято.
    assert assess_rows([_row("proposed", dated=False)]) == []
    # Недействующему решению пробелы не предъявляем.
    for status in ("superseded", "deprecated", "rejected"):
        assert assess_rows([_row(status, dated=False, decision=None)]) == []


_TODAY = date(2026, 10, 3)


def test_proposed_with_date_is_flagged() -> None:
    """ARG-003: дата у непринятого изображает решение, которого нет (ADR-009)."""
    issues = assess_rows([_row("proposed", dated=True)], today=_TODAY)
    assert [(i.code, i.scope_id) for i in issues] == [("adr_proposed_has_date", "ADR-001")]
    assert "--clear-decided-at" in issues[0].body


def test_stale_proposal_threshold() -> None:
    """Ровно 30 дней — ещё норма, 31 — находка; у accepted срок не считается."""

    def row(status: str, created: date) -> ADRRow:
        return ADRRow(
            adr_id="ADR-009",
            title="T",
            status=status,
            has_decided_at=False,
            decision="x",
            created=created,
        )

    assert assess_rows([row("proposed", date(2026, 9, 3))], today=_TODAY) == []
    issues = assess_rows([row("proposed", date(2026, 9, 2))], today=_TODAY)
    assert [i.code for i in issues] == ["adr_stale_proposal"]
    assert "31" in issues[0].title
    # ADR-009 на живой БД: предложен 2026-05-17.
    assert "139" in assess_rows([row("proposed", date(2026, 5, 17))], today=_TODAY)[0].title
    accepted = ADRRow(
        adr_id="ADR-001",
        title="T",
        status="accepted",
        has_decided_at=True,
        decision="x",
        created=date(2026, 1, 1),
    )
    assert assess_rows([accepted], today=_TODAY) == []


def test_depends_on_closed_target() -> None:
    """Опора на снятое решение — находка на пару; на действующее — нет."""

    def row(status: str, deps: tuple[tuple[str, str], ...]) -> ADRRow:
        return ADRRow(
            adr_id="ADR-017",
            title="T",
            status=status,
            has_decided_at=status == "accepted",
            decision="x",
            depends_on=deps,
        )

    deps = (("ADR-016", "rejected"), ("ADR-010", "accepted"))
    issues = assess_rows([row("proposed", deps)], today=_TODAY)
    assert [(i.code, i.scope_kind, i.scope_id) for i in issues] == [
        ("adr_depends_on_closed", "adr_relation", "ADR-017->ADR-016")
    ]
    # Источник сам снят — его опоры не предъявляем.
    assert assess_rows([row("deprecated", deps)], today=_TODAY) == []


def test_depends_on_closed_read_from_db(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``assess`` читает depends_on из adr_relation вместе со статусом цели."""
    from cod_doc.services import adr_health

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session, tmp_path)
        adr_service.create(session, project_id=pid, title="Реплики", adr_id="ADR-016", decision="d")
        adr_service.create(
            session, project_id=pid, title="Идентичность", adr_id="ADR-017", decision="d"
        )
        adr_service.relate(
            session, project_id=pid, from_adr_id="ADR-017", to_adr_id="ADR-016", kind="depends_on"
        )
        assert "adr_depends_on_closed" not in {i.code for i in adr_health.assess(session, pid)}
        adr_service.update(session, project_id=pid, adr_id="ADR-016", status="rejected")
        codes = [(i.code, i.scope_id) for i in adr_health.assess(session, pid)]
    assert ("adr_depends_on_closed", "ADR-017->ADR-016") in codes


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
