"""TSC-004: scenario edges + authoring coverage."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from cod_doc.domain.entities import (
    ScenarioKind,
    ScenarioLinkKind,
    ScenarioRelation,
    ScenarioStatus,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import scenario_service


def _seed(session) -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="scn", title="P", root_path="/tmp/scn", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _create(session, pid: int, **kw):  # type: ignore[no-untyped-def]
    params = {
        "title": "A scenario",
        "kind": ScenarioKind.HAPPY_PATH,
        "group_key": "plan-management",
        "preconditions": "Something is true.",
        "expected": "Something else becomes true.",
        "steps": ["Do the thing"],
        "author": "human:test",
    }
    params.update(kw)
    return scenario_service.create(session, project_id=pid, **params)


# --------------------------------------------------------------------------- #
# links                                                                        #
# --------------------------------------------------------------------------- #


def test_link_and_list(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        scenario_service.link(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            to_kind=ScenarioLinkKind.TASK,
            to_ref="TSC-004",
            relation=ScenarioRelation.EXERCISED_BY,
            author="human:test",
        )
        links = scenario_service.list_links(session, s.row_id)
    assert [(link.to_kind.value, link.to_ref) for link in links] == [("task", "TSC-004")]


def test_relinking_the_same_edge_is_a_noop(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        args = {
            "project_id": pid,
            "scenario_id": s.scenario_id,
            "to_kind": ScenarioLinkKind.STORY,
            "to_ref": "US-013",
            "relation": ScenarioRelation.SPECIFIED_IN,
            "author": "human:test",
        }
        scenario_service.link(session, **args)
        scenario_service.link(session, **args)
        assert len(scenario_service.list_links(session, s.row_id)) == 1


def test_unlink_returns_false_when_absent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        removed = scenario_service.unlink(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            to_kind=ScenarioLinkKind.TASK,
            to_ref="NOPE-001",
            relation=ScenarioRelation.VERIFIES,
            author="human:test",
        )
    assert removed is False


def test_link_and_unlink_emit_events(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        args = {
            "project_id": pid,
            "scenario_id": s.scenario_id,
            "to_kind": ScenarioLinkKind.DOCUMENT,
            "to_ref": "docs/system/capabilities/plan-management",
            "relation": ScenarioRelation.SPECIFIED_IN,
            "author": "human:test",
        }
        scenario_service.link(session, **args)
        scenario_service.unlink(session, **args)
        session.flush()
        kinds = [
            e.kind
            for e in session.execute(
                select(ActivityEventModel)
                .where(ActivityEventModel.scope_id == s.scenario_id)
                .order_by(ActivityEventModel.row_id)
            ).scalars()
        ]
    assert kinds == ["scenario.created", "scenario.linked", "scenario.unlinked"]


# --------------------------------------------------------------------------- #
# authoring coverage                                                           #
# --------------------------------------------------------------------------- #


def test_group_coverage_reports_missing_kinds(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        _create(session, pid, kind=ScenarioKind.HAPPY_PATH)
        cov = scenario_service.group_coverage(session, pid, "plan-management")
    assert cov.total == 1
    assert cov.by_kind == {"happy_path": 1}
    assert cov.missing_kinds == ["error_path"]


def test_group_coverage_excludes_retired(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        _create(session, pid, kind=ScenarioKind.ERROR_PATH)
        scenario_service.retire(
            session, project_id=pid, scenario_id=s.scenario_id, author="human:test"
        )
        cov = scenario_service.group_coverage(session, pid, "plan-management")
    assert cov.total == 1
    assert cov.retired == 1
    assert cov.missing_kinds == ["happy_path"]


def test_group_coverage_counts_claim_statuses(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        _create(session, pid)
        second = _create(session, pid, kind=ScenarioKind.ERROR_PATH)
        scenario_service.update(
            session,
            project_id=pid,
            scenario_id=second.scenario_id,
            author="human:test",
            status=ScenarioStatus.CONFIRMED,
        )
        cov = scenario_service.group_coverage(session, pid, "plan-management")
    assert (cov.draft, cov.confirmed) == (1, 1)


def test_project_coverage_lists_every_group(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        _create(session, pid)
        _create(session, pid, group_key="doc-evolution")
        report = scenario_service.project_coverage(session, pid)
    assert [c.group_key for c in report] == ["doc-evolution", "plan-management"]
