"""TaskService — write-path for tasks.

Public API:
- `create` — persist a new task, auto-generate `task_id` if omitted, write
  an initial TASK revision.
- `update_status` — set `task.status` to any value; no dep-gate (use
  `complete()` for the guarded transition to DONE).
- `update_description` / `update_acceptance` / `update_priority` — grooming
  правки уже созданной задачи (ADO-067); каждая пишет TASK revision +
  activity event и выставлена в CLI (`cod-doc task update`) и MCP
  (`task_update`).
- `move_to_section` — переложить задачу в другую секцию того же плана
  (группировка бэклога); пишет TASK revision (op=section) + activity event,
  выставлена в CLI (`cod-doc task move`) и MCP (`task_move_to_section`).
- `complete` — validate all blocking deps are DONE, then set `status=done` +
  `completed_at` + optional `completed_commit`; writes TASK revision.
- `remove_dependency` — delete a task→task `dependency` edge (kind='blocks');
  raises on unknown task or missing edge; writes TASK revision + activity.
- `add_dependency` — upsert a task→task `dependency` edge with a mandatory
  `note` (ADO-202, RFC 26 §3.2): create / update note / adopt / no-op;
  refuses self-loops, cross-project blockers and edges closing a cycle.
- `dependency_warnings` — read-only предупреждения о ребре (блокер закрыт,
  задача в работе, кросс-плановое, транзитивно выводимое).

ID format:  `<PREFIX>-<NNN>` (e.g. `COD-011`, `AUTH-025`). Caller may pass
`id_prefix` when `task_id=None` (иначе он выводится из плана); the service finds the current max sequence
within the PROJECT and increments — тот же скоуп, что у ограничения
`UNIQUE (project_id, task_id)`. Format validation is COD-020's job.

Caller owns the transaction (`transactional()` from `cod_doc.infra.db`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cod_doc.domain.entities import (
    AffectedFileKind,
    EntityKind,
    Priority,
    Task,
    TaskStatus,
    TaskType,
    canonical_task_status,
    equivalent_task_statuses,
)
from cod_doc.infra.models import (
    AffectedFileModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.repositories import PlanSectionRepository, TaskRepository
from cod_doc.infra.sql_helpers import ensure_outer_transaction, priority_sql_order
from cod_doc.services import activity_service, event_bus, search_service, validation
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session


class TaskNotFoundError(LookupError):
    pass


class DependencyNotFoundError(LookupError):
    """Raised by `remove_dependency()` when the requested edge does not exist."""


class DependencyCycleError(ValueError):
    """Raised by `add_dependency()` when the new edge would close a cycle.

    ``path`` — task_id вершин от блокера до задачи по рёбрам from→to;
    новое ребро задача→блокер замыкает его. Петля — ``[task_id]``.
    """

    def __init__(self, path: list[str]) -> None:
        cycle = " → ".join([*path, path[0]])
        super().__init__(f"dependency would close a cycle: {cycle}")
        self.path = path


@dataclass(frozen=True)
class DependencyChange:
    """Результат `add_dependency()`: задача и записанная операция.

    ``op`` ∈ {'add_dependency', 'update_dependency_note', 'adopt_dependency'},
    ``None`` — ничего не записано (ребро уже такое).
    """

    task: Task
    op: str | None


class SectionNotFoundError(LookupError):
    """Raised by `move_to_section()` when the target section does not exist."""


class CrossPlanMoveError(ValueError):
    """Raised by `move_to_section()` when the target section is in another plan."""


class TaskBlockedError(RuntimeError):
    """Raised by `complete()` when blocking deps are not yet done."""


class TaskAlreadyDoneError(RuntimeError):
    """Raised by `complete()` when the task is already done (idempotency guard)."""


class DuplicateTaskIdError(ValueError):
    """Raised by `create()` when the `task_id` is already taken in the project.

    Несёт свободный номер с тем же префиксом, чтобы вызывающий мог повторить
    без угадывания. Текст — без SQL: агенту уходит он, а не сырой
    ``IntegrityError`` драйвера.
    """

    def __init__(self, task_id: str, next_free_id: str) -> None:
        super().__init__(
            f"task_id {task_id!r} already exists in project; next free: {next_free_id}"
        )
        self.task_id = task_id
        self.next_free_id = next_free_id


# Авто-ID считается как max+1 без замка: параллельный create с тем же
# префиксом может занять номер между расчётом и insert. Столько раз
# пересчитываем номер, прежде чем сдаться.
_AUTO_ID_ATTEMPTS = 3
_MIN_PREFIX_LEN = 2
_SCOPE_PREFIX_LEN = 3
_FALLBACK_PREFIX = "TSK"
_PREFIXED_TASK_ID_RE = re.compile(r"^([A-Z]{2,5})-\d+$")


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _project_slug(session: Session, project_id: int) -> str | None:
    """Resolve a project's slug for live-event routing. Returns None if unknown."""
    return session.execute(
        select(ProjectModel.slug).where(ProjectModel.row_id == project_id)
    ).scalar_one_or_none()


