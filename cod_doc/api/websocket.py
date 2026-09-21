"""WebSocket endpoint that streams event_bus events to a project's clients.

URL: ``/ws/projects/{slug}``. The client connects when it lands on a project
page; the server forwards every event published for that slug as a JSON
text frame (``{"project": ..., "kind": ..., "payload": {...}, "ts": ...}``).

The connection stays open until the client closes it or the project goes
silent — there's no server-initiated heartbeat (websockets keepalives at
the protocol level are enough for our reverse proxy setup).

ADO-194: разрыв обязан сворачивать всю работу соединения
========================================================

Раньше цикл был просто ``async for event in sub``, а под ним — ожидание,
которое будило только новое событие шины. Разрыв соединения его не будил,
и обработчик спал вечно: ``cod-doc serve`` не завершался по Ctrl-C (16
процессов-зомби за трое суток на живой машине), а подписка переживала
закрытие вкладки.

Причину вылечили в самой шине: ``Subscription`` теперь завершаема, и
``async for`` заканчивается сам, когда подписку закрывают. Здесь остаётся
вторая половина — **узнать** о разрыве и закрыть подписку.

Сторож разрыва живёт в task group и отменяет **scope**, а не выставляет
флаг. Разница принципиальная: отправка клиенту тоже может залипнуть — если
тот перестал читать (закрытая крышка ноутбука, TCP zero-window), ``send``
паркуется на переполненном буфере. Флаг такую отправку не прервёт, и
зависание вернулось бы ровно в самом частом реальном сценарии разрыва.
Отмена scope прерывает и её.

Отменой владеет anyio, а не ``try/except CancelledError`` руками. Это не
стилистика: ``CancelledError`` — наследник ``BaseException``, и код,
превращающий его в штатный возврат, обезоруживает и внешний
``timeout_graceful_shutdown``, и любой объемлющий ``TaskGroup``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from cod_doc.api.deps import get_config
from cod_doc.services import event_bus

if TYPE_CHECKING:
    from cod_doc.services.event_bus import Subscription

router = APIRouter()
logger = logging.getLogger("cod_doc.api.ws")

#: Тип сообщения ASGI, которым и клиентский разрыв, и остановка сервера
#: доезжают до приложения одинаково.
_DISCONNECT = "websocket.disconnect"

#: Что starlette бросает на чтении/отправке в уже мёртвый сокет.
#: ``WebSocketDisconnect`` — штатный путь send-side разрыва (starlette
#: превращает в него ``ClientDisconnected`` от uvicorn); ``RuntimeError`` —
#: обращение к сокету после закрытия.
_GONE = (WebSocketDisconnect, RuntimeError)


async def _watch_disconnect(websocket: WebSocket, scope: anyio.CancelScope) -> None:
    """Дождаться разрыва и свернуть работу соединения.

    Читает входящие в цикле, а не один раз: клиент вправе что-то прислать,
    и одно его сообщение не должно делать следующий разрыв незаметным.
    """
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == _DISCONNECT:
                return
    except _GONE:
        return
    finally:
        scope.cancel()


async def _stream(websocket: WebSocket, sub: Subscription) -> None:
    """Слать события, пока подписка жива и сокет принимает."""
    with contextlib.suppress(*_GONE):
        async for event in sub:
            await websocket.send_json(event.to_wire())


@router.websocket("/ws/projects/{slug}")
async def project_event_stream(websocket: WebSocket, slug: str) -> None:
    cfg = get_config()
    if cfg.get_project(slug) is None:
        await websocket.close(code=4404, reason="project not found")
        return

    await websocket.accept()
    # Send a hello so the client knows the channel is live.
    await websocket.send_json({"project": slug, "kind": "hello", "payload": {}, "ts": 0})
    try:
        async with event_bus.subscribe(slug) as sub, anyio.create_task_group() as tg:
            tg.start_soon(_watch_disconnect, websocket, tg.cancel_scope)
            await _stream(websocket, sub)
            # Поток кончился сам (шину закрыли) — сторожу больше нечего
            # ждать, иначе task group висел бы на нём до разрыва.
            tg.cancel_scope.cancel()
    except WebSocketDisconnect:
        return
    except Exception:  # defensive (covered by test_ws_stream_crash_closes_gracefully)
        logger.exception("ws/projects/%s stream crashed", slug)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="server error")
