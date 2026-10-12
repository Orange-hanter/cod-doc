"""Скоуп проекта для тестов с одним проектом в БД (AFT-023).

С AFT-023 ``project_id`` обязателен у мутаций задач и у чтений по ``task_id`` /
``plan_scope`` / истории ревизий. Тестам, где в БД ровно один проект и он не
под рукой как переменная, нужен его ``row_id``. ``scalar_one`` — нарочно: если
проектов окажется больше одного, тест упадёт, а не выберет «какой-нибудь».
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import ProjectModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def only_project_id(session: Session) -> int:
    """``row_id`` единственного проекта БД; два и больше — ошибка."""
    return int(session.execute(select(ProjectModel.row_id)).scalar_one())
