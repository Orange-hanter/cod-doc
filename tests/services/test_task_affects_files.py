"""AFT-021: правка ``affects_files`` существующей задачи.

Три уровня:

* service — ``task_service.update_affects_files``: режимы replace/add/remove,
  схлопывание дублей, пустой набор, no-op без ревизии, AFT-012 warning для
  путей вне ``root_path``, TASK-ревизия ``op=affects_files`` и её откат
  через ``revision_service.revert``;
* MCP — ``task_update(affects_files=…, affects_files_mode=…)``;
* локальность (RFC 27 F13) — ``task_get`` и ready-выборка видят новый набор.

CLI-эквивалент — tests/cli/test_task_update.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    AffectedFileModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
)
from cod_doc.services import plan_service as plans
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks
from cod_doc.services.task_service import TaskNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_ROOT = "/repo/proj"
_TASK = "AFF-001"


def _seed(session: Session, affected: list[str] | None = None) -> tuple[int, int]:
    """Проект с root '/repo/proj', план, одна todo-задача ``AFF-001``."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="af", title="AF", root_path=_ROOT, config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="af-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
    session.add(sec)
    session.flush()
    task = tasks.create(
        session,
        project_id=proj.row_id,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=_TASK,
        title="Task with files",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        affected_files=affected,
    )
    assert task.row_id is not None
    return proj.row_id, task.row_id


def _other_project(session: Session) -> int:
    """Второй проект без задач — для проверок скоупа по проекту."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="other", title="O", root_path="/repo/other", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _paths(session: Session, row_id: int) -> list[str]:
    return sorted(
        session.execute(
            select(AffectedFileModel.path).where(AffectedFileModel.task_id == row_id)
        ).scalars()
    )


def _revisions(session: Session, row_id: int) -> int:
    return len(rev.list_for_entity(session, EntityKind.TASK, row_id))


# --------------------------------------------------------------------------- #
# service                                                                      #
# --------------------------------------------------------------------------- #


def test_replace_swaps_whole_set_and_leaves_trail(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py", "src/b.py"])

        change = tasks.update_affects_files(
            session,
            task_id=_TASK,
            paths=["src/b.py", "src/c.py"],
            mode="replace",
            author="agent:run-X",
            reason="переписан скоуп",
        )

        assert change.changed is True
        assert change.old == ["src/a.py", "src/b.py"]
        assert change.new == ["src/b.py", "src/c.py"]
        assert change.warnings == []
        assert _paths(session, row_id) == ["src/b.py", "src/c.py"]

        last = rev.list_for_entity(session, EntityKind.TASK, row_id)[-1]
        assert json.loads(last.diff) == {
            "op": "affects_files",
            "mode": "replace",
            "old": ["src/a.py", "src/b.py"],
            "new": ["src/b.py", "src/c.py"],
        }
        assert last.reason == "переписан скоуп"

        session.flush()
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "task.affects_files_updated",
                    ActivityEventModel.scope_id == _TASK,
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].actor_kind == "agent"
        assert events[0].payload["added"] == ["src/c.py"]
        assert events[0].payload["removed"] == ["src/a.py"]


def test_add_appends_only_new_paths(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py"])

        change = tasks.update_affects_files(
            session, task_id=_TASK, paths=["src/a.py", "src/z.py"], mode="add", author="human:t"
        )

        assert change.new == ["src/a.py", "src/z.py"]
        assert _paths(session, row_id) == ["src/a.py", "src/z.py"]


def test_remove_drops_listed_paths_and_ignores_absent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py", "src/b.py"])

        change = tasks.update_affects_files(
            session,
            task_id=_TASK,
            paths=["src/a.py", "src/never.py"],
            mode="remove",
            author="human:t",
        )

        assert change.new == ["src/b.py"]
        assert _paths(session, row_id) == ["src/b.py"]


def test_same_set_is_noop_without_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py", "src/b.py"])
        before = _revisions(session, row_id)

        # Тот же набор в другом порядке и с дублем — не изменение.
        replaced = tasks.update_affects_files(
            session,
            task_id=_TASK,
            paths=["src/b.py", "src/a.py", "src/b.py"],
            mode="replace",
            author="human:t",
        )
        added = tasks.update_affects_files(
            session, task_id=_TASK, paths=["src/a.py"], mode="add", author="human:t"
        )
        removed = tasks.update_affects_files(
            session, task_id=_TASK, paths=["src/none.py"], mode="remove", author="human:t"
        )

        assert not (replaced.changed or added.changed or removed.changed)
        assert _revisions(session, row_id) == before, "no-op не должен писать ревизию"
        session.flush()
        assert (
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "task.affects_files_updated"
                )
            ).first()
            is None
        )


def test_replace_with_empty_list_clears_set(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py"])

        change = tasks.update_affects_files(
            session, task_id=_TASK, paths=[], mode="replace", author="human:t"
        )

        assert change.changed is True
        assert change.new == []
        assert _paths(session, row_id) == []


def test_duplicates_collapse(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session)

        change = tasks.update_affects_files(
            session,
            task_id=_TASK,
            paths=["src/a.py", "src/a.py", "src/b.py", "src/a.py"],
            mode="replace",
            author="human:t",
        )

        assert change.new == ["src/a.py", "src/b.py"]
        assert _paths(session, row_id) == ["src/a.py", "src/b.py"]


def test_blank_path_is_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
        with pytest.raises(ValueError, match="пуст"):
            tasks.update_affects_files(
                session, task_id=_TASK, paths=["  "], mode="replace", author="human:t"
            )


def test_paths_are_stripped_before_dedup(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """``" src/a.py "`` и ``"src/a.py"`` — один путь, а не визуальный дубль."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py"])

        change = tasks.update_affects_files(
            session, task_id=_TASK, paths=[" src/a.py ", "\tsrc/b.py"], mode="add", author="human:t"
        )

        assert change.new == ["src/a.py", "src/b.py"]
        assert _paths(session, row_id) == ["src/a.py", "src/b.py"]


