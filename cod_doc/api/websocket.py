"""WebSocket endpoint that streams event_bus events to a project's clients.

URL: ``/ws/projects/{slug}``. The client connects when it lands on a project
page; the server forwards every event published for that slug as a JSON
text frame (``{"project": ..., "kind": ..., "payload": {...}, "ts": ...}``).

The connection stays open until the client closes it or the project goes
silent — there's no server-initiated heartbeat (websockets keepalives at
the protocol level are enough for our reverse proxy setup).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from cod_doc.api.deps import get_config
from cod_doc.services import event_bus

if TYPE_CHECKING:
    pass

router = APIRouter()
logger = logging.getLogger("cod_doc.api.ws")


@router.websocket("/ws/projects/{slug}")
async def project_event_stream(websocket: WebSocket, slug: str) -> None:
    cfg = get_config()
    if cfg.get_project(slug) is None:
        await websocket.close(code=4404, reason="project not found")
        return

    await websocket.accept()
    # Send a hello so the client knows the channel is live.
    await websocket.send_json(
        {"project": slug, "kind": "hello", "payload": {}, "ts": 0}
    )
    try:
        async with event_bus.subscribe(slug) as sub:
            async for event in sub:
                try:
                    await websocket.send_json(event.to_wire())
                except (RuntimeError, asyncio.CancelledError):
                    break
    except WebSocketDisconnect:
        return
    except Exception:  # pragma: no cover — defensive
        logger.exception("ws/projects/%s stream crashed", slug)
        try:
            await websocket.close(code=1011, reason="server error")
        except Exception:
            pass
