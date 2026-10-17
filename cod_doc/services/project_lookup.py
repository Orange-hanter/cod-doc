"""Слаг проекта → ``project.row_id`` для поверхностей ниже MCP.

Резолв жил в ``mcp/tools/_db.py``, и встроенный агент (``cod_doc/agent``)
тянул его оттуда — импорт одной поверхности из другой. Здесь остаётся только
чтение по явному слагу; workspace-default — забота MCP, и ``_db`` делегирует
сюда уже разрешённое имя.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.infra.repositories import ProjectRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ProjectNotFoundError(LookupError, ValueError):
    """Проекта со слагом нет в БД.

    Сосед ``PlanNotFoundError``/``StoryNotFoundError`` — тоже ``LookupError``;
    ``ValueError`` в базах оставлен, потому что раньше здесь падал голый
    ``ValueError`` и места перехвата на поверхностях ловят именно его.
    """


def find_project_id(session: Session, slug: str) -> int | None:
    """``row_id`` проекта по слагу или ``None``, если его нет в БД.

    Единственное место, где поверхности читают проект по слагу (SVC-008): CLI,
    web и MCP различаются только тем, во что превращают отсутствие — exit 1,
    HTTP 404 или страница «не инициализирован».
    """
    proj = ProjectRepository(session).get_by_slug(slug)
    if proj is None or proj.row_id is None:
        return None
    return proj.row_id


def require_project_id(session: Session, slug: str) -> int:
    """``row_id`` проекта по слагу; нет в БД — ``ProjectNotFoundError`` с подсказкой."""
    project_id = find_project_id(session, slug)
    if project_id is None:
        raise ProjectNotFoundError(f"Project '{slug}' not in DB — run 'cod-doc project add' first.")
    return project_id