@pytest.mark.parametrize(
    ("paths", "match"),
    [(["  "], "пуст"), ([""], "пуст"), (["src/a.py", "\n"], "пуст"), (["a\x00b"], "NUL")],
)
def test_invalid_paths_are_rejected(  # type: ignore[no-untyped-def]
    engine_with_schema, paths: list[str], match: str
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/keep.py"])
        with pytest.raises(ValueError, match=match):
            tasks.update_affects_files(
                session, task_id=_TASK, paths=paths, mode="replace", author="human:t"
            )
        assert _paths(session, row_id) == ["src/keep.py"]


@pytest.mark.parametrize("path", ["../outside.py", "..\\win.py", "/etc/passwd"])
def test_unusual_paths_are_stored_as_given(engine_with_schema, path: str) -> None:  # type: ignore[no-untyped-def]
    """Контракт: путь — строка-метка для локальности, файловых операций по нему нет.

    Как и `create()`, сервис его не нормализует и не отвергает; путь вне
    ``root_path`` (абсолютный) даёт AFT-012 warning.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session)
        change = tasks.update_affects_files(
            session, task_id=_TASK, paths=[path], mode="replace", author="human:t"
        )
        assert _paths(session, row_id) == [path]
        assert bool(change.warnings) == path.startswith("/")


def test_foreign_project_id_raises_not_found(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py"])
        other_pid = _other_project(session)
        with pytest.raises(TaskNotFoundError):
            tasks.update_affects_files(
                session,
                task_id=_TASK,
                paths=["src/x.py"],
                mode="replace",
                author="human:t",
                project_id=other_pid,
            )
        assert _paths(session, row_id) == ["src/a.py"]


def test_unknown_mode_is_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
        with pytest.raises(ValueError, match="mode"):
            tasks.update_affects_files(
                session, task_id=_TASK, paths=["a"], mode="merge", author="human:t"
            )


def test_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
        with pytest.raises(TaskNotFoundError):
            tasks.update_affects_files(
                session, task_id="NOPE-999", paths=["a"], mode="replace", author="human:t"
            )


def test_out_of_root_path_warns_but_is_written(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session)

        change = tasks.update_affects_files(
            session,
            task_id=_TASK,
            paths=["/elsewhere/x.py", "/repo/proj/in.py", "rel.py"],
            mode="replace",
            author="human:t",
        )

        assert len(change.warnings) == 1
        assert "/elsewhere/x.py" in change.warnings[0]
        assert "/repo/proj/in.py" not in change.warnings[0]
        assert _paths(session, row_id) == ["/elsewhere/x.py", "/repo/proj/in.py", "rel.py"]


@pytest.mark.parametrize("mode", ["replace", "add"])
def test_already_stored_foreign_path_does_not_warn_again(  # type: ignore[no-untyped-def]
    engine_with_schema, mode: str
) -> None:
    """Предупреждение — только о реально добавленных путях: no-op не шумит (ревью PR #190)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session, ["/elsewhere/x.py", "rel.py"])

        change = tasks.update_affects_files(
            session,
            task_id=_TASK,
            paths=["/elsewhere/x.py", "rel.py"] if mode == "replace" else ["/elsewhere/x.py"],
            mode=mode,
            author="human:t",
        )

        assert change.changed is False
        assert change.warnings == []


def test_remove_mode_never_warns(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session, ["/elsewhere/x.py", "/elsewhere/y.py"])

        change = tasks.update_affects_files(
            session, task_id=_TASK, paths=["/elsewhere/x.py"], mode="remove", author="human:t"
        )

        assert change.warnings == []


def test_revision_revert_restores_previous_set(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py", "src/b.py"])
        tasks.update_affects_files(
            session, task_id=_TASK, paths=["src/c.py"], mode="replace", author="human:t"
        )
        target = rev.list_for_entity(session, EntityKind.TASK, row_id)[-1]

        rev.revert(session, target.revision_id, author="human:undo")

        assert _paths(session, row_id) == ["src/a.py", "src/b.py"]
        inverse = rev.list_for_entity(session, EntityKind.TASK, row_id)[-1]
        assert inverse.revision_id != target.revision_id
        assert inverse.reason == f"revert revision {target.revision_id}"
        assert json.loads(inverse.diff) == {
            "op": "affects_files",
            "mode": "replace",
            "old": ["src/c.py"],
            "new": ["src/a.py", "src/b.py"],
        }


def test_revision_revert_of_add_restores_set(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session)
        tasks.update_affects_files(
            session, task_id=_TASK, paths=["src/new.py"], mode="add", author="human:t"
        )
        target = rev.list_for_entity(session, EntityKind.TASK, row_id)[-1]

        rev.revert(session, target.revision_id, author="human:undo")

        assert _paths(session, row_id) == []


@pytest.mark.parametrize("old", ["src/a.py", None, [1, 2]])
def test_revision_revert_rejects_malformed_old(engine_with_schema, old: object) -> None:  # type: ignore[no-untyped-def]
    """``old`` не список строк — отказ, а не набор из символов строки."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py"])
        tasks.update_affects_files(
            session, task_id=_TASK, paths=["src/b.py"], mode="replace", author="human:t"
        )
        target = rev.list_for_entity(session, EntityKind.TASK, row_id)[-1]
        model = session.execute(
            select(RevisionModel).where(RevisionModel.revision_id == target.revision_id)
        ).scalar_one()
        diff = json.loads(model.diff)
        if old is None:
            del diff["old"]
        else:
            diff["old"] = old
        model.diff = json.dumps(diff)
        session.flush()

        with pytest.raises(rev.RevertNotSupportedError):
            rev.revert(session, target.revision_id, author="human:undo")
        assert _paths(session, row_id) == ["src/b.py"]


# --------------------------------------------------------------------------- #
# MCP: task_update                                                             #
# --------------------------------------------------------------------------- #


def _tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _register(monkeypatch, factory, proj_id: int) -> FastMCP:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return mcp


def test_mcp_task_update_replaces_affects_files(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, row_id = _seed(session, ["src/a.py"])

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    out = task_update(
        project="af", task_id=_TASK, affects_files=["src/x.py", "src/x.py", "src/y.py"]
    )

    assert out["updated_fields"] == ["affects_files"]
    assert out["affects_files"] == ["src/x.py", "src/y.py"]
    assert "warnings" not in out
    with transactional(factory) as session:
        assert _paths(session, row_id) == ["src/x.py", "src/y.py"]


def test_mcp_task_update_add_and_remove_modes(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _row_id = _seed(session, ["src/a.py", "src/b.py"])

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    out = task_update(
        project="af", task_id=_TASK, affects_files=["src/c.py"], affects_files_mode="add"
    )
    assert out["affects_files"] == ["src/a.py", "src/b.py", "src/c.py"]

    out = task_update(
        project="af",
        task_id=_TASK,
        affects_files=["src/a.py", "src/c.py"],
        affects_files_mode="remove",
    )
    assert out["affects_files"] == ["src/b.py"]


def test_mcp_task_update_empty_list_clears(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, row_id = _seed(session, ["src/a.py"])

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    out = task_update(project="af", task_id=_TASK, affects_files=[])

    assert out["updated_fields"] == ["affects_files"]
    assert out["affects_files"] == []
    with transactional(factory) as session:
        assert _paths(session, row_id) == []


def test_mcp_task_update_none_leaves_affects_files(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, row_id = _seed(session, ["src/a.py"])
        before = _revisions(session, row_id)

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    out = task_update(project="af", task_id=_TASK, priority="high")

    assert out["updated_fields"] == ["priority"]
    assert out["affects_files"] == ["src/a.py"]
    with transactional(factory) as session:
        assert _revisions(session, row_id) == before + 1, "только ревизия приоритета"


def test_mcp_task_update_same_set_writes_no_revision(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, row_id = _seed(session, ["src/a.py"])
        before = _revisions(session, row_id)

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    out = task_update(project="af", task_id=_TASK, affects_files=["src/a.py"])

    assert out["updated_fields"] == [], "no-op не числится изменённым полем"
    with transactional(factory) as session:
        assert _revisions(session, row_id) == before


def test_mcp_task_update_foreign_project_not_found(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Задача проекта A через ``project=B`` — «не найдена», набор A не тронут."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _pid, row_id = _seed(session, ["src/a.py"])
        other_pid = _other_project(session)

    task_update = _tool(_register(monkeypatch, factory, other_pid), "task_update")
    with pytest.raises(ValueError, match="not found"):
        task_update(project="other", task_id=_TASK, affects_files=["src/x.py"])

    with transactional(factory) as session:
        assert _paths(session, row_id) == ["src/a.py"]


def test_mcp_task_update_out_of_root_warns(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, row_id = _seed(session)

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    out = task_update(project="af", task_id=_TASK, affects_files=["/elsewhere/x.py"])

    assert len(out["warnings"]) == 1
    assert "/elsewhere/x.py" in out["warnings"][0]
    with transactional(factory) as session:
        assert _paths(session, row_id) == ["/elsewhere/x.py"]


def test_mcp_task_update_rejects_unknown_mode(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _row_id = _seed(session)

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    with pytest.raises(ValueError, match="affects_files_mode"):
        task_update(project="af", task_id=_TASK, affects_files=["a"], affects_files_mode="x")


def test_mcp_task_update_dry_run_keeps_set(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, row_id = _seed(session, ["src/a.py"])

    task_update = _tool(_register(monkeypatch, factory, pid), "task_update")
    out = task_update(project="af", task_id=_TASK, affects_files=["src/b.py"], dry_run=True)

    assert out["dry_run"] is True
    assert out["affects_files"] == ["src/b.py"]
    with transactional(factory) as session:
        assert _paths(session, row_id) == ["src/a.py"]


# --------------------------------------------------------------------------- #
# локальность (RFC 27 F13): task_get и ready-выборка видят новый набор          #
# --------------------------------------------------------------------------- #


def test_task_turns_local_after_paths_change(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _row_id = _seed(session, ["/elsewhere/a.py", "/elsewhere/b.py"])
        foreign = plans.ready_batch_for_project(session, pid)
        assert [t.task_id for t in foreign.tasks] == []
        assert foreign.skipped_foreign == 1

    mcp = _register(monkeypatch, factory, pid)
    _tool(mcp, "task_update")(
        project="af",
        task_id=_TASK,
        affects_files=["/repo/proj/src/dispute.py", "test/dispute/"],
    )

    got = _tool(mcp, "task_get")(project="af", task_id=_TASK)
    assert got["affects_files"] == ["/repo/proj/src/dispute.py", "test/dispute/"]
    with transactional(factory) as session:
        local = plans.ready_batch_for_project(session, pid)
        assert [t.task_id for t in local.tasks] == [_TASK]
        assert local.skipped_foreign == 0
    assert _tool(mcp, "task_next_ready")(project="af")["task_id"] == _TASK


def test_task_turns_foreign_after_paths_change(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Чужая задача не попадает в ``ready_for_project`` вовсе.

    Здесь plain-вариант, а не ``ready_batch_for_project``, как выше: проверяем
    сам ready-набор, а счётчик ``skipped_foreign`` уже покрыт соседним тестом.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _row_id = _seed(session, ["src/a.py"])
        assert [t.task_id for t in plans.ready_for_project(session, pid)] == [_TASK]

        tasks.update_affects_files(
            session,
            task_id=_TASK,
            paths=["/elsewhere/a.py"],
            mode="replace",
            author="human:t",
        )

        assert plans.ready_for_project(session, pid) == []
