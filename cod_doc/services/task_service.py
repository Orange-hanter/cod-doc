"""TaskService — write-path for tasks.

Public API:
- `create` — persist a new task, auto-generate `task_id` if omitted, write
  an initial TASK revision.
- `update_status` — set `task.status` to any value; no dep-gate (use
  `complete()` for the guarded transition to DONE).
- `complete` — validate all blocking deps are DONE, then set `status=done` +
  `completed_at` + optional `completed_commit`; writes TASK revision.

ID format:  `<PREFIX>-<NNN>` (e.g. `COD-011`, `AUTH-025`). Caller passes
`id_prefix` when `task_id=None`; the service finds the current max sequence
within the plan and increments. Format validation is COD-020's job.

Caller owns the transaction (`transactional()` from `cod_doc.infra.db`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.domain.entities import (
    AffectedFileKind,
    EntityKind,
    Priority,
    Task,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.models import AffectedFileModel, DependencyModel, TaskModel
from cod_doc.infra.repositories import TaskRepository
from cod_doc.services import revision_service as rev


class TaskNotFoundError(LookupError):
    pass


class TaskBlockedError(RuntimeError):
    """Raised by `complete()` when blocking deps are not yet done."""


class TaskAlreadyDoneError(RuntimeError):
    """Raised by `complete()` when the task is already done (idempotency guard)."""


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _require_task(session: Session, task_id: str) -> TaskModel:
    stmt = select(TaskModel).where(TaskModel.task_id == task_id)
    model = session.execute(stmt).scalar_one_or_none()
    if model is None:
        raise TaskNotFoundError(task_id)
    return model


def _next_task_id(session: Session, plan_id: int, prefix: str) -> str:
    """Return the next unused `{prefix}-NNN` id within the plan."""
    stmt = select(TaskModel.task_id).where(
        TaskModel.plan_id == plan_id,
        TaskModel.task_id.like(f"{prefix}-%"),
    )
    max_n = 0
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for tid in session.execute(stmt).scalars():
        m = pat.match(tid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}-{max_n + 1:03d}"


def _task_diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields})


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #


def create(
    session: Session,
    *,
    project_id: int,
    plan_id: int,
    section_id: int,
    title: str,
    type: TaskType,
    priority: Priority,
    author: str,
    task_id: str | None = None,
    id_prefix: str | None = None,
    description: str | None = None,
    acceptance: str | None = None,
    affected_files: list[str] | None = None,
    reason: str | None = None,
) -> Task:
    """Persist a task and write its initial revision.

    If `task_id` is None, `id_prefix` must be provided; the service assigns
    `{prefix}-NNN` where NNN is the next sequence within the plan.
    """
    if task_id is None:
        if not id_prefix:
            raise ValueError("provide task_id or id_prefix")
        task_id = _next_task_id(session, plan_id, id_prefix)

    now = datetime.now(timezone.utc)
    task = TaskRepository(session).add(
        Task(
            project_id=project_id,
            task_id=task_id,
            plan_id=plan_id,
            section_id=section_id,
            title=title,
            status=TaskStatus.PENDING,
            type=type,
            priority=priority,
            description=description,
            acceptance=acceptance,
            created=now,
            last_updated=now,
        )
    )
    assert task.row_id is not None

    if affected_files:
        for path in affected_files:
            session.add(
                AffectedFileModel(
                    task_id=task.row_id,
                    path=path,
                    kind=AffectedFileKind.SOURCE.value,
                )
            )
        session.flush()

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.TASK,
        entity_id=task.row_id,
        author=author,
        diff=_task_diff("create", task_id=task_id, status="pending"),
        reason=reason or "create",
    )
    return task


def update_status(
    session: Session,
    *,
    task_id: str,
    new_status: TaskStatus,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | None | object = rev.NO_PARENT_CHECK,
) -> Task:
    """Set task.status directly; no dep-gate.

    For the guarded `→done` transition that validates blocking deps, use
    `complete()` instead.
    Pass `expected_parent_revision_id` to detect concurrent writes
    (mirrors `patch_section` optimistic concurrency).
    """
    model = _require_task(session, task_id)
    old_status = model.status
    if old_status == new_status.value:
        t = TaskRepository(session).get_by_task_id(task_id)
        assert t is not None
        return t

    model.status = new_status.value
    model.last_updated = datetime.now(timezone.utc)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff("status", old=old_status, new=new_status.value),
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
    )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def complete(
    session: Session,
    *,
    task_id: str,
    author: str,
    commit_sha: str | None = None,
    reason: str | None = None,
    expected_parent_revision_id: str | None | object = rev.NO_PARENT_CHECK,
) -> Task:
    """Complete a task: validate deps → done, write revision.

    Raises `TaskAlreadyDoneError` if the task is already done.
    Raises `TaskBlockedError` if any `blocks`-type dep is not yet done.
    """
    model = _require_task(session, task_id)

    if model.status == TaskStatus.DONE.value:
        raise TaskAlreadyDoneError(task_id)

    # Check blocking deps (DATA_MODEL §7.2).
    blocking: list[str] = []
    dep_stmt = select(DependencyModel).where(
        DependencyModel.from_task_id == model.row_id,
        DependencyModel.kind == "blocks",
    )
    for dep in session.execute(dep_stmt).scalars():
        dep_task = session.get(TaskModel, dep.to_task_id)
        if dep_task is not None and dep_task.status != TaskStatus.DONE.value:
            blocking.append(dep_task.task_id)
    if blocking:
        raise TaskBlockedError(
            f"{task_id} blocked by: {', '.join(blocking)}"
        )

    now = datetime.now(timezone.utc)
    old_status = model.status
    model.status = TaskStatus.DONE.value
    model.completed_at = now
    model.completed_commit = commit_sha
    model.last_updated = now
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff(
            "complete",
            old_status=old_status,
            commit_sha=commit_sha,
        ),
        reason=reason or "complete",
        expected_parent_revision_id=expected_parent_revision_id,
    )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def get(session: Session, task_id: str) -> Task | None:
    return TaskRepository(session).get_by_task_id(task_id)


def list_for_plan(session: Session, plan_id: int) -> list[Task]:
    return TaskRepository(session).list_for_plan(plan_id)
