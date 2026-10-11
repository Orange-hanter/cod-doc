"""Story, к которой привязана задача, — один запрос на весь сервисный слой.

Этот резолв жил тремя копиями — в ``serializers.task_to_dict``, в карточке
``agent_service`` и в ``heartbeat_service`` — и проектный фильтр STO-023
пришлось вписывать в каждую. Модуль лёгкий нарочно: только SQLAlchemy и
ORM-модели, без ``story_service``, чтобы его могли звать сериализаторы,
которые импортируются почти отовсюду, и не замкнуть цикл импортов.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import StoryLinkModel, UserStoryModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def story_id_for_task(session: Session, *, project_id: int, task_id: str) -> str | None:
    """``story_id`` истории проекта со ссылкой ``story_link`` на ``task_id``, или None.

    Ссылок может быть несколько — берётся первая строка, как и раньше во всех
    трёх копиях (``LIMIT 1`` без явного порядка).
    """
    return session.execute(
        select(UserStoryModel.story_id)
        .join(StoryLinkModel, StoryLinkModel.story_id == UserStoryModel.row_id)
        .where(
            UserStoryModel.project_id == project_id,
            StoryLinkModel.to_kind == "task",
            StoryLinkModel.to_ref == task_id,
        )
        .limit(1)
    ).scalar_one_or_none()
