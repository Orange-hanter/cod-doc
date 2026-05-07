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
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import (
    AffectedFileKind,
    EntityKind,
    Priority,
    Task,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.models import (
    AffectedFileModel,
    DependencyModel,
    PlanModel,
    ProjectModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.repositories import TaskRepository
from cod_doc.infra.sql_helpers import priority_sql_order
from cod_doc.services import event_bus, validation
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class TaskNotFoundError(LookupError):
    pass


class TaskBlockedError(RuntimeError):
    """Raised by `complete()` when blocking deps are not yet done."""


class TaskAlreadyDoneError(RuntimeError):
    """Raised by `complete()` when the task is already done (idempotency guard)."""


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _project_slug(session: Session, project_id: int) -> str | None:
    """Resolve a project's slug for live-event routing. Returns None if unknown."""
    return session.execute(
        select(ProjectModel.slug).where(ProjectModel.row_id == project_id)
    ).scalar_one_or_none()


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


from cod_doc.domain.text import normalize_title as _normalize_title  # noqa: E402


class DuplicateTaskError(ValueError):
    """Raised by `create()` when an existing task has the same normalized title.

    Carries the colliding task's `task_id` so callers can either reference
    the existing task or retry with `allow_duplicate=True`.
    """

    def __init__(self, *, existing_task_id: str, normalized_title: str) -> None:
        super().__init__(
            f"task with similar title already exists: {existing_task_id} "
            f"(normalized: {normalized_title!r})"
        )
        self.existing_task_id = existing_task_id
        self.normalized_title = normalized_title


def find_duplicate_by_title(
    session: Session, project_id: int, title: str
) -> Task | None:
    """Return an existing task in the project with the same normalized title.

    Uses the indexed ``task.normalized_title`` column (COD-076). Returns
    ``None`` if no candidate is found. Comparison is exact on
    ``_normalize_title(title)`` — case/punctuation/whitespace insensitive.

    Falls back to a per-row scan for legacy rows that haven't been
    backfilled (NULL normalized_title) — this should be empty after the
    0009 migration runs.
    """
    normalized = _normalize_title(title)
    if not normalized:
        return None

    repo = TaskRepository(session)
    stmt = select(TaskModel).where(
        TaskModel.project_id == project_id,
        TaskModel.normalized_title == normalized,
    )
    model = session.execute(stmt).scalar_one_or_none()
    if model is not None:
        return repo._to_domain(model)

    # Fallback for un-backfilled rows.
    legacy = session.execute(
        select(TaskModel).where(
            TaskModel.project_id == project_id,
            TaskModel.normalized_title.is_(None),
        )
    ).scalars()
    for m in legacy:
        if _normalize_title(m.title) == normalized:
            return repo._to_domain(m)
    return None


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
    blocked_by: list[str] | None = None,
    story_id: str | None = None,
    blocked_reason: str | None = None,
    reason: str | None = None,
    allow_duplicate: bool = True,
) -> Task:
    """Persist a task and write its initial revision.

    If `task_id` is None, `id_prefix` must be provided; the service assigns
    `{prefix}-NNN` where NNN is the next sequence within the plan.

    Structured links (PCA-902/903 — close cycle-2 G2/G3 gaps):
    - ``blocked_by``: list of task_id strings; each becomes a `dependency`
      row (kind='blocks', from=new_task → to=blocker). Unknown task_id
      raises ``ValueError``.
    - ``story_id``: a story_id string; becomes a `story_link` row
      (story → to_kind='task', to_ref=task_id, relation='implemented_by').
      Unknown story_id raises ``ValueError``.

    When `allow_duplicate=False`, `find_duplicate_by_title` is consulted
    before insert; a match raises `DuplicateTaskError`. The default `True`
    preserves legacy callers and tests; new MCP/CLI surfaces flip it to
    `False` so duplicate prevention is the default at the user boundary.
    """
    if not allow_duplicate:
        existing = find_duplicate_by_title(session, project_id, title)
        if existing is not None:
            assert existing.task_id is not None
            raise DuplicateTaskError(
                existing_task_id=existing.task_id,
                normalized_title=_normalize_title(title),
            )

    if task_id is None:
        if not id_prefix:
            raise ValueError("provide task_id or id_prefix")
        validation.validate_id_prefix(id_prefix)
        task_id = _next_task_id(session, plan_id, id_prefix)
    else:
        validation.validate_task_id(task_id)
    validation.validate_task_type(type.value)

    now = datetime.now(UTC)
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
            blocked_reason=blocked_reason,
            created=now,
            last_updated=now,
        )
    )
    assert task.row_id is not None

    # COD-076: backfill the indexed dedupe column. Title is immutable after
    # create (no update_title path exists), so this single write is enough.
    inserted_model = session.get(TaskModel, task.row_id)
    if inserted_model is not None:
        inserted_model.normalized_title = _normalize_title(title)
        session.flush()

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

    if blocked_by:
        for blocker_task_id in blocked_by:
            blocker_model = session.execute(
                select(TaskModel).where(TaskModel.task_id == blocker_task_id)
            ).scalar_one_or_none()
            if blocker_model is None:
                raise ValueError(
                    f"blocked_by references unknown task_id: {blocker_task_id!r}"
                )
            session.add(
                DependencyModel(
                    from_task_id=task.row_id,
                    to_task_id=blocker_model.row_id,
                    kind="blocks",
                )
            )
        session.flush()

    if story_id is not None:
        story_model = session.execute(
            select(UserStoryModel).where(
                UserStoryModel.project_id == project_id,
                UserStoryModel.story_id == story_id,
            )
        ).scalar_one_or_none()
        if story_model is None:
            raise ValueError(
                f"story_id references unknown story: {story_id!r}"
            )
        session.add(
            StoryLinkModel(
                story_id=story_model.row_id,
                to_kind="task",
                to_ref=task_id,
                relation="implemented_by",
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
    if (slug := _project_slug(session, project_id)) is not None:
        event_bus.queue_emit(
            session,
            slug,
            "task.created",
            task_id=task.task_id,
            title=task.title,
            status=task.status.value,
            priority=task.priority.value,
            type=task.type.value,
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
    model.last_updated = datetime.now(UTC)
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
    if (slug := _project_slug(session, model.project_id)) is not None:
        event_bus.queue_emit(
            session,
            slug,
            "task.status_changed",
            task_id=task_id,
            old=old_status,
            new=new_status.value,
        )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def _update_text_field(
    session: Session,
    *,
    task_id: str,
    field: str,
    new_value: str,
    author: str,
    reason: str | None,
    expected_parent_revision_id: str | None | object,
) -> Task:
    """Shared body for update_description / update_acceptance.

    No-ops when value is unchanged. Writes a TASK revision with
    op=`<field>` carrying old/new content (length-only when very long).
    """
    model = _require_task(session, task_id)
    old_value = getattr(model, field) or ""
    if old_value == new_value:
        t = TaskRepository(session).get_by_task_id(task_id)
        assert t is not None
        return t

    setattr(model, field, new_value or None)
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff(
            field,
            old_len=len(old_value),
            new_len=len(new_value),
            old_preview=(old_value[:80] + "…") if len(old_value) > 80 else old_value,
            new_preview=(new_value[:80] + "…") if len(new_value) > 80 else new_value,
        ),
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
    )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def update_description(
    session: Session,
    *,
    task_id: str,
    new_description: str,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | None | object = rev.NO_PARENT_CHECK,
) -> Task:
    """Replace task.description; writes a TASK revision (op=description)."""
    return _update_text_field(
        session,
        task_id=task_id,
        field="description",
        new_value=new_description,
        author=author,
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
    )


def update_acceptance(
    session: Session,
    *,
    task_id: str,
    new_acceptance: str,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | None | object = rev.NO_PARENT_CHECK,
) -> Task:
    """Replace task.acceptance; writes a TASK revision (op=acceptance)."""
    return _update_text_field(
        session,
        task_id=task_id,
        field="acceptance",
        new_value=new_acceptance,
        author=author,
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
    )


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
        raise TaskBlockedError(f"{task_id} blocked by: {', '.join(blocking)}")

    now = datetime.now(UTC)
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
    # COD-022: signal plan staleness so callers know projection may be outdated.
    plan_model = session.get(PlanModel, model.plan_id)
    if plan_model is not None:
        plan_model.last_updated = now
    session.flush()

    if (slug := _project_slug(session, model.project_id)) is not None:
        event_bus.queue_emit(
            session,
            slug,
            "task.status_changed",
            task_id=task_id,
            old=old_status,
            new=TaskStatus.DONE.value,
        )

    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def get(session: Session, task_id: str) -> Task | None:
    return TaskRepository(session).get_by_task_id(task_id)


def list_for_plan(session: Session, plan_id: int) -> list[Task]:
    return TaskRepository(session).list_for_plan(plan_id)


def list_for_project(
    session: Session,
    project_id: int,
    *,
    status: TaskStatus | None = None,
    priority: Priority | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Task]:
    """List tasks for a project with optional status/priority filters and pagination.

    A `limit=None` returns all matching tasks (legacy behaviour). Callers
    fronted by MCP/REST should pass a finite limit to bound payload size.
    """
    return TaskRepository(session).list_for_project(
        project_id,
        status=status,
        priority=priority,
        limit=limit,
        offset=offset,
    )


def count_for_project(
    session: Session,
    project_id: int,
    *,
    status: TaskStatus | None = None,
    priority: Priority | None = None,
) -> int:
    """Return the count of matching tasks (paired with list_for_project)."""
    from sqlalchemy import func

    stmt = select(func.count(TaskModel.row_id)).where(TaskModel.project_id == project_id)
    if status is not None:
        stmt = stmt.where(TaskModel.status == status.value)
    if priority is not None:
        stmt = stmt.where(TaskModel.priority == priority.value)
    return int(session.execute(stmt).scalar_one() or 0)


def set_blocker(
    session: Session,
    *,
    task_id: str,
    reason: str,
    author: str,
) -> Task:
    """Mark a task as externally blocked (free-text reason).

    Stored in ``task.blocked_reason``. Independent of the ``dependency`` edge
    graph (which models task→task blocks). Writes a TASK revision with
    op=set_blocker.
    """
    if not reason or not reason.strip():
        raise ValueError("reason must be non-empty")

    model = _require_task(session, task_id)
    old_reason = model.blocked_reason
    model.blocked_reason = reason
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff("set_blocker", old=old_reason, new=reason),
        reason="set_blocker",
    )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def clear_blocker(
    session: Session,
    *,
    task_id: str,
    author: str,
) -> Task:
    """Clear the external blocker on a task (no-op if already clear)."""
    model = _require_task(session, task_id)
    if model.blocked_reason is None:
        t = TaskRepository(session).get(model.row_id)
        assert t is not None
        return t

    old_reason = model.blocked_reason
    model.blocked_reason = None
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff("clear_blocker", old=old_reason),
        reason="clear_blocker",
    )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def list_blocked(
    session: Session,
    project_id: int,
) -> list[Task]:
    """Return tasks that have an external blocker set (blocked_reason IS NOT NULL).

    DONE tasks are excluded — once a task is finished its old blocker is
    historical noise.
    """
    stmt = (
        select(TaskModel)
        .where(
            TaskModel.project_id == project_id,
            TaskModel.blocked_reason.is_not(None),
            TaskModel.status != TaskStatus.DONE.value,
        )
        .order_by(priority_sql_order(TaskModel.priority), TaskModel.task_id)
    )
    repo = TaskRepository(session)
    return [repo._to_domain(m) for m in session.execute(stmt).scalars()]


