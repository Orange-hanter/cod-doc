"""Web-rendered error model.

`WebError` and subclasses are raised from web handlers (pages.py, fragments.py)
to surface a recoverable error to the user as an alert in `#alerts`. The
exception handler registered in `server.py` renders the alert via HTMX
out-of-band swap (HTMX requests) or via cookie-flash + 303 redirect
(non-HTMX form posts).

For non-recoverable errors (DB missing, route not found by FastAPI itself),
keep using `HTTPException` — the global FastAPI handler renders a plain
4xx/5xx response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlsplit

if TYPE_CHECKING:
    from urllib.parse import SplitResult

    from fastapi import Request
    from fastapi.responses import Response

Severity = Literal["error", "warning", "info"]

# Cookies are limited to ~4 KB browser-wide and must be latin-1 — we percent-
# encode messages, but a single non-ASCII char becomes 9 bytes, so a multi-
# line traceback can blow the limit fast. WEB-054: cap before encoding.
COOKIE_FLASH_MAX_LEN = 512


def truncate_for_cookie(message: str, *, max_len: int = COOKIE_FLASH_MAX_LEN) -> str:
    """Truncate a flash message so it fits in a 4 KB cookie after percent-encoding.

    Uses a one-char ellipsis (`…`) since percent-encoding triples its size
    in the worst case — still cheaper than a 3-char "..." that survives
    encoding verbatim plus another truncation pass.
    """
    if len(message) <= max_len:
        return message
    return message[: max_len - 1] + "…"


def set_flash_cookie(response: Response, request: Request, *, severity: str, message: str) -> None:
    """Положить flash-уведомление в пару cookie для следующей страницы.

    Читает их только сервер (``base.html`` через ``request.cookies``), поэтому
    ``HttpOnly``; ``SameSite=Lax`` — чтобы чужой сайт не подсовывал баннер
    своим POST; ``Secure`` — когда страница отдана по https. Заголовки cookie
    только latin-1, поэтому текст percent-кодируется после усечения.
    """
    secure = request.url.scheme == "https"
    for name, value in (
        ("flash_severity", severity),
        ("flash_message", quote(truncate_for_cookie(message))),
    ):
        response.set_cookie(
            name,
            value,
            max_age=30,
            path="/",
            httponly=True,
            samesite="lax",
            secure=secure,
        )


_DEFAULT_PORTS = {"http": 80, "https": 443}
# Что остаётся в пути и запросе как есть; прочее (пробел, кавычки, угловые
# скобки, не-ASCII) percent-кодируется. ``%`` здесь — чтобы уже закодированное
# не кодировалось второй раз.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "?"
# C0 и DEL: в адресе им не место ни сырыми, ни после чистки.
_CONTROL_CHARS = frozenset(chr(c) for c in (*range(0x20), 0x7F))


def _has_unsafe_chars(value: str) -> bool:
    # ``urlsplit`` молча вырезает \t, \r и \n, поэтому проверка идёт по
    # сырому заголовку, до разбора.
    return "\\" in value or not _CONTROL_CHARS.isdisjoint(value)


def _same_origin(parts: SplitResult, request: Request) -> bool:
    """Схема, хост и порт совпадают без учёта регистра и с дефолтным портом."""
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or scheme != request.url.scheme.lower():
        return False
    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:  # порт не число или вне диапазона
        return False
    own_port = request.url.port or _DEFAULT_PORTS[scheme]
    own_host = (request.url.hostname or "").lower()
    return bool(parts.hostname) and parts.hostname == own_host and port == own_port


def _safe_back_url(request: Request, fallback: str) -> str:
    """Путь возврата из ``Referer``, только в пределах этого же сайта (CWE-601).

    Абсолютный ``Referer`` принимается, когда схема, хост и порт совпадают с
    запросом (регистр и явный дефолтный порт не в счёт), относительный — когда
    начинается ровно с одного ``/``: ``//evil`` браузер читает как адрес
    другого хоста. Обратный слеш и управляющие символы в любом месте — отказ:
    браузеры читают ``\\`` как ``/``. Итог всегда относительный путь,
    собранный заново из разобранных пути и запроса с percent-кодированием,
    так что сырые байты заголовка в ``Location`` не попадают.
    """
    referer = request.headers.get("Referer")
    back = same_site_path(request, referer) if referer else None
    return back if back is not None else fallback


def same_site_path(request: Request, url: str) -> str | None:
    """Относительный путь из ``url``, если он ведёт на этот же сайт; иначе None.

    Правила — как у ``_safe_back_url``: свой хост в любом написании, ровно
    один ведущий ``/``, без ``\\`` и управляющих символов; итог собран заново
    с percent-кодированием. Его же зовёт полоса «назад» (ADO-149) — и для
    ``Referer``, и для ``?from=``.
    """
    if _has_unsafe_chars(url):
        return None
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        if not _same_origin(parts, request):
            return None
    elif url.startswith("//"):
        return None
    path = parts.path
    if not path.startswith("/") or path.startswith("//"):
        return None
    back = quote(path, safe=_PATH_SAFE)
    if parts.query:
        back += "?" + quote(parts.query, safe=_QUERY_SAFE)
    return back


class WebError(Exception):
    """Base class. Subclass to choose default severity + status code."""

    severity: Severity = "error"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        severity: Severity | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if severity is not None:
            self.severity = severity
        if status_code is not None:
            self.status_code = status_code


class ValidationWebError(WebError):
    severity: Severity = "warning"
    status_code: int = 400


class NotFoundWebError(WebError):
    severity: Severity = "error"
    status_code: int = 404


class ConflictWebError(WebError):
    severity: Severity = "warning"
    status_code: int = 409
