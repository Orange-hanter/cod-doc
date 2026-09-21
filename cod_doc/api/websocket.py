"""WebSocket endpoint that streams event_bus events to a project's clients.

URL: ``/ws/projects/{slug}``. The client connects when it lands on a project
page; the server forwards every event published for that slug as a JSON
text frame (``{"project": ..., "kind": ..., "payload": {...}, "ts": ...}``).

The connection stays open until the client closes it or the project goes
silent — there's no server-initiated heartbeat (websockets keepalives at
the protocol level are enough for our reverse proxy setup).

ADO-194: событие и разрыв ждутся **гонкой**, а не последовательно
=================================================================

Наивная форма — ``async for event in sub`` — выглядит естественно и
содержит дедлок. Под ней ``Subscription.__anext__`` это
``await self._queue.get()``: ожидание без таймаута, которое будит только
новое событие шины. Разрыв соединения его не будит вообще — задача ждёт
``asyncio.Queue``, а не сокет.

Отсюда два наблюдаемых дефекта, оба с одной причиной:

* **``cod-doc serve`` не завершался.** uvicorn при остановке закрывает
  слушающий сокет, просит соединения закрыться и ждёт их завершения —
  это его строка ``Waiting for background tasks to complete``. Обработчик
  её не слышит и стоит дальше. Разбудить его мог бы
  ``event_bus.dispose()``, но он в lifespan **после** ``yield``, то есть
  запускается только когда соединения закроются. Которые не закроются.
  На живой машине это дало 16 зависших процессов за трое суток: ни один
  не слушал порт и ни один не ушёл по SIGTERM.
* **Подписка утекала после ухода клиента.** Закрытая вкладка браузера
  обработчика не будила: он узнавал о разрыве только при следующем
  событии, когда падал ``send_json``. До тех пор ``async with`` не
  закрывался, и очередь висела в реестре подписчиков.

Лечение — :func:`_pump`: ``sub.__anext__()`` и ``websocket.receive()``
крутятся одновременно через ``FIRST_COMPLETED``, и любой из двух исходов
завершает цикл. Разрыв (клиентский или от graceful shutdown uvicorn)
доезжает до приложения как сообщение ``websocket.disconnect``, поэтому
одного ``receive()`` хватает на оба случая.

Потолок ожидания на стороне сервера — ``timeout_graceful_shutdown`` в
``cli/cmd_serve.py``: он страхует от того же класса ошибки в любом другом
обработчике, которого здесь ещё нет.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

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


async def _cancel(*tasks: asyncio.Task[Any]) -> None:
    """Снять незавершённые задачи гонки и дождаться их смерти.

    Ожидание обязательно: без него ``asyncio`` ругается «Task was destroyed
    but it is pending» ровно в тот момент, когда человек читает лог
    остановки сервера.

    ``shield`` — не перестраховка. Отмена прилетает и снаружи (uvicorn гасит
    задачу соединения), и тогда голый ``gather`` перевозбуждает
    ``CancelledError`` прямо из ``finally``, не дав задачам прибраться.
    """
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))


async def _pump(websocket: WebSocket, sub: Subscription) -> None:
    """Слать события клиенту, пока соединение живо.

    Возвращается при разрыве — не бросает. Вызывающий закрывает
    подписку выходом из ``async with``, и именно поэтому возврат обязан
    быть штатным, а не исключением: на исключении ``__aexit__`` тоже
    отработает, но лог получит трассировку там, где ничего не сломалось.
    """
    next_event: asyncio.Task[Any] = asyncio.ensure_future(sub.__anext__())
    incoming: asyncio.Task[Any] = asyncio.ensure_future(websocket.receive())
    try:
        while True:
            done, _ = await asyncio.wait(
                {next_event, incoming}, return_when=asyncio.FIRST_COMPLETED
            )

            if incoming in done:
                try:
                    message = incoming.result()
                except (RuntimeError, WebSocketDisconnect):
                    # starlette бросает RuntimeError на чтении из уже
                    # закрытого сокета — для нас это тот же разрыв.
                    return
                if message.get("type") == _DISCONNECT:
                    return
                # Клиент что-то прислал. Протокол односторонний — читаем и
                # забываем, но ожидание надо перевзвести, иначе следующий
                # разрыв снова станет незаметным.
                incoming = asyncio.ensure_future(websocket.receive())

            if next_event in done:
                event = next_event.result()
                next_event = asyncio.ensure_future(sub.__anext__())
                try:
                    await websocket.send_json(event.to_wire())
                except (RuntimeError, asyncio.CancelledError):
                    # Как в исходной версии: отправка в закрытый сокет —
                    # штатное завершение, а не крах потока.
                    return
    finally:
        await _cancel(next_event, incoming)


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
        async with event_bus.subscribe(slug) as sub:
            await _pump(websocket, sub)
    except WebSocketDisconnect:
        return
    except Exception:  # defensive (covered by test_ws_stream_crash_closes_gracefully)
        logger.exception("ws/projects/%s stream crashed", slug)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="server error")
