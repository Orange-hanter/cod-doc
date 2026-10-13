"""Скоуп проекта для тестов с одним проектом в БД (AFT-023).

С AFT-023 ``project_id`` обязателен у мутаций задач и у чтений по ``task_id`` /
``plan_scope`` / истории ревизий. Тестам, где в БД ровно один проект и он не
под рукой как переменная, нужен его ``row_id``. ``scalar_one`` — нарочно: если
проектов окажется больше одного, тест упадёт, а не выберет «какой-нибудь».
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import DocumentModel, ProjectModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def only_project_id(session: Session) -> int:
    """``row_id`` единственного проекта БД; ноль или два и больше — ошибка
    (``scalar_one``), а не молча первый попавшийся."""
    return int(session.execute(select(ProjectModel.row_id)).scalar_one())


def doc_project_id(session: Session, doc_id: int) -> int:
    """``project_id`` документа ``doc_id`` — скоуп для его ревизий и секций."""
    return int(
        session.execute(
            select(DocumentModel.project_id).where(DocumentModel.row_id == doc_id)
        ).scalar_one()
    )
