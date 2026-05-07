"""PCA-023: run_agent_once wake-flow helpers.

Direct tests of the public helpers `_resolve_task` and `_build_wake_for_run`
in legacy_agent_tools — covers the trigger-param translation that the
@mcp.tool() decorator wraps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cod_doc.agent.wake_context import WakeReason
from cod_doc.config import ProjectEntry
from cod_doc.core.project import Project, Task, TaskStatus
from cod_doc.mcp.tools.legacy_agent_tools import _build_wake_for_run, _resolve_task

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def project(tmp_path: Path) -> Project:
    entry = ProjectEntry(name="rao-test", path=str(tmp_path))
    proj = Project(entry)
    proj.init()
    return proj


def test_resolve_task_returns_yaml_task_by_id(project: Project) -> None:
    t1 = Task(title="task one")
    t2 = Task(title="task two")
    project.add_task(t1)
    project.add_task(t2)

    found = _resolve_task(project, t2.id)
    assert found is not None
    assert found.id == t2.id
    assert found.title == "task two"


def test_resolve_task_falls_back_to_next_pending_when_id_unknown(project: Project) -> None:
    t1 = Task(title="pending one")
    project.add_task(t1)

    found = _resolve_task(project, "deadbeef")  # not in YAML
    assert found is not None
    assert found.id == t1.id


def test_resolve_task_returns_none_when_no_id_and_no_pending(project: Project) -> None:
    t = Task(title="completed")
    project.add_task(t)
    project.update_task(t.id, status=TaskStatus.DONE)

    assert _resolve_task(project, None) is None


def test_resolve_task_no_id_returns_next_pending(project: Project) -> None:
    t = Task(title="pending only")
    project.add_task(t)
    found = _resolve_task(project, None)
    assert found is not None
    assert found.id == t.id


# --------------------------------------------------------------------------- #
# _build_wake_for_run                                                          #
# --------------------------------------------------------------------------- #


def test_build_wake_returns_none_when_wake_reason_unset(project: Project) -> None:
    wake = _build_wake_for_run(
        project,
        wake_reason=None,
        task_id=None,
        triggering_doc_ref=None,
        since_revision_id=None,
    )
    assert wake is None


def test_build_wake_doc_drift_no_session_needed(project: Project) -> None:
    wake = _build_wake_for_run(
        project,
        wake_reason="doc_drift",
        task_id=None,
        triggering_doc_ref="doc:foo",
        since_revision_id="01J0000",
    )
    assert wake is not None
    assert wake.reason is WakeReason.DOC_DRIFT
    assert wake.is_scoped is True
    assert wake.payload == {"doc_ref": "doc:foo", "since_revision_id": "01J0000"}


def test_build_wake_cold_start_empty_payload(project: Project) -> None:
    wake = _build_wake_for_run(
        project,
        wake_reason="cold_start",
        task_id=None,
        triggering_doc_ref=None,
        since_revision_id=None,
    )
    assert wake is not None
    assert wake.reason is WakeReason.COLD_START
    assert wake.is_scoped is False
    assert wake.payload == {}


def test_build_wake_manual_does_not_require_task_or_doc(project: Project) -> None:
    wake = _build_wake_for_run(
        project,
        wake_reason="manual",
        task_id=None,
        triggering_doc_ref=None,
        since_revision_id=None,
    )
    assert wake is not None
    assert wake.reason is WakeReason.MANUAL


def test_build_wake_doc_drift_without_doc_ref_raises(project: Project) -> None:
    with pytest.raises(ValueError, match="triggering_doc_ref"):
        _build_wake_for_run(
            project,
            wake_reason="doc_drift",
            task_id=None,
            triggering_doc_ref=None,
            since_revision_id=None,
        )


def test_build_wake_unknown_reason_string_raises(project: Project) -> None:
    with pytest.raises(ValueError):
        _build_wake_for_run(
            project,
            wake_reason="not_a_real_reason",
            task_id=None,
            triggering_doc_ref=None,
            since_revision_id=None,
        )