def _require_task(session: Session, task_id: str, *, project_id: int | None) -> TaskModel:
    """Найти задачу по ``task_id`` в скоупе проекта.

    ``project_id`` обязателен и без значения по умолчанию (ADO-200, RFC 26
    §3.2, T9). Ограничение целостности — ``UNIQUE (project_id, task_id)``,
    поэтому в общей hub-БД два проекта с одним ID роняют
    ``scalar_one_or_none()`` через ``MultipleResultsFound``. Дефолт дал бы
    call-site'у забыть скоуп молча.

    ``None`` — явный легаси-долг вызывающих, у которых ``project_id`` нет
    в сигнатуре: поиск идёт по всей БД, как раньше.
    """
    stmt = select(TaskModel).where(TaskModel.task_id == task_id)
    if project_id is not None:
        stmt = stmt.where(TaskModel.project_id == project_id)
    model = session.execute(stmt).scalar_one_or_none()
    if model is None:
        raise TaskNotFoundError(task_id)
    return model


def _next_task_id(session: Session, project_id: int, prefix: str) -> str:
    """Return the next unused `{prefix}-NNN` id within the PROJECT.

    Скоуп обязан совпадать с ограничением целостности. Оно — ``UNIQUE
    (project_id, task_id)`` (`uq_task_project_task_id`), а не по плану:
    считая максимум внутри плана, мы выдавали ``{prefix}-001`` каждый раз,
    когда префикс появлялся в новом плане, и немедленно упирались в занятый
    id из соседнего плана того же проекта (ADO-177).
    """
    stmt = select(TaskModel.task_id).where(
        TaskModel.project_id == project_id,
        TaskModel.task_id.like(f"{prefix}-%"),
    )
    max_n = 0
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for tid in session.execute(stmt).scalars():
        m = pat.match(tid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}-{max_n + 1:03d}"


def id_prefix_from_scope(scope: str) -> str:
    """Префикс ID из scope плана: первые три латинские буквы в верхнем регистре.

    ``adoption-2026-08`` → ``ADO``, ``cod-doc`` → ``COD``. Меньше двух букв —
    ``TSK``: результат обязан проходить ``validate_id_prefix`` (2-5 заглавных).
    """
    letters = [c for c in scope.upper() if c.isascii() and c.isalpha()]
    if len(letters) < _MIN_PREFIX_LEN:
        return _FALLBACK_PREFIX
    return "".join(letters[:_SCOPE_PREFIX_LEN])


def id_prefix_for_plan(session: Session, plan_id: int) -> str:
    """Префикс ID для новой задачи плана, когда вызывающий его не дал.

    Уже существующие задачи плана выигрывают у scope: план ``agent-fit``
    нумерует ``AFT-*``, а не ``AGE-*``. Берётся самый частый префикс, при
    равенстве — префикс самой поздней задачи (наибольший ``row_id``). Задач
    с разбираемым ID нет — префикс из scope.
    """
    counts: dict[str, int] = {}
    latest: dict[str, int] = {}
    rows = session.execute(
        select(TaskModel.row_id, TaskModel.task_id).where(TaskModel.plan_id == plan_id)
    )
    for row_id, tid in rows:
        m = _PREFIXED_TASK_ID_RE.match(tid)
        if m is None:
            continue
        prefix = m.group(1)
        counts[prefix] = counts.get(prefix, 0) + 1
        latest[prefix] = max(latest.get(prefix, row_id), row_id)
    if counts:
        return max(counts, key=lambda p: (counts[p], latest[p]))
    scope = session.execute(
        select(PlanModel.scope).where(PlanModel.row_id == plan_id)
    ).scalar_one_or_none()
    return id_prefix_from_scope(scope or "")


def _require_free_task_id(session: Session, project_id: int, task_id: str) -> None:
    """Явный `task_id` занят в проекте → `DuplicateTaskIdError` до insert."""
    taken = session.execute(
        select(TaskModel.row_id).where(
            TaskModel.project_id == project_id, TaskModel.task_id == task_id
        )
    ).first()
    if taken is not None:
        prefix = task_id.split("-", 1)[0]
        raise DuplicateTaskIdError(task_id, _next_task_id(session, project_id, prefix))


def _add_with_auto_id(
    session: Session,
    repo: TaskRepository,
    project_id: int,
    prefix: str,
    build: Callable[[str], Task],
) -> Task:
    """Вставить задачу с авто-ID, пересчитывая номер на конфликте.

    Номер считается max+1 без замка: между расчётом и insert его может
    занять параллельный create. Каждая попытка — в своём savepoint, конфликт
    откатывает только её. Внешняя транзакция открывается явно до savepoint,
    иначе на pysqlite RELEASE закоммитил бы insert мимо отката вызывающего
    (STO-022).
    """
    ensure_outer_transaction(session)
    attempted = ""
    for _ in range(_AUTO_ID_ATTEMPTS):
        attempted = _next_task_id(session, project_id, prefix)
        try:
            with session.begin_nested():
                return repo.add(build(attempted))
        except IntegrityError:
            continue
    raise DuplicateTaskIdError(attempted, _next_task_id(session, project_id, prefix))


