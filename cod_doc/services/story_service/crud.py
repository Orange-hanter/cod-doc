"""Story CRUD + status update + listing reads."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cod_doc.domain.entities import (
    EntityKind,
    Priority,
    StoryAcceptance,
    StoryLink,
    StoryLinkKind,
    StoryRelation,
    Task,
    UserStory,
    UserStoryStatus,
)
from cod_doc.infra.models import (
    StoryAcceptanceModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.repositories import (
    StoryAcceptanceRepository,
    StoryLinkRepository,
    StorySectionRepository,
    TaskRepository,
    UserStoryRepository,
)
from cod_doc.infra.sql_helpers import is_unique_violation
from cod_doc.services import activity_service, search_service, validation
from cod_doc.services import revision_service as rev
from cod_doc.services.sync_service import leases

from ._internals import _diff, _require_story
from ._types import SectionNotFoundError, StoryAlreadyExistsError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def create(
    session: Session,
    *,
    project_id: int,
    story_id: str,
    persona: str,
    narrative: str,
    priority: Priority,
    author: str,
    status: UserStoryStatus = UserStoryStatus.DRAFT,
    acceptance: list[str] | None = None,
    section_key: str | None = None,
    reason: str | None = None,
) -> UserStory:
    """Persist a story (+ optional acceptance criteria) and write its initial revision.

    ADO-159: секция принимается КЛЮЧОМ, а не ``row_id``. Резолв был у
    каждого вызывающего свой, а в журнал уезжало целое число — при
    том что всё остальное в том же diff человекочитаемо. ``row_id``
    теряет смысл при переименовании секции, а ``ON DELETE SET NULL``
    гарантирует, что ссылка однажды повиснет; `revision_revert` эти
    diff'ы проигрывает.
    """
    if not leases.is_provisional(story_id):  # next_story_id без аренды (CLO-030)
        validation.validate_story_id(story_id)
    if (
        session.execute(
            select(UserStoryModel.row_id).where(
                UserStoryModel.project_id == project_id, UserStoryModel.story_id == story_id
            )
        ).scalar_one_or_none()
        is not None
    ):
        raise StoryAlreadyExistsError(story_id)

    section_id: int | None = None
    if section_key is not None:
        # Пустой ключ секцией не бывает (validate_story_section_key) — тот же
        # промах, а не ошибка аргумента из репозитория.
        section = (
            StorySectionRepository(session).get_by_key(project_id, section_key)
            if section_key
            else None
        )
        if section is None:
            raise SectionNotFoundError(section_key)
        section_id = section.row_id

    now = datetime.now(UTC)
    try:
        story = UserStoryRepository(session).add(
            UserStory(
                project_id=project_id,
                story_id=story_id,
                persona=persona,
                narrative=narrative,
                status=status,
                priority=priority,
                created=now,
                last_updated=now,
                section_id=section_id,
            )
        )
    except IntegrityError as exc:
        # Проверка выше и INSERT — не одна операция: параллельный писатель
        # успевает занять тот же story_id, и тогда срабатывает UNIQUE из
        # 0047. Это тот же отказ, что и у проверки. Savepoint не берём
        # (STO-022): транзакцию откатывает вызывающий. Прочие нарушения
        # целостности не маскируем.
        if not _is_story_id_conflict(exc):
            raise
        raise StoryAlreadyExistsError(story_id) from exc
    assert story.row_id is not None

    if acceptance:
        for i, criterion in enumerate(acceptance):
            session.add(
                StoryAcceptanceModel(
                    story_id=story.row_id,
                    position=i,
                    criterion=criterion,
                    met=False,
                )
            )
        session.flush()

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.STORY,
        entity_id=story.row_id,
        author=author,
        diff=_diff(
            "create",
            story_id=story_id,
            status=status.value,
            acceptance_count=len(acceptance or []),
            section=section_key,
        ),
        reason=reason or "create",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "story.created",
        author,
        scope_kind="story",
        scope_id=story_id,
        payload={
            "status": status.value,
            "acceptance_count": len(acceptance or []),
            "priority": priority.value,
            "section": section_key,
        },
        summary=f"Story {story_id} created",
    )
    # CUR-012: keep the FTS index current without a manual reindex.
    story_model = session.get(UserStoryModel, story.row_id)
    if story_model is not None:
        search_service.index_story(session, story_model)
    return story


def _is_story_id_conflict(exc: IntegrityError) -> bool:
    """Нарушен именно UNIQUE(project_id, story_id), а не внешний ключ или CHECK."""
    return is_unique_violation(
        exc,
        constraint="uq_user_story_project_story_id",
        table="user_story",
        columns=("project_id", "story_id"),
    )


def get(session: Session, story_id: str, *, project_id: int) -> UserStory | None:
    """История проекта; закреплённый провизорный ``US-~xxxx`` — по алиасу (CLO-030)."""
    repo = UserStoryRepository(session)
    found = repo.get_by_story_id(story_id, project_id=project_id)
    if found is None:
        current = leases.current_id(session, project_id, "user_story", story_id)
        if current is not None:
            found = repo.get_by_story_id(current, project_id=project_id)
    return found


def list_for_project(session: Session, project_id: int) -> list[UserStory]:
    return UserStoryRepository(session).list_for_project(project_id)


def next_story_id(session: Session, project_id: int, prefix: str = "US") -> str:
    """Auto-numbered story_id (e.g. ``US-007``) for a project + prefix.

    Used by AI-driven story generation flows where the LLM proposes drafts
    and the route persists them with sequential ids.

    CLO-030: с настроенным remote — номер из аренды или провизорный ``US-~xxxx``
    с ``SYNC-107`` (номер выдаётся под создание, остаток тратится).
    """
    if leases.enabled(session, project_id):
        return leases.lease_number(session, project_id, prefix, kind="user_story")
    # ``%`` и ``_`` в префиксе — метасимволы LIKE; экранируем, как regex ниже.
    like_prefix = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = session.execute(
        select(UserStoryModel.story_id).where(
            UserStoryModel.project_id == project_id,
            UserStoryModel.story_id.like(f"{like_prefix}-%", escape="\\"),
        )
    ).scalars()
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    max_n = 0
    for sid in rows:
        m = pat.match(sid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}-{max_n + 1:03d}"


def list_acceptance(session: Session, story_id: str, *, project_id: int) -> list[StoryAcceptance]:
    model = _require_story(session, story_id, project_id=project_id)
    return StoryAcceptanceRepository(session).list_for_story(model.row_id)


def list_links(session: Session, story_id: str, *, project_id: int) -> list[StoryLink]:
    model = _require_story(session, story_id, project_id=project_id)
    return StoryLinkRepository(session).list_for_story(model.row_id)


def list_tasks(session: Session, story_id: str, *, project_id: int) -> list[Task]:
    """Tasks linked to the story via `implemented_by` relation."""
    model = _require_story(session, story_id, project_id=project_id)
    stmt = (
        select(TaskModel)
        .join(StoryLinkModel, StoryLinkModel.to_ref == TaskModel.task_id)
        .where(
            StoryLinkModel.story_id == model.row_id,
            StoryLinkModel.to_kind == StoryLinkKind.TASK.value,
            StoryLinkModel.relation == StoryRelation.IMPLEMENTED_BY.value,
            TaskModel.project_id == model.project_id,
        )
        .order_by(TaskModel.task_id)
    )
    repo = TaskRepository(session)
    return [repo.to_domain(m) for m in session.execute(stmt).scalars()]


def update_status(
    session: Session,
    *,
    project_id: int,
    story_id: str,
    new_status: UserStoryStatus,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
) -> UserStory:
    model = _require_story(session, story_id, project_id=project_id)
    old_status = model.status
    if old_status == new_status.value:
        s = UserStoryRepository(session).get_by_story_id(story_id, project_id=project_id)
        assert s is not None
        return s

    model.status = new_status.value
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.STORY,
        entity_id=model.row_id,
        author=author,
        diff=_diff("status", old=old_status, new=new_status.value),
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
    )
    activity_service.emit_for_write(
        session,
        model.project_id,
        "story.status_changed",
        author,
        scope_kind="story",
        scope_id=story_id,
        payload={"old_status": old_status, "new_status": new_status.value, "reason": reason},
        summary=f"Story {story_id}: {old_status} → {new_status.value}",
    )
    search_service.index_story(session, model)
    s = UserStoryRepository(session).get(model.row_id)
    assert s is not None
    return s
