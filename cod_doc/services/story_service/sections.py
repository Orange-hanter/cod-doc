"""Секции историй — реестр продуктовых модулей и привязка истории к секции.

ADO-143. До этого секция выводилась из прозы нарратива и на нелатинском
корпусе не выводилась никогда. Здесь она становится хранимой сущностью с
человеческим названием и явным порядком.

Caller owns the transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import EntityKind, StorySection
from cod_doc.infra.repositories import StorySectionRepository
from cod_doc.services import activity_service, validation
from cod_doc.services import revision_service as rev

from ._internals import _diff, _require_story
from ._types import SectionAlreadyExistsError, SectionNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Потолок заголовка секции. Не безопасность, а вёрстка: заголовок —
#: подпись группы в списке историй и в чипе фильтра.
_MAX_SECTION_TITLE = 256


def create_section(
    session: Session,
    *,
    project_id: int,
    key: str,
    title: str,
    position: int | None = None,
    author: str,
    reason: str | None = None,
) -> StorySection:
    """Завести секцию. ``position`` по умолчанию — в конец списка.

    ADO-160: ``title`` и ``position`` проверяются здесь, а не отдельным
    валидатором из ``services/validation/structural.py``. Инъекционный риск
    несёт ``key`` — он уходит в сегмент URL и в ``querySelector``, и у него
    свой валидатор с кодом ``US-002``. ``title`` только отображается и
    экранируется Jinja, так что ему нужна не защита, а вменяемость: без
    этих двух проверок первый же ``story_section_create`` с пустым title
    рисует безымянную группу, неотличимую от «No section».
    """
    validation.validate_story_section_key(key)
    if not title.strip():
        raise ValueError("Story section title must not be empty")
    if len(title) > _MAX_SECTION_TITLE:
        raise ValueError(f"Story section title must be at most {_MAX_SECTION_TITLE} characters")
    if position is not None and position < 0:
        raise ValueError("Story section position must not be negative")
    repo = StorySectionRepository(session)
    if repo.get_by_key(project_id, key) is not None:
        raise SectionAlreadyExistsError(key)

    section = repo.add(
        StorySection(
            project_id=project_id,
            key=key,
            title=title,
            position=position if position is not None else repo.next_position(project_id),
        )
    )
    session.flush()
    assert section.row_id is not None

    # Адрес ревизии — пара (entity_kind, entity_id). У story_section своя
    # нумерация row_id, поэтому под STORY её писать нельзя: секция row_id=1
    # оказалась бы в истории истории row_id=1.
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.STORY_SECTION,
        entity_id=section.row_id,
        author=author,
        diff=_diff("create_section", key=key, title=title, position=section.position),
        reason=reason or "create_section",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "story.section_created",
        author,
        scope_kind=EntityKind.STORY_SECTION.value,
        scope_id=key,
        payload={"key": key, "title": title, "position": section.position},
        summary=f"Story section {key} created",
    )
    return section


def list_sections(session: Session, project_id: int) -> list[StorySection]:
    return StorySectionRepository(session).list_for_project(project_id)


def get_section(session: Session, project_id: int, key: str) -> StorySection | None:
    return StorySectionRepository(session).get_by_key(project_id, key)


def sections_by_id(session: Session, project_id: int) -> dict[int, StorySection]:
    """``row_id → секция`` для проекта.

    ADO-159. Этот резолв разъехался по пяти местам — дважды в
    ``story_tools``, в ``cli/story/cmd_show``, в веб-странице историй и в
    ``crud.create``. Каждая копия писала свой вариант: где-то ``if row_id is
    not None``, где-то без; где-то словарь, где-то линейный поиск с ``break``.
    Один вход дешевле, чем следить за пятью.
    """
    return {
        sec.row_id: sec
        for sec in StorySectionRepository(session).list_for_project(project_id)
        if sec.row_id is not None
    }


def section_keys(session: Session, project_id: int) -> dict[int, str]:
    """``row_id → ключ секции``: то, что нужно всем поверхностям.

    Ключ, а не ``row_id``, — единственная форма секции, пригодная для
    журнала: ``row_id`` ничего не значит для читателя, а
    ``ON DELETE SET NULL`` гарантирует, что ссылка однажды повиснет.
    """
    return {row_id: sec.key for row_id, sec in sections_by_id(session, project_id).items()}


def assign_section(
    session: Session,
    *,
    story_id: str,
    key: str | None,
    author: str,
    reason: str | None = None,
) -> StorySection | None:
    """Привязать историю к секции; ``key=None`` — снять привязку.

    Возвращает секцию, к которой история привязана после вызова (или ``None``).
    Идемпотентно: повторная привязка к той же секции не пишет ни ревизию, ни
    событие — как ``update_status`` и ``set_criterion_met``.
    """
    model = _require_story(session, story_id)
    repo = StorySectionRepository(session)

    new_section: StorySection | None = None
    if key is not None:
        new_section = repo.get_by_key(model.project_id, key)
        if new_section is None:
            raise SectionNotFoundError(key)

    new_id = new_section.row_id if new_section is not None else None
    old_id = model.section_id
    if old_id == new_id:
        return new_section

    old_key: str | None = None
    if old_id is not None:
        previous = repo.get(old_id)
        old_key = previous.key if previous is not None else None

    model.section_id = new_id
    model.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=model.project_id,
        entity_kind=EntityKind.STORY,
        entity_id=model.row_id,
        author=author,
        diff=_diff("section", old=old_key, new=key),
        reason=reason,
    )
    activity_service.emit_for_write(
        session,
        model.project_id,
        "story.section_changed",
        author,
        scope_kind="story",
        scope_id=story_id,
        payload={"old_section": old_key, "new_section": key},
        summary=f"Story {story_id}: section {old_key} → {key}",
    )
    return new_section