def _add_task(
    session: Session,
    repo: TaskRepository,
    project_id: int,
    plan_id: int,
    task_id: str | None,
    id_prefix: str | None,
    build: Callable[[str], Task],
) -> Task:
    """Вставить задачу: явный ID — после проверки формата и занятости, иначе авто-ID.

    Без `id_prefix` префикс выводится из плана (`id_prefix_for_plan`).
    """
    if task_id is not None:
        validation.validate_task_id(task_id)
        _require_free_task_id(session, project_id, task_id)
        return repo.add(build(task_id))
    prefix = id_prefix or id_prefix_for_plan(session, plan_id)
    validation.validate_id_prefix(prefix)
    return _add_with_auto_id(session, repo, project_id, prefix, build)


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


def find_duplicate_by_title(session: Session, project_id: int, title: str) -> Task | None:
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

    If `task_id` is None, the service assigns `{prefix}-NNN` where NNN is the
    next sequence within the PROJECT — scope of ``UNIQUE (project_id,
    task_id)`` (ADO-177). Without `id_prefix` the prefix is derived from the
    plan (`id_prefix_for_plan`): the most frequent prefix among the plan's
    tasks, else from the plan scope (`adoption-2026-08` → ``ADO``). A number
    taken concurrently between computing and insert is retried up to
    ``_AUTO_ID_ATTEMPTS`` times, each attempt in its own savepoint.

    An explicit `task_id` already present in the project raises
    `DuplicateTaskIdError` with the next free id of the same prefix, before
    any insert — no raw ``IntegrityError`` reaches the caller.

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

    validation.validate_task_type(type.value)

    now = datetime.now(UTC)
    repo = TaskRepository(session)

    def _build(tid: str) -> Task:
        return Task(
            project_id=project_id,
            task_id=tid,
            plan_id=plan_id,
            section_id=section_id,
            title=title,
            # ADO-156: новая задача рождается в каноническом написании.
            # Легаси `pending` остаётся принимаемым на входе, но больше
            # никогда не записывается.
            status=TaskStatus.TODO,
            type=type,
            priority=priority,
            description=description,
            acceptance=acceptance,
            blocked_reason=blocked_reason,
            created=now,
            last_updated=now,
        )

    task = _add_task(session, repo, project_id, plan_id, task_id, id_prefix, _build)
    assert task.task_id is not None
    task_id = task.task_id
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
                select(TaskModel).where(
                    TaskModel.project_id == project_id,
                    TaskModel.task_id == blocker_task_id,
                )
            ).scalar_one_or_none()
            if blocker_model is None:
                raise ValueError(f"blocked_by references unknown task_id: {blocker_task_id!r}")
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
            raise ValueError(f"story_id references unknown story: {story_id!r}")
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
        # Статус берётся из самой записи, а не из литерала: иначе ревизия
        # рассказывает про строку, которой в `task.status` нет (ADO-156).
        diff=_task_diff("create", task_id=task_id, status=task.status.value),
        reason=reason or "create",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "task.created",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={"title": task.title, "type": task.type.value, "priority": task.priority.value},
        summary=f"Task {task_id} created",
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
    # CUR-012: keep the FTS index current without a manual reindex.
    if inserted_model is not None:
        search_service.index_task(session, inserted_model)
    return task


