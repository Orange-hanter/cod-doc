"""``project_id`` для тестов с одним проектом в БД (STO-030).

Мутации задач требуют ``project_id``: ``task_id`` уникален в проекте, а не
во всей БД. Однопроектным фикстурам скоуп неинтересен, но передавать его
обязаны — хелпер отдаёт id единственного проекта и падает, если проектов
больше одного: там тест должен назвать проект сам.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import ProjectModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def sole_project_id(session: Session) -> int:
    ids = list(session.execute(select(ProjectModel.row_id)).scalars())
    assert len(ids) == 1, f"ожидался один проект в БД, найдено {len(ids)}: назови проект явно"
    return ids[0]
