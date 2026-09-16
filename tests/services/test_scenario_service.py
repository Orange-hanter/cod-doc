"""TSC-002: scenario_service CRUD + steps + ADO-040 write path."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    ScenarioKind,
    ScenarioStatus,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    ProjectModel,
    RevisionModel,
    ScenarioStepModel,
)
from cod_doc.services import doc_service, scenario_service
from cod_doc.services.scenario_service import ScenarioAlreadyExistsError, ScenarioNotFoundError
from cod_doc.services.validation import ValidationError

BODY = {
    "preconditions": "A plan with one open task exists.",
    "expected": "plan_progress reports one task done.",
}


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
        "title": "Plan progress recomputes after a task completes",
        "kind": ScenarioKind.HAPPY_PATH,
        "group_key": "plan-management",
        "steps": ["Complete the task", "Read the plan progress"],
        "author": "human:test",
        **BODY,
    }
    params.update(kw)
    return scenario_service.create(session, project_id=pid, **params)


# --------------------------------------------------------------------------- #
# create + id allocation                                                       #
# --------------------------------------------------------------------------- #


def test_create_allocates_first_scenario_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
    assert s.scenario_id == "SCN-001"
    assert s.status is ScenarioStatus.DRAFT


def test_scenario_ids_increment_within_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        _create(session, pid)
        second = _create(session, pid, kind=ScenarioKind.ERROR_PATH)
    assert second.scenario_id == "SCN-002"


def test_duplicate_scenario_id_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        _create(session, pid, scenario_id="SCN-007")
        with pytest.raises(ScenarioAlreadyExistsError):
            _create(session, pid, scenario_id="SCN-007")


def test_group_key_defaults_to_doc_key_basename(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(
            session,
            pid,
            group_key=None,
            doc_key="docs/system/capabilities/plan-management",
        )
    assert s.group_key == "plan-management"


def test_create_requires_group_or_doc_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(ValueError, match="group_key or doc_key"):
            _create(session, pid, group_key=None)


def test_steps_are_persisted_in_order(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid, steps=["first", "second", "third"])
        rows = session.execute(
            select(ScenarioStepModel)
            .where(ScenarioStepModel.scenario_row_id == s.row_id)
            .order_by(ScenarioStepModel.position)
        ).scalars()
        assert [r.text for r in rows] == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# document anchor                                                              #
# --------------------------------------------------------------------------- #


def test_anchor_resolves_document_and_snapshots_hash(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        doc_service.create(
            session,
            project_id=pid,
            doc_key="docs/system/capabilities/plan-management",
            type=DocumentType.CAPABILITY,
            status=DocumentStatus.ACTIVE,
            title="Plan management",
            owner="cod-doc core",
            author="human:test",
        )
        s = _create(
            session,
            pid,
            group_key=None,
            doc_key="docs/system/capabilities/plan-management",
        )
    assert s.document_id is not None
    assert s.doc_key == "docs/system/capabilities/plan-management"


def test_unimported_anchor_is_tolerated(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A scenario may be authored before its capability document is imported."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid, group_key=None, doc_key="docs/system/capabilities/adr-system")
    assert s.document_id is None
    assert s.doc_key == "docs/system/capabilities/adr-system"


# --------------------------------------------------------------------------- #
# validation                                                                   #
# --------------------------------------------------------------------------- #


def test_coverage_verdict_rejected_as_status(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """RFC 24 §9 verdicts are producer evidence, never a hand-typed claim status."""
    from cod_doc.services import validation

    for verdict in ("covered", "partial", "missing", "unverifiable"):
        with pytest.raises(ValidationError) as exc:
            validation.validate_scenario_status(verdict)
        assert exc.value.code == "SCV-003"
        assert "scenario_assessment" in str(exc.value)


def test_body_without_steps_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(ValidationError) as exc:
            _create(session, pid, steps=[])
        assert exc.value.code == "SCV-005"


def test_bad_group_key_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(ValidationError) as exc:
            _create(session, pid, group_key="Plan Management")
        assert exc.value.code == "SCV-004"


# --------------------------------------------------------------------------- #
# update / retire                                                              #
# --------------------------------------------------------------------------- #


def test_update_changes_only_given_fields(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        updated = scenario_service.update(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            author="human:test",
            title="New title",
        )
    assert updated.title == "New title"
    assert updated.expected == BODY["expected"]


def test_update_without_changes_writes_no_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        before = _revision_count(session, s.row_id)
        scenario_service.update(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            author="human:test",
            title=s.title,
        )
        assert _revision_count(session, s.row_id) == before


def test_retire_is_idempotent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        scenario_service.retire(
            session, project_id=pid, scenario_id=s.scenario_id, author="human:test"
        )
        before = _revision_count(session, s.row_id)
        again = scenario_service.retire(
            session, project_id=pid, scenario_id=s.scenario_id, author="human:test"
        )
        assert again.status is ScenarioStatus.RETIRED
        assert _revision_count(session, s.row_id) == before


def test_unknown_scenario_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(ScenarioNotFoundError):
            scenario_service.retire(
                session, project_id=pid, scenario_id="SCN-404", author="human:test"
            )


# --------------------------------------------------------------------------- #
# steps                                                                        #
# --------------------------------------------------------------------------- #


def test_set_steps_replaces_and_renumbers(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid, steps=["a", "b", "c"])
        steps = scenario_service.set_steps(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            steps=["only one"],
            author="human:test",
        )
    assert [st.text for st in steps] == ["only one"]
    assert [st.position for st in steps] == [0]


def test_add_step_appends(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid, steps=["a"])
        steps = scenario_service.add_step(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            text="b",
            author="human:test",
        )
    assert [st.text for st in steps] == ["a", "b"]


# --------------------------------------------------------------------------- #
# ADO-040: revision + activity event on every mutation                         #
# --------------------------------------------------------------------------- #


def _revision_count(session, row_id: int) -> int:  # type: ignore[no-untyped-def]
    return len(
        list(
            session.execute(
                select(RevisionModel).where(
                    RevisionModel.entity_kind == EntityKind.SCENARIO.value,
                    RevisionModel.entity_id == row_id,
                )
            ).scalars()
        )
    )


def _event_kinds(session, scenario_id: str) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        e.kind
        for e in session.execute(
            select(ActivityEventModel)
            .where(
                ActivityEventModel.scope_kind == "scenario",
                ActivityEventModel.scope_id == scenario_id,
            )
            .order_by(ActivityEventModel.row_id)
        ).scalars()
    ]


def test_every_mutation_writes_revision_and_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid)
        scenario_service.update(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            author="human:test",
            title="Changed",
        )
        scenario_service.set_steps(
            session,
            project_id=pid,
            scenario_id=s.scenario_id,
            steps=["x", "y"],
            author="human:test",
        )
        scenario_service.retire(
            session, project_id=pid, scenario_id=s.scenario_id, author="human:test"
        )
        session.flush()

        assert _event_kinds(session, s.scenario_id) == [
            "scenario.created",
            "scenario.updated",
            "scenario.steps_set",
            "scenario.retired",
        ]
        assert _revision_count(session, s.row_id) == 4


def test_actor_kind_derived_from_author(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        s = _create(session, pid, author="agent:run-1")
        session.flush()
        event = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.scope_id == s.scenario_id)
        ).scalar_one()
    assert event.actor_kind == "agent"


# --------------------------------------------------------------------------- #
# reads                                                                        #
# --------------------------------------------------------------------------- #


def test_list_for_group_is_ordered_by_position(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        _create(session, pid, kind=ScenarioKind.HAPPY_PATH)
        _create(session, pid, kind=ScenarioKind.ERROR_PATH)
        _create(session, pid, group_key="doc-evolution")

        group = scenario_service.list_for_group(session, pid, "plan-management")
        assert [s.position for s in group] == [0, 1]
        assert scenario_service.group_keys(session, pid) == [
            "doc-evolution",
            "plan-management",
        ]