def update_status(
    session: Session,
    *,
    task_id: str,
    new_status: TaskStatus,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
    via_checkout: bool = False,
    strict: bool = True,
    force: bool = False,
) -> Task:
    """Set task.status directly; no dep-gate.

    For the guarded `→done` transition that validates blocking deps, use
    `complete()` instead.

    Per PCA-221 / proposal 06 §89, transition validation is **opt-in**
    (warn-mode for Phase 1). Pass ``strict=True`` to raise
    :class:`StatusTransitionError` on disallowed transitions. Default is
    permissive — disallowed transitions log via :func:`_warn_invalid_transition`
    but proceed.

    ADO-039 (Phase 2, решение владельца 2026-08-30): checkout-правило
    ``todo → in_progress`` **enforce'ится всегда** — это правило протокола,
    а не таблицы переходов, поэтому ``strict=False`` его не смягчает.
    Обход — только ``force=True`` (revert / миграции данных).

    Pass ``via_checkout=True`` when this call is the ``todo→in_progress``
    leg of a checkout flow.

    Pass ``force=True`` to skip both validation and warning (for revert
    flows that produce out-of-table transitions intentionally).

    Pass `expected_parent_revision_id` to detect concurrent writes
    (mirrors `patch_section` optimistic concurrency).
    """
    from cod_doc.services.task_status_machine import (
        StatusTransitionError,
        normalise,
        validate_transition,
    )

    # ADO-200: project_id=None — легаси-долг. Публичные сигнатуры update_status,
    # _update_text_field, update_priority, move_to_section, complete,
    # set_blocker, clear_blocker и log_progress в этой задаче не меняются,
    # скоупа проекта у них нет — поиск по всей БД, как раньше.
    model = _require_task(session, task_id, project_id=None)
    old_status = model.status
    target_status = canonical_task_status(new_status)
    # Сравнение по бакету, а не по строке: `todo → pending` — не переход, а
    # смена написания, и до ADO-156 она проходила как настоящая смена статуса
    # (ревизия, событие, перевод задачи обратно в легаси).
    if canonical_task_status(old_status) == target_status:
        t = TaskRepository(session).get_by_task_id(task_id)
        assert t is not None
        return t

    if not force:
        # ADO-039: checkout-правило — протокольное, проверяется до strict и
        # не смягчается им.
        if (normalise(old_status), normalise(new_status.value)) == (
            "todo",
            "in_progress",
        ) and not via_checkout:
            raise StatusTransitionError(
                from_status=old_status,
                to_status=new_status.value,
                reason="must go through task_checkout (proposal 06, Phase-2 enforce ADO-039)",
            )
        try:
            validate_transition(old_status, new_status.value, via_checkout=via_checkout)
        except StatusTransitionError:
            if strict:
                raise
            # Warn-mode: proceed but record the protocol violation as an
            # audit hint. The actual activity-event emission is wired up
            # by PCA-912; for now we just keep the path silent + permissive.

    # ADO-156, точка схождения всех поверхностей: CLI, веб-форма, легаси-REST,
    # MCP и авточекаут-нога `complete()` пишут статус только отсюда, поэтому
    # канонизация здесь закрывает их разом. Легаси-написание принимается на
    # входе (enum его знает), но в колонку не попадает.
    model.status = target_status
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff("status", old=old_status, new=target_status),
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
            new=target_status,
        )

    # PCA-912 (extension): persist to activity_event so CLI/programmatic
    # callers participate in the audit timeline (the MCP wrapper used to be
    # the only emit site). Errors are not swallowed.
    activity_service.emit_for_write(
        session,
        model.project_id,
        "task.status_changed",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={"old_status": old_status, "new_status": target_status, "reason": reason},
        summary=f"Task {task_id}: {old_status} → {target_status}",
    )
    search_service.index_task(session, model)

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
    expected_parent_revision_id: str | object | None,
) -> Task:
    """Shared body for update_description / update_acceptance.

    No-ops when value is unchanged. Writes a TASK revision with
    op=`<field>` carrying old/new content (length-only when very long).
    """
    model = _require_task(session, task_id, project_id=None)
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
    activity_service.emit_for_write(
        session,
        model.project_id,
        f"task.{field}_updated",
        author,
        scope_kind="task",
        scope_id=model.task_id,
        payload={"field": field, "old_len": len(old_value), "new_len": len(new_value)},
        summary=f"Task {model.task_id}: {field} updated",
    )
    search_service.index_task(session, model)
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
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
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
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
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


