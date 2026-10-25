"""Режим «шторки» (WUI-001): та же карточка, но фрагментом, а не страницей.

Шторка грузит канонический URL задачи или документа с `?peek=1`. Отдельный
URL — не прихоть: ответ-фрагмент и ответ-страница лежат в кэше браузера под
разными ключами, и перезагрузка или «Назад» никогда не покажут голый
фрагмент вместо страницы.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from cod_doc.api.web.templates_env import templates

PEEK_PARAM = "peek"

_NOT_FOUND = 404


def is_peek(request: Request) -> bool:
    """Запрос пришёл из шторки: `?peek=1`."""
    return request.query_params.get(PEEK_PARAM) == "1"


def peek_not_found(request: Request, *, message: str) -> HTMLResponse:
    """404 шторки: сообщение и ссылка на полную страницу, а не JSON FastAPI.

    Клиент разрешает htmx вставить этот 404 в тело шторки — иначе она
    осталась бы пустой.
    """
    return templates.TemplateResponse(
        request,
        "_frag/peek_error.html",
        {"message": message, "full_url": request.url.path},
        status_code=_NOT_FOUND,
    )
