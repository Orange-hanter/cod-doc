"""PCA-031: contextvar run_id propagation through RevisionService.write."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import AgentRunModel, ProjectModel, RevisionModel
from cod_doc.services import revision_service as rev
from cod_doc.services.run_context import (
    get_current_run_id,
    run_scope,
    set_current_run_id,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="rc", title="RC", root_path="/tmp/rc", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _write_rev(
    session: Session, project_id: int, *, entity_id: int, author: str = "x"
) -> str:
    """Write one task revision and return revision_id."""
    r = rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.TASK,
        entity_id=entity_id,
        author=author,
        diff='{"op":"create"}',
    )
    return r.revision_id


# --------------------------------------------------------------------------- #
# Scope semantics                                                              #
# --------------------------------------------------------------------------- #


def test_no_scope_means_run_id_is_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Mutations outside a run scope leave run_id NULL."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        rid = _write_rev(session, proj_id, entity_id=1)

    with transactional(factory) as session:
        model = session.execute(
            select(RevisionModel).where(RevisionModel.revision_id == rid)
        ).scalar_one()
        assert model.run_id is None


def test_run_scope_stamps_run_id_on_revisions(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        with run_scope(
            session,
            project_id=proj_id,
            run_id="01J0SCOPE",
            wake_reason="manual",
        ):
            rid_a = _write_rev(session, proj_id, entity_id=1)
            rid_b = _write_rev(session, proj_id, entity_id=2)

    with transactional(factory) as session:
        for rid in (rid_a, rid_b):
            r = session.execute(
                select(RevisionModel).where(RevisionModel.revision_id == rid)
            ).scalar_one()
            assert r.run_id == "01J0SCOPE"


def test_run_scope_marks_run_done_on_clean_exit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        with run_scope(session, project_id=proj_id, run_id="01J0DONE"):
            _write_rev(session, proj_id, entity_id=1)

    with transactional(factory) as session:
        run = session.execute(
            select(AgentRunModel).where(AgentRunModel.run_id == "01J0DONE")
        ).scalar_one()
        assert run.status == "done"
        assert run.finished_at is not None


def test_run_scope_marks_run_failed_on_exception(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        with (
            pytest.raises(RuntimeError),
            run_scope(session, project_id=proj_id, run_id="01J0FAIL"),
        ):
            _write_rev(session, proj_id, entity_id=1)
            raise RuntimeError("simulated agent crash")

    with transactional(factory) as session:
        run = session.execute(
            select(AgentRunModel).where(AgentRunModel.run_id == "01J0FAIL")
        ).scalar_one()
        assert run.status == "failed"
        assert run.finished_at is not None


def test_run_scope_resets_contextvar_on_exit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """After the scope returns, get_current_run_id() is None again."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        assert get_current_run_id() is None
        with run_scope(session, project_id=proj_id, run_id="01J0RESET"):
            assert get_current_run_id() == "01J0RESET"
        assert get_current_run_id() is None


def test_run_scope_resets_contextvar_after_failure(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """contextvar must reset even when the scope raised."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        assert get_current_run_id() is None
        with (
            pytest.raises(RuntimeError),
            run_scope(session, project_id=proj_id, run_id="01J0FAILRESET"),
        ):
            assert get_current_run_id() == "01J0FAILRESET"
            raise RuntimeError("crash")
        assert get_current_run_id() is None


def test_one_scope_one_run_id_across_multiple_revisions(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Acceptance: один прогон → все мутации имеют один и тот же run_id."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        with run_scope(session, project_id=proj_id, run_id="01J0BATCH"):
            for i in range(5):
                _write_rev(session, proj_id, entity_id=i + 1)

    with transactional(factory) as session:
        revs = list(
            session.execute(
                select(RevisionModel).where(RevisionModel.run_id == "01J0BATCH")
            ).scalars()
        )
        assert len(revs) == 5
        assert {r.run_id for r in revs} == {"01J0BATCH"}


# --------------------------------------------------------------------------- #
# Helper: set/get for tests                                                    #
# --------------------------------------------------------------------------- #


def test_set_current_run_id_overrides_contextvar(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        # `set_current_run_id` is the test/admin escape hatch — no scope context.
        try:
            set_current_run_id("manual-override")
            rid = _write_rev(session, proj_id, entity_id=1)
        finally:
            set_current_run_id(None)

    with transactional(factory) as session:
        r = session.execute(
            select(RevisionModel).where(RevisionModel.revision_id == rid)
        ).scalar_one()
        assert r.run_id == "manual-override"