def update_priority(
    session: Session,
    *,
    task_id: str,
    new_priority: Priority,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
) -> Task:
    """Replace task.priority; writes a TASK revision (op=priority) + activity event.

    ADO-067: приоритет был неизменяем после создания на всех поверхностях —
    грумить бэклог (переоценить приоритет спринта) можно было только правкой
    БД в обход сервисов. No-op, когда значение не меняется.

    Revision и activity event пишутся одним атомарным вызовом
    ``activity_service.write_revision_and_emit_event`` (правило ADO-040);
    ``actor_kind`` выводится из ``author``.
    """
    model = _require_task(session, task_id, project_id=None)
    old_priority = model.priority
    if old_priority == new_priority.value:
        t = TaskRepository(session).get_by_task_id(task_id)
        assert t is not None
        return t

    model.priority = new_priority.value
    model.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff("priority", old=old_priority, new=new_priority.value),
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
        activity_kind="task.priority_changed",
        activity_scope_kind="task",
        activity_scope_id=task_id,
        activity_payload={
            "old_priority": old_priority,
            "new_priority": new_priority.value,
            "reason": reason,
        },
        activity_summary=f"Task {task_id}: priority {old_priority} → {new_priority.value}",
    )

    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def move_to_section(
    session: Session,
    *,
    task_id: str,
    new_section_id: int,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
) -> Task:
    """Move a task to another section of the SAME plan (op=section).

    Бэклог группируется секциями плана, но перекладывать задачу между ними
    было нечем: `create` принимает `section_id`, а ни одна мутация его не
    меняет. Единственным способом оставался прямой SQL в обход сервисов —
    без ревизии и без события, после чего `revision_revert` и `plan audit`
    начинают врать.

    Смена плана намеренно запрещена: `plan_id` и `section_id` — два
    независимых NOT NULL FK, и рассинхрон пары ломает и экспорт плана, и
    автонумерацию `task_id` (её скоуп — проект, а префикс берётся из scope
    плана). Перенос между планами — другая операция с другой семантикой.

    No-op, когда задача уже в целевой секции. Revision и activity event
    пишутся одним атомарным вызовом (правило ADO-040).
    """
    model = _require_task(session, task_id, project_id=None)
    old_section_id = model.section_id
    if old_section_id == new_section_id:
        t = TaskRepository(session).get_by_task_id(task_id)
        assert t is not None
        return t

    sections = PlanSectionRepository(session)
    new_section = sections.get(new_section_id)
    if new_section is None:
        raise SectionNotFoundError(f"Unknown section_id: {new_section_id}")
    if new_section.plan_id != model.plan_id:
        raise CrossPlanMoveError(
            f"Section {new_section_id} belongs to plan {new_section.plan_id}, "
            f"task {task_id} — to plan {model.plan_id}; "
            "перенос между планами не поддерживается"
        )

    old_section = sections.get(old_section_id)
    old_letter = old_section.letter if old_section else "?"
    new_letter = new_section.letter

    model.section_id = new_section_id
    model.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff(
            "section",
            old=old_section_id,
            new=new_section_id,
            old_letter=old_letter,
            new_letter=new_letter,
        ),
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
        activity_kind="task.section_changed",
        activity_scope_kind="task",
        activity_scope_id=task_id,
        activity_payload={
            "old_section_id": old_section_id,
            "new_section_id": new_section_id,
            "old_letter": old_letter,
            "new_letter": new_letter,
            "reason": reason,
        },
        activity_summary=f"Task {task_id}: section {old_letter} → {new_letter}",
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
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
) -> Task:
    """Complete a task: validate deps → done, write revision.

    ADO-038: переход в done идёт через статус-машину (ALLOWED_TRANSITIONS).
    Прямого ребра ``todo→done`` в машине нет (proposal 08: сначала начать
    работу), поэтому для задач в ``todo``/``pending`` выполняется явная
    checkout-нога ``todo→in_progress`` (update_status, via_checkout=True) —
    два легальных перехода вместо одного нелегального. Переходы
    ``cancelled→done`` и ``backlog→done`` отклоняются StatusTransitionError.

    Raises `TaskAlreadyDoneError` if the task is already done.
    Raises `TaskBlockedError` if any `blocks`-type dep is not yet done.
    Raises `StatusTransitionError` on a status the machine forbids → done.
    """
    from cod_doc.services.task_status_machine import is_terminal, validate_transition

    model = _require_task(session, task_id, project_id=None)

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
        # ADO-078: закрыто — это `done` И `cancelled`. Отменённая задача уже
        # никогда не станет `done`, поэтому сравнение только с `done` держало
        # зависимых вечно: закрыть их было нельзя в принципе.
        if dep_task is not None and not is_terminal(dep_task.status):
            blocking.append(dep_task.task_id)
    if blocking:
        raise TaskBlockedError(f"{task_id} blocked by: {', '.join(blocking)}")

    # old_status для revision-диффа — статус ДО checkout-ноги: revert
    # complete-ревизии должен вернуть задачу в исходное состояние, а не в
    # промежуточное in-progress.
    old_status = model.status
    if model.status in (TaskStatus.PENDING.value, TaskStatus.TODO.value):
        update_status(
            session,
            task_id=task_id,
            new_status=TaskStatus.IN_PROGRESS_NEW,
            author=author,
            reason="auto-checkout перед complete (ADO-038)",
            via_checkout=True,
        )
    validate_transition(model.status, TaskStatus.DONE.value)

    now = datetime.now(UTC)
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

    # PCA-912 (extension): emit activity event from the service layer so
    # CLI / programmatic callers don't bypass the audit timeline.  The MCP
    # wrapper used to do this; moving the emit here covers all entry points.
    # Errors are not swallowed.
    activity_service.emit_for_write(
        session,
        model.project_id,
        "task.completed",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={"commit_sha": commit_sha, "reason": reason},
        summary=f"Task {task_id} completed by {author}",
    )
    search_service.index_task(session, model)

    # COD-022: signal plan staleness so callers know projection may be outdated.
    plan_model = session.get(PlanModel, model.plan_id)
    if plan_model is not None:
        plan_model.last_updated = now
    session.flush()

    # OBI-001: record per-task completion metrics for /p/<slug>/metrics
    # dashboard. Idempotent — safe under re-completion.
    import logging as _log

    try:
        from cod_doc.services import metrics_service

        metrics_service.record_on_complete(session, model)
    except Exception as _exc:  # never break completion on metrics failure
        _log.getLogger("cod_doc.metrics").warning(
            "metrics_service.record_on_complete failed for %s: %s",
            task_id,
            _exc,
        )

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


