"""REL-012: запись связи из панели «Связи».

- ``POST /p/{slug}/relations`` — ``relate``; назад на страницу сущности (``back``).
  Ошибка ``relate`` — тоже назад, с ``?rel_error=…`` и введёнными полями: панель
  показывает сообщение у формы и не теряет ввод (как ``?error=`` у вопросов).

Роль writer+ проверяет ``web_access`` по методу и пути до маршрута, автор —
владелец OIDC-сессии, иначе ``human:web`` (как у вопросов).
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project_db
from cod_doc.api.web.pages.questions import _author
from cod_doc.services import relation_service

router = APIRouter()


@router.post("/p/{slug}/relations")
def relation_add(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    from_ref: Annotated[str, Form()],
    type: Annotated[str, Form()],
    to_ref: Annotated[str, Form()],
    back: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    session, project_id = db
    # Возврат только на страницы этого проекта: иначе форма — открытый редирект.
    if not back.startswith(f"/p/{slug}/") or back.startswith("//"):
        raise HTTPException(400, "back: ожидается путь внутри проекта")
    try:
        relation_service.relate(
            session,
            project_id=project_id,
            from_ref=from_ref.strip(),
            type=type.strip(),
            to_ref=to_ref.strip(),
            author=_author(request),
            note=note.strip() or None,
        )
        session.commit()
    except relation_service.RelationError as exc:
        session.rollback()
        query = urlencode(
            {"rel_error": str(exc), "rel_type": type, "rel_to": to_ref, "rel_note": note}
        )
        return RedirectResponse(url=f"{urlsplit(back).path}?{query}#relations", status_code=303)
    return RedirectResponse(url=back, status_code=303)
