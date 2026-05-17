"""ADR-002: AdrService CRUD + supersede + link + graph."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ADRSupersedeModel,
    ADRTaskModel,
    ProjectModel,
    RevisionModel,
)
from cod_doc.services import adr_service
from cod_doc.services.adr_service import (
    ADRAlreadyExistsError,
    ADRNotFoundError,
)


def _seed(session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="adrs", title="P", root_path="/tmp", config_json={})
    proj.created = now; proj.updated = now
    session.add(proj); session.flush()
    return proj.row_id


# ----------------------------------------------------------------- #
# create + auto-id                                                   #
# ----------------------------------------------------------------- #


def test_create_auto_assigns_first_adr_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        r = adr_service.create(session, project_id=pid, title="X")
    assert r.adr_id == "ADR-001"


def test_create_auto_increments_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="A")
        adr_service.create(session, project_id=pid, title="B")
        r = adr_service.create(session, project_id=pid, title="C")
    assert r.adr_id == "ADR-003"


def test_create_explicit_id_validates_format(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(ValueError, match="ADR-NNN"):
            adr_service.create(session, project_id=pid, title="X", adr_id="ADR-1")


def test_create_duplicate_explicit_id_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="A", adr_id="ADR-099")
    with pytest.raises(ADRAlreadyExistsError), transactional(factory) as session:
        pid = session.execute(select(ProjectModel.row_id)).scalar_one()
        adr_service.create(session, project_id=pid, title="dup", adr_id="ADR-099")


def test_create_with_full_payload(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        r = adr_service.create(
            session, project_id=pid, title="Layered arch",
            status="accepted", decided_at=date(2026, 4, 5),
            context="DI requirement", decision="4-layer",
            alternatives="Clean Arch", consequences="+ testable; − boilerplate",
        )
    assert r.status == "accepted"
    assert r.decided_at == date(2026, 4, 5)


# ----------------------------------------------------------------- #
# get / list / update                                                #
# ----------------------------------------------------------------- #


def test_get_returns_full_row(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X")
    with transactional(factory) as session:
        r = adr_service.get(session, 1, "ADR-001")
    assert r is not None and r.title == "X"


def test_get_miss_returns_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with transactional(factory) as session:
        assert adr_service.get(session, 1, "ADR-999") is None


def test_list_filters_by_status(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="a1", status="accepted")
        adr_service.create(session, project_id=pid, title="a2", status="accepted")
        adr_service.create(session, project_id=pid, title="p1", status="proposed")
    with transactional(factory) as session:
        accepted = adr_service.list_for_project(session, 1, status="accepted")
        proposed = adr_service.list_for_project(session, 1, status="proposed")
    assert len(accepted) == 2
    assert len(proposed) == 1


def test_update_changes_fields(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="old")
    with transactional(factory) as session:
        r = adr_service.update(
            session, project_id=1, adr_id="ADR-001",
            title="new", status="accepted",
        )
    assert r.title == "new"
    assert r.status == "accepted"


def test_update_unknown_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with pytest.raises(ADRNotFoundError), transactional(factory) as session:
        adr_service.update(session, project_id=1, adr_id="ADR-999", title="x")


# ----------------------------------------------------------------- #
# diagrams                                                            #
# ----------------------------------------------------------------- #


def test_add_diagram_appends(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="x")
    with transactional(factory) as session:
        d1 = adr_service.add_diagram(session, project_id=1, adr_id="ADR-001",
                                     mermaid="graph TD;A-->B", title="first")
        d2 = adr_service.add_diagram(session, project_id=1, adr_id="ADR-001",
                                     mermaid="graph LR;X-->Y", title="second")
    assert d1.position == 0
    assert d2.position == 1


# ----------------------------------------------------------------- #
# supersede                                                          #
# ----------------------------------------------------------------- #


def test_supersede_creates_edge_and_flips_status(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="old", status="accepted")
        adr_service.create(session, project_id=pid, title="new", status="accepted")
    with transactional(factory) as session:
        adr_service.supersede(
            session, project_id=1,
            superseding_adr_id="ADR-002", superseded_adr_id="ADR-001",
            reason="better approach",
        )
    with transactional(factory) as session:
        old = adr_service.get(session, 1, "ADR-001")
        new = adr_service.get(session, 1, "ADR-002")
        edges = list(session.execute(select(ADRSupersedeModel)).scalars())
    assert old.status == "superseded"
    assert new.status == "accepted"
    assert len(edges) == 1
    assert "better" in (edges[0].reason or "")


def test_supersede_idempotent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="a")
        adr_service.create(session, project_id=pid, title="b")
    with transactional(factory) as session:
        adr_service.supersede(
            session, project_id=1,
            superseding_adr_id="ADR-002", superseded_adr_id="ADR-001",
        )
    with transactional(factory) as session:
        adr_service.supersede(
            session, project_id=1,
            superseding_adr_id="ADR-002", superseded_adr_id="ADR-001",
        )
    with transactional(factory) as session:
        edges = list(session.execute(select(ADRSupersedeModel)).scalars())
    assert len(edges) == 1


def test_supersede_self_loop_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="x")
    with pytest.raises(ValueError, match="itself"), transactional(factory) as session:
        adr_service.supersede(
            session, project_id=1,
            superseding_adr_id="ADR-001", superseded_adr_id="ADR-001",
        )


# ----------------------------------------------------------------- #
# link_task                                                          #
# ----------------------------------------------------------------- #


def test_link_task_idempotent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="x")
    with transactional(factory) as session:
        adr_service.link_task(session, project_id=1, adr_id="ADR-001", task_id="COD-001")
        adr_service.link_task(session, project_id=1, adr_id="ADR-001", task_id="COD-001")
    with transactional(factory) as session:
        links = list(session.execute(select(ADRTaskModel)).scalars())
    assert len(links) == 1


def test_link_task_invalid_relation(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="x")
    with pytest.raises(ValueError, match="invalid relation"), transactional(factory) as session:
        adr_service.link_task(
            session, project_id=1, adr_id="ADR-001", task_id="t", relation="weird",
        )


# ----------------------------------------------------------------- #
# graph                                                              #
# ----------------------------------------------------------------- #


def test_graph_returns_nodes_and_edges(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="A", status="superseded")
        adr_service.create(session, project_id=pid, title="B", status="accepted")
        adr_service.create(session, project_id=pid, title="C", status="proposed")
        adr_service.supersede(
            session, project_id=pid,
            superseding_adr_id="ADR-002", superseded_adr_id="ADR-001",
        )
    with transactional(factory) as session:
        g = adr_service.graph(session, project_id=1)
    assert len(g["nodes"]) == 3
    assert len(g["edges"]) == 1
    edge = g["edges"][0]
    assert edge["from"] == "ADR-002"
    assert edge["to"] == "ADR-001"


# ----------------------------------------------------------------- #
# revisions (entity_kind=ADR)                                         #
# ----------------------------------------------------------------- #


def _adr_revisions(session, *, project_id: int) -> list[RevisionModel]:
    return list(
        session.execute(
            select(RevisionModel)
            .where(
                RevisionModel.project_id == project_id,
                RevisionModel.entity_kind == EntityKind.ADR.value,
            )
            .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
        ).scalars()
    )


def test_create_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X", author="human:test")
    with transactional(factory) as session:
        revs = _adr_revisions(session, project_id=1)
    assert len(revs) == 1
    assert revs[0].author == "human:test"
    assert '"op": "create"' in revs[0].diff


def test_update_writes_revision_only_for_changes(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X")
    with transactional(factory) as session:
        adr_service.update(session, project_id=1, adr_id="ADR-001", title="X")  # no-op
        adr_service.update(session, project_id=1, adr_id="ADR-001", title="Y")
    with transactional(factory) as session:
        revs = _adr_revisions(session, project_id=1)
    assert len(revs) == 2  # create + 1 real update
    assert '"op": "update"' in revs[-1].diff


def test_supersede_writes_revision_on_old_adr(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        a = adr_service.create(session, project_id=pid, title="A", status="accepted")
        adr_service.create(session, project_id=pid, title="B", status="accepted")
    with transactional(factory) as session:
        adr_service.supersede(
            session, project_id=1,
            superseding_adr_id="ADR-002", superseded_adr_id="ADR-001",
            reason="r",
        )
    with transactional(factory) as session:
        revs = list(
            session.execute(
                select(RevisionModel).where(
                    RevisionModel.entity_kind == EntityKind.ADR.value,
                    RevisionModel.entity_id == a.row_id,
                )
            ).scalars()
        )
    # one create + one supersede (on the OLD ADR)
    assert len(revs) == 2
    assert '"op": "supersede"' in revs[-1].diff


def test_supersede_rejects_cycle(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A → B, then B → A must be rejected."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="A", status="accepted")
        adr_service.create(session, project_id=pid, title="B", status="accepted")
        adr_service.supersede(
            session, project_id=pid,
            superseding_adr_id="ADR-002", superseded_adr_id="ADR-001",
        )
    with pytest.raises(ValueError, match="cycle"), transactional(factory) as session:
        adr_service.supersede(
            session, project_id=1,
            superseding_adr_id="ADR-001", superseded_adr_id="ADR-002",
        )


def test_link_task_writes_revision_once(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="x")
    with transactional(factory) as session:
        adr_service.link_task(session, project_id=1, adr_id="ADR-001", task_id="T-1")
        adr_service.link_task(session, project_id=1, adr_id="ADR-001", task_id="T-1")  # dup
    with transactional(factory) as session:
        revs = _adr_revisions(session, project_id=1)
    create_revs = [r for r in revs if '"op": "create"' in r.diff]
    link_revs = [r for r in revs if '"op": "link_task"' in r.diff]
    assert len(create_revs) == 1
    assert len(link_revs) == 1


def test_add_diagram_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="x")
    with transactional(factory) as session:
        adr_service.add_diagram(
            session, project_id=1, adr_id="ADR-001",
            mermaid="graph TD; A-->B", title="d",
        )
    with transactional(factory) as session:
        revs = _adr_revisions(session, project_id=1)
    diagram_revs = [r for r in revs if '"op": "add_diagram"' in r.diff]
    assert len(diagram_revs) == 1


# ----------------------------------------------------------------- #
# Markdown projection (P2-1)                                          #
# ----------------------------------------------------------------- #


def test_render_markdown_returns_full_md(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(
            session, project_id=pid, title="Layered arch",
            status="accepted",
            context="DI needed", decision="4-layer DDD",
            alternatives="Clean Arch", consequences="+ testable",
        )
    with transactional(factory) as session:
        md = adr_service.render_markdown(session, project_id=1, adr_id="ADR-001")
    assert "ADR-001: Layered arch" in md
    assert "✅ accepted" in md
    assert "DI needed" in md
    assert "4-layer DDD" in md
    assert "+ testable" in md


def test_render_markdown_includes_diagrams_and_supersedes(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="A", status="accepted")
        adr_service.create(session, project_id=pid, title="B", status="accepted")
        adr_service.supersede(
            session, project_id=pid,
            superseding_adr_id="ADR-002", superseded_adr_id="ADR-001",
            reason="perf",
        )
        adr_service.add_diagram(
            session, project_id=pid, adr_id="ADR-002",
            mermaid="graph TD;A-->B", title="flow",
        )
    with transactional(factory) as session:
        md = adr_service.render_markdown(session, project_id=1, adr_id="ADR-002")
    assert "graph TD;A-->B" in md
    assert "```mermaid" in md
    assert "ADR-001" in md  # supersedes section
    assert "perf" in md     # supersedes reason


def test_export_to_disk_idempotent(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X")
        adr_service.create(session, project_id=pid, title="Y")
    out = tmp_path / "adr_export"
    with transactional(factory) as session:
        written1 = adr_service.export_to_disk(session, project_id=1, out_dir=out)
        written2 = adr_service.export_to_disk(session, project_id=1, out_dir=out)
    # First pass writes both, second pass is no-op.
    assert {p.name for p in written1} == {"ADR-001.md", "ADR-002.md"}
    assert written2 == []