def parse_since(value: str) -> datetime:
    """Parse an ISO-8601 ``--since`` value into an aware UTC datetime.

    A bare date (``2026-09-16``) is treated as midnight UTC; a naive
    datetime is assumed to be UTC; an aware one is converted to UTC.
    Нормализация обязательна, потому что SQLite хранит ``DateTime`` строкой,
    и строковое сравнение корректно, только когда обе стороны записаны в
    одной зоне, — зоной проекта выбрана UTC. Мусор на входе даёт
    ``ValueError`` с исходной строкой и подсказкой формата.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"cannot parse datetime {value!r}; expected ISO-8601, "
            "e.g. '2026-09-16' or '2026-09-16T12:00:00Z'"
        ) from None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _resolve_scope_filters(
    session: Session,
    project_id: int,
    *,
    plan_scope: str | None,
    section_letter: str | None,
) -> tuple[int | None, int | None]:
    """Resolve ``plan_scope`` / ``section_letter`` to row ids (RFC 27 F7).

    Общий для ``list_for_project`` и ``count_for_project``, чтобы total и
    items считались по одному множеству. ``section_letter`` осмысленна
    только внутри плана (буква уникальна в рамках plan_id), поэтому без
    ``plan_scope`` это ошибка.
    """
    if section_letter is not None and plan_scope is None:
        raise ValueError(
            "section_letter requires plan_scope: буква секции уникальна только внутри плана"
        )
    plan_id: int | None = None
    section_id: int | None = None
    if plan_scope is not None:
        plan_id = session.execute(
            select(PlanModel.row_id).where(
                PlanModel.project_id == project_id,
                PlanModel.scope == plan_scope,
            )
        ).scalar_one_or_none()
        if plan_id is None:
            raise ValueError(f"Plan '{plan_scope}' not found in project")
        if section_letter is not None:
            section_id = session.execute(
                select(PlanSectionModel.row_id).where(
                    PlanSectionModel.plan_id == plan_id,
                    PlanSectionModel.letter == section_letter,
                )
            ).scalar_one_or_none()
            if section_id is None:
                raise ValueError(f"Section '{section_letter}' not found in plan '{plan_scope}'")
    return plan_id, section_id


def list_for_project(
    session: Session,
    project_id: int,
    *,
    status: TaskStatus | None = None,
    priority: Priority | None = None,
    limit: int | None = None,
    offset: int = 0,
    plan_scope: str | None = None,
    section_letter: str | None = None,
    type: TaskType | None = None,
    completed_since: datetime | None = None,
    updated_since: datetime | None = None,
    has_commit: bool | None = None,
) -> list[Task]:
    """List tasks for a project with filters and pagination (RFC 27 F7).

    Filters: ``status`` (бакет эквивалентности), ``priority``, ``type``,
    ``plan_scope`` (scope плана внутри проекта; неизвестный scope —
    ``ValueError``), ``section_letter`` (буква секции; требует
    ``plan_scope``, неизвестная буква — ``ValueError``),
    ``completed_since`` (``completed_at >= значения``, NULL не проходит),
    ``updated_since`` (``last_updated >= значения``) и ``has_commit``
    (True — непустой ``completed_commit``, False — NULL или пустая строка).
    Даты нормализуются к UTC заранее — см. :func:`parse_since`.

    A `limit=None` returns all matching tasks (legacy behaviour). Callers
    fronted by MCP/REST should pass a finite limit to bound payload size.
    """
    plan_id, section_id = _resolve_scope_filters(
        session, project_id, plan_scope=plan_scope, section_letter=section_letter
    )
    return TaskRepository(session).list_for_project(
        project_id,
        status=status,
        priority=priority,
        type=type,
        plan_id=plan_id,
        section_id=section_id,
        completed_since=completed_since,
        updated_since=updated_since,
        has_commit=has_commit,
        limit=limit,
        offset=offset,
    )


def count_for_project(
    session: Session,
    project_id: int,
    *,
    status: TaskStatus | None = None,
    priority: Priority | None = None,
    plan_scope: str | None = None,
    section_letter: str | None = None,
    type: TaskType | None = None,
    completed_since: datetime | None = None,
    updated_since: datetime | None = None,
    has_commit: bool | None = None,
) -> int:
    """Return the count of matching tasks (paired with list_for_project).

    Принимает те же фильтры (RFC 27 F7), что и :func:`list_for_project`, и
    делегирует в репозиторий — total и items считаются по одному множеству.
    """
    plan_id, section_id = _resolve_scope_filters(
        session, project_id, plan_scope=plan_scope, section_letter=section_letter
    )
    return TaskRepository(session).count_for_project(
        project_id,
        status=status,
        priority=priority,
        type=type,
        plan_id=plan_id,
        section_id=section_id,
        completed_since=completed_since,
        updated_since=updated_since,
        has_commit=has_commit,
    )


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

    model = _require_task(session, task_id, project_id=None)
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
    activity_service.emit_for_write(
        session,
        model.project_id,
        "task.blocker_added",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={"reason": reason},
        summary=f"Task {task_id}: blocker added",
    )
    search_service.index_task(session, model)
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
    model = _require_task(session, task_id, project_id=None)
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
    activity_service.emit_for_write(
        session,
        model.project_id,
        "task.blocker_cleared",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={"old_reason": old_reason},
        summary=f"Task {task_id}: blocker cleared",
    )
    search_service.index_task(session, model)
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def remove_dependency(
    session: Session,
    *,
    project_id: int,
    task_id: str,
    blocker_task_id: str,
    author: str,
    reason: str | None = None,
) -> Task:
    """Remove a task→task ``dependency`` edge (kind='blocks', task → blocker).

    Not idempotent: raises :class:`TaskNotFoundError` if either task is
    unknown and :class:`DependencyNotFoundError` if the edge does not
    exist. Writes a TASK revision with op=remove_dependency and emits a
    ``task.dependency_removed`` activity event (proposal 09 / PCA-912
    extension — the service emits directly so CLI/programmatic callers
    participate in the audit timeline).

    Обе задачи ищутся в проекте ``project_id`` (ADO-200): задача другого
    проекта с тем же ID — :class:`TaskNotFoundError`, а не чужое ребро.
    """
    model = _require_task(session, task_id, project_id=project_id)
    blocker_model = _require_task(session, blocker_task_id, project_id=project_id)

    edge = session.execute(
        select(DependencyModel).where(
            DependencyModel.from_task_id == model.row_id,
            DependencyModel.to_task_id == blocker_model.row_id,
            DependencyModel.kind == "blocks",
        )
    ).scalar_one_or_none()
    if edge is None:
        raise DependencyNotFoundError(
            f"no 'blocks' dependency edge: {task_id!r} is not blocked by {blocker_task_id!r}"
        )
    session.delete(edge)
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff("remove_dependency", blocker=blocker_task_id),
        reason=reason or "remove_dependency",
    )
    activity_service.emit_for_write(
        session,
        model.project_id,
        "task.dependency_removed",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={"blocker_task_id": blocker_task_id, "reason": reason},
        summary=f"Task {task_id}: dependency on {blocker_task_id} removed",
    )

    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def _reaches(
    session: Session, *, start_row_id: int, target_row_id: int, kind: str
) -> list[int] | None:
    """Путь row_id от ``start`` до ``target`` по рёбрам ``kind`` (from→to) или None.

    Итеративный DFS с восстановлением пути через карту родителей; соседей
    вершины читает одним SELECT при первом посещении.
    """
    parent: dict[int, int | None] = {start_row_id: None}
    stack = [start_row_id]
    while stack:
        node = stack.pop()
        if node == target_row_id:
            path = [node]
            while (prev := parent[path[-1]]) is not None:
                path.append(prev)
            return path[::-1]
        neighbours = session.execute(
            select(DependencyModel.to_task_id).where(
                DependencyModel.from_task_id == node, DependencyModel.kind == kind
            )
        ).scalars()
        for nxt in neighbours:
            if nxt not in parent:
                parent[nxt] = node
                stack.append(nxt)
    return None


def _upsert_edge(
    session: Session,
    model: TaskModel,
    blocker_model: TaskModel,
    kind: str,
    note: str,
    adopt: bool,
) -> str | None:
    """Применить таблицу upsert из RFC 26 §3.2 и вернуть op (None — ничего)."""
    edge = session.execute(
        select(DependencyModel).where(
            DependencyModel.from_task_id == model.row_id,
            DependencyModel.to_task_id == blocker_model.row_id,
            DependencyModel.kind == kind,
        )
    ).scalar_one_or_none()
    if edge is None:
        path = _reaches(
            session,
            start_row_id=blocker_model.row_id,
            target_row_id=model.row_id,
            kind=kind,
        )
        if path is not None:
            rows = session.execute(
                select(TaskModel.row_id, TaskModel.task_id).where(TaskModel.row_id.in_(path))
            ).all()
            ids = {row_id: tid for row_id, tid in rows}
            raise DependencyCycleError([ids[row_id] for row_id in path])
        session.add(
            DependencyModel(
                from_task_id=model.row_id, to_task_id=blocker_model.row_id, kind=kind, note=note
            )
        )
        return "add_dependency"
    if edge.note != note:
        edge.note = note
        return "update_dependency_note"
    return "adopt_dependency" if adopt else None


def add_dependency(
    session: Session,
    *,
    project_id: int,
    task_id: str,
    blocker_task_id: str,
    note: str,
    author: str,
    reason: str | None = None,
    kind: str = "blocks",
    adopt: bool = False,
) -> DependencyChange:
    """Поставить ребро ``task_id → blocker_task_id`` с мотивацией (ADO-202).

    Upsert по таблице RFC 26 §3.2: ребра нет — создать (op=add_dependency);
    есть и ``note`` отличается — обновить (op=update_dependency_note); есть и
    совпадает — молчать (op=None: ни ревизии, ни события, ``last_updated``
    не трогается); при ``adopt=True`` совпадение пишет ревизию без изменения
    данных (op=adopt_dependency) — легализация внесистемной правки.

    ``note`` обязателен и при ``adopt``: легализуется только ребро, у которого
    мотивация уже есть. Выдумывать её задним числом для немых рёбер хуже, чем
    оставить их пустыми — их предъявляет рутина ``graph_health``.

    Отказы: петля — :class:`DependencyCycleError` до CHECK'а БД; блокер или
    задача вне ``project_id`` — :class:`TaskNotFoundError`; ребро, замыкающее
    цикл, — :class:`DependencyCycleError` с путём от блокера до задачи.

    ``event_bus.queue_emit`` не вызывается — симметрично `remove_dependency`.
    При этом ``ready_tasks`` — SQL-вьюха (миграция 20260919_0035): новое
    ребро мгновенно вынимает задачу из plan_ready, task_next_ready,
    agent_pick и очереди куратора, и события об этом нет.
    """
    if not isinstance(note, str) or not note.strip():
        raise ValueError("note is required: a dependency edge must carry its motivation")
    if not kind:
        raise ValueError("kind must be a non-empty string")
    if task_id == blocker_task_id:
        raise DependencyCycleError([task_id])
    note = note.strip()
    model = _require_task(session, task_id, project_id=project_id)
    blocker_model = _require_task(session, blocker_task_id, project_id=project_id)

    previous_note = session.execute(
        select(DependencyModel.note).where(
            DependencyModel.from_task_id == model.row_id,
            DependencyModel.to_task_id == blocker_model.row_id,
            DependencyModel.kind == kind,
        )
    ).scalar_one_or_none()
    op = _upsert_edge(session, model, blocker_model, kind, note, adopt)
    if op is None:
        t = TaskRepository(session).get(model.row_id)
        assert t is not None
        return DependencyChange(task=t, op=None)

    model.last_updated = datetime.now(UTC)
    session.flush()

    extra: dict[str, object] = (
        {"previous_note": previous_note} if op == "update_dependency_note" else {}
    )
    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.TASK,
        entity_id=model.row_id,
        author=author,
        diff=_task_diff(op, blocker=blocker_task_id, kind=kind, note=note, **extra),
        reason=reason or op,
    )
    activity_service.emit_for_write(
        session,
        model.project_id,
        "task.dependency_added" if op == "add_dependency" else "task.dependency_updated",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={
            "blocker_task_id": blocker_task_id,
            "kind": kind,
            "note": note,
            "op": op,
            "reason": reason,
        },
        summary=f"Task {task_id}: dependency on {blocker_task_id} ({op})",
    )

    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return DependencyChange(task=t, op=op)


def dependency_warnings(
    session: Session,
    *,
    project_id: int,
    task_id: str,
    blocker_task_id: str,
    kind: str = "blocks",
) -> list[dict[str, str]]:
    """Предупреждения о ребре ``task_id → blocker_task_id``; только чтение.

    Отдельная read-функция, а не поле результата `add_dependency`: иначе
    сканер паритета поверхностей принял бы её за мутацию. Коды по порядку:
    ``blocker_closed`` (блокер done/cancelled), ``task_in_progress`` (задача
    in_progress или на checkout), ``cross_plan`` (разные планы),
    ``transitive`` (блокер достижим от задачи путём длины ≥ 2; прямое ребро
    из обхода исключено, так что ответ не зависит от того, вставлено ли оно).
    """
    model = _require_task(session, task_id, project_id=project_id)
    blocker_model = _require_task(session, blocker_task_id, project_id=project_id)
    warnings: list[dict[str, str]] = []
    if canonical_task_status(blocker_model.status) in {TaskStatus.DONE, TaskStatus.CANCELLED}:
        warnings.append(
            {
                "code": "blocker_closed",
                "message": f"blocker {blocker_task_id} is already {blocker_model.status}",
            }
        )
    if (
        canonical_task_status(model.status) == TaskStatus.IN_PROGRESS_NEW
        or model.checked_out_by is not None
    ):
        warnings.append(
            {
                "code": "task_in_progress",
                "message": f"task {task_id} is already in progress",
            }
        )
    if model.plan_id != blocker_model.plan_id:
        warnings.append(
            {
                "code": "cross_plan",
                "message": f"{task_id} and {blocker_task_id} belong to different plans",
            }
        )
    first_hops = (
        session.execute(
            select(DependencyModel.to_task_id).where(
                DependencyModel.from_task_id == model.row_id,
                DependencyModel.kind == kind,
                DependencyModel.to_task_id != blocker_model.row_id,
            )
        )
        .scalars()
        .all()
    )
    if any(
        _reaches(session, start_row_id=hop, target_row_id=blocker_model.row_id, kind=kind)
        for hop in first_hops
    ):
        warnings.append(
            {
                "code": "transitive",
                "message": f"{blocker_task_id} is already reachable from {task_id} transitively",
            }
        )
    return warnings


def list_blocked(
    session: Session,
    project_id: int,
) -> list[Task]:
    """Return tasks that have an external blocker set (blocked_reason IS NOT NULL).

    Закрытые задачи исключены — у завершённой или отменённой задачи прежний
    блокер это исторический шум (ADO-078: `cancelled` закрыт наравне с `done`).
    """
    from cod_doc.services.task_status_machine import TERMINAL_STATUSES

    stmt = (
        select(TaskModel)
        .where(
            TaskModel.project_id == project_id,
            TaskModel.blocked_reason.is_not(None),
            TaskModel.status.notin_(TERMINAL_STATUSES),
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
            # Бакет целиком: `in-progress` и `in_progress` — один статус, и
            # после бэкфилла ADO-156 в колонке лежит только каноническое
            # написание, а в ещё не мигрированной БД — только легаси.
            TaskModel.status.in_(equivalent_task_statuses(TaskStatus.IN_PROGRESS_NEW)),
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

    model = _require_task(session, task_id, project_id=None)
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
    activity_service.emit_for_write(
        session,
        model.project_id,
        "task.progress_logged",
        author,
        scope_kind="task",
        scope_id=task_id,
        payload={"message": message},
        summary=f"Task {task_id}: progress logged",
    )
    t = TaskRepository(session).get(model.row_id)
    assert t is not None
    return t


def summarize_for_project(session: Session, project_id: int) -> dict:  # type: ignore[type-arg]
    """Aggregate counts by status and priority — cheap overview for big projects.

    Returns: {
      "total": int,
      "by_status": {"todo": N, "in_progress": N, "done": N, ...},
      "by_priority": {"critical": N, "high": N, "medium": N, "low": N},
    }

    Ключи ``by_status`` — ХРАНИМЫЕ написания, как они лежат в колонке, без
    нормализации: сумма обязана сходиться с ``total``, а свести бакеты здесь
    значило бы решить за вызывающего, какой из них показывать. После
    бэкфилла ADO-156 (миграция 0037) это канонические написания; БД, ещё не
    поднятая на 0036, отдаст `pending` / `in-progress`. Кому нужен бакет, а
    не строка, — складывает ключи через
    ``domain.entities.equivalent_task_statuses`` (так делает
    ``api/web/pages/index.py``).
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