def list_stale_in_progress(
    session: Session,
    project_id: int,
    *,
    threshold_hours: float = 24.0,
) -> list[Task]:
    """Return in-progress tasks whose ``last_updated`` is older than the threshold.

    Used to surface "stuck" agents — a task in IN_PROGRESS without any
    revision activity for ``threshold_hours`` is treated as stale and
    deserves human attention (or an automatic reset to PENDING).
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=threshold_hours)
    stmt = (
        select(TaskModel)
        .where(
            TaskModel.project_id == project_id,
            TaskModel.status == TaskStatus.IN_PROGRESS.value,
            TaskModel.last_updated < cutoff,
        )
        .order_by(TaskModel.last_updated)
    )
    repo = TaskRepository(session)
    return [repo._to_domain(m) for m in session.execute(stmt).scalars()]


def log_progress(
    session: Session,
    *,
    task_id: str,
    message: str,
    author: str,
) -> Task:
    """Record a progress note on an in-progress task.

    Touches ``last_updated`` (so the task no longer looks stale) and writes
    a revision with ``op=progress`` carrying the message. Does not change
    status — the caller already moved the task to IN_PROGRESS.
    """
    if not message or not message.strip():
        raise ValueError("message must be non-empty")

    model = _require_task(session, task_id)
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff("progress", message=message),
        reason="progress",
    )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def summarize_for_project(session: Session, project_id: int) -> dict:  # type: ignore[type-arg]
    """Aggregate counts by status and priority — cheap overview for big projects.

    Returns: {
      "total": int,
      "by_status": {"pending": N, "in-progress": N, "done": N, ...},
      "by_priority": {"critical": N, "high": N, "medium": N, "low": N},
    }
    """
    from sqlalchemy import func

    by_status: dict[str, int] = {}
    rows = session.execute(
        select(TaskModel.status, func.count(TaskModel.row_id))
        .where(TaskModel.project_id == project_id)
        .group_by(TaskModel.status)
    ).all()
    for s, n in rows:
        by_status[s] = int(n)

    by_priority: dict[str, int] = {}
    rows = session.execute(
        select(TaskModel.priority, func.count(TaskModel.row_id))
        .where(TaskModel.project_id == project_id)
        .group_by(TaskModel.priority)
    ).all()
    for p, n in rows:
        by_priority[p] = int(n)

    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_priority": by_priority,
    }
