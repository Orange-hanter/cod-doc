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

from typing import Literal

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
