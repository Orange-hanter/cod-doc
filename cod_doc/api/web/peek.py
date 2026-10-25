"""Режим «шторки» (WUI-001): та же карточка, но фрагментом, а не страницей.

Шторка грузит канонический URL задачи или документа с `?peek=1`. Отдельный
URL — не прихоть: ответ-фрагмент и ответ-страница лежат в кэше браузера под
разными ключами, и перезагрузка или «Назад» никогда не покажут голый
фрагмент вместо страницы.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

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
        {"message": message, "full_url": full_page_url(request)},
        status_code=_NOT_FOUND,
    )


def full_page_url(request: Request) -> str:
    """URL полной страницы: тот же путь и query, но без `peek`.

    Прочие параметры — состояние страницы (`?raw=1` у документа): ссылка
    «открыть полную страницу» обязана его сохранить.

    Путь — `raw_path`, байты в том виде, в каком их прислал клиент.
    Раскодированные формы теряют информацию: `request.url.path` обрезан на
    раскодированном `?` (id `A%3FB` → `/tasks/A`), а в `scope["path"]`
    `%2F` уже неотличим от `/` (ключ документа `a%2Fb` → `a/b`). Без
    `raw_path` (сервер его не передал) — кодируем `scope["path"]` заново.
    """
    rest = [(k, v) for k, v in request.query_params.multi_items() if k != PEEK_PARAM]
    raw = request.scope.get("raw_path")
    path = raw.decode("latin-1") if raw else quote(request.scope["path"], safe="/")
    return path + (f"?{urlencode(rest)}" if rest else "")
