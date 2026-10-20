"""Потолок тела запроса ``/api/sync/v1`` по виду маршрута (CLO-035).

JSON-маршруты (``refs``, ``objects/has``, ``meta``, ``members``, ``tokens``)
FastAPI разбирает до входа в обработчик — и до проверки роли. Без потолка
на уровне ASGI любой владелец токена (и reader) мог бы прислать гигабайт JSON.
Здесь тело считается по мере чтения и обрывается 413, как только превысит
потолок своего маршрута; ``Content-Length`` больше потолка — 413 сразу.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["SyncBodyCap", "cap_for"]

_PREFIX: Final = "/api/sync/v1/"
_KIB: Final = 1024
_MIB: Final = 1024 * _KIB
#: Потолок по умолчанию — для небольших JSON-тел (``{sha}``, ``{role}`` …).
_SMALL: Final = 64 * _KIB
_RULES: Final = (
    (re.compile(r"/projects/[^/]+/objects/has$"), 8 * _MIB),
    (re.compile(r"/projects/[^/]+/objects/[^/]+$"), 128 * _MIB),
    (re.compile(r"/projects/[^/]+/json/"), 1 * _MIB),
    (re.compile(r"/projects/[^/]+/snapshots/[^/]+/[^/]+$"), 1024 * _MIB),
)
_HTTP_TOO_LARGE: Final = 413


def cap_for(path: str) -> int:
    for pattern, cap in _RULES:
        if pattern.search(path):
            return cap
    return _SMALL


class _TooLargeError(Exception):
    pass


class SyncBodyCap:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(_PREFIX):
            await self.app(scope, receive, send)
            return
        cap = cap_for(scope["path"])
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                declared = bytes(value).decode("latin-1").strip()
                if not declared.isdigit() or int(declared) > cap:
                    await _reject(send, cap)
                    return
        seen = 0
        started = False

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:
                    raise _TooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, tracking_send)
        except _TooLargeError:
            if not started:
                await _reject(send, cap)


async def _reject(send: Send, cap: int) -> None:
    body = json.dumps(
        {
            "error": {
                "code": "SYNC-110",
                "message": f"тело запроса больше {cap} байт",
                "details": {},
            }
        },
        ensure_ascii=False,
    ).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": _HTTP_TOO_LARGE, "headers": headers})
    await send({"type": "http.response.body", "body": body})
