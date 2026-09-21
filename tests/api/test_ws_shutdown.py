"""ADO-194: разрыв соединения завершает WS-обработчик, даже если событий нет.

Почему не через ``TestClient``. Он на выходе из ``websocket_connect``
отменяет задачу приложения, и отменённый ``queue.get()`` просыпается сам —
подписка закрывается, счётчик подписчиков падает до нуля, тест зеленеет
**и на сломанном коде**. Проверено мутацией: наивный ``async for`` проходит
такой тест целиком.

Настоящий uvicorn задачу соединения не отменяет: он закрывает сокет и ждёт,
пока обработчик закончит сам. Поэтому здесь ASGI-интерфейс дёргается
напрямую — ``receive`` отдаёт ``websocket.disconnect`` и больше ничего,
событий по шине не публикуется вовсе. Обработчик обязан завершиться от
одного лишь разрыва; на коде до фикса он уходит в ``queue.get()`` навсегда
и тест падает по бюджету.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from starlette.websockets import WebSocket

from cod_doc.api.websocket import project_event_stream
from cod_doc.config import Config, ProjectEntry
from cod_doc.services import event_bus

if TYPE_CHECKING:
    from pathlib import Path

#: Сколько даём обработчику на завершение после разрыва. Он должен уложиться
#: в миллисекунды; секунды здесь — запас на медленный CI, а не ожидание.
SHUTDOWN_BUDGET_S = 5.0

SLUG = "demo"


class FakeSocket:
    """Минимальный ASGI-дуплекс: очередь входящих плюс журнал исходящих."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


@pytest.fixture
def configured(tmp_path: Path):
    repo = tmp_path / "ws-shutdown-demo"
    repo.mkdir()
    entry = ProjectEntry(name=SLUG, path=str(repo))
    cfg = Config(api_key="sk-test", model="test/m", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    yield
    event_bus.dispose()


async def _run_handler(socket: FakeSocket) -> None:
    scope: dict[str, Any] = {
        "type": "websocket",
        "path": f"/ws/projects/{SLUG}",
        "headers": [],
    }
    websocket = WebSocket(scope, receive=socket.receive, send=socket.send)
    await project_event_stream(websocket, SLUG)


async def test_disconnect_alone_ends_the_handler(configured) -> None:
    """Ни одного события — только разрыв. Обработчик обязан уйти."""
    socket = FakeSocket()
    await socket.incoming.put({"type": "websocket.connect"})

    handler = asyncio.ensure_future(_run_handler(socket))

    # Дать дойти до ожидания: accept + hello уже отправлены.
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(m.get("type") == "websocket.send" for m in socket.sent):
            break
    assert event_bus.active_subscribers(SLUG) == 1, "подписка не открылась"

    await socket.incoming.put({"type": "websocket.disconnect", "code": 1001})

    try:
        await asyncio.wait_for(handler, timeout=SHUTDOWN_BUDGET_S)
    except TimeoutError:
        handler.cancel()
        pytest.fail(
            "обработчик не завершился за "
            f"{SHUTDOWN_BUDGET_S} с после разрыва — он спит на queue.get() "
            "и в этом состоянии uvicorn не может остановиться"
        )

    assert event_bus.active_subscribers(SLUG) == 0, "подписка пережила разрыв"


async def test_events_still_reach_the_client(configured) -> None:
    """Починка не должна стоить доставки: событие по-прежнему доезжает."""
    socket = FakeSocket()
    await socket.incoming.put({"type": "websocket.connect"})
    handler = asyncio.ensure_future(_run_handler(socket))

    for _ in range(100):
        await asyncio.sleep(0.01)
        if event_bus.active_subscribers(SLUG) == 1:
            break

    await event_bus.publish(SLUG, "task.status_changed", {"task_id": "DEMO-1"})

    for _ in range(100):
        await asyncio.sleep(0.01)
        if len(socket.sent) >= 3:  # accept + hello + событие
            break

    await socket.incoming.put({"type": "websocket.disconnect", "code": 1001})
    await asyncio.wait_for(handler, timeout=SHUTDOWN_BUDGET_S)

    kinds = [
        __import__("json").loads(m["text"])["kind"]
        for m in socket.sent
        if m.get("type") == "websocket.send" and "text" in m
    ]
    assert kinds == ["hello", "task.status_changed"], kinds


async def test_client_chatter_does_not_swallow_the_next_disconnect(configured) -> None:
    """Сообщение от клиента не должно съедать следующий разрыв.

    Гонка обязана перевзвести ожидание входящих после каждого сообщения.
    Забудь она это — ``incoming`` навсегда остаётся завершённым, цикл
    крутится вхолостую, а разрыв не наблюдается уже никогда. Первый тест
    этой мутации не поймает: там клиент молчит.
    """
    socket = FakeSocket()
    await socket.incoming.put({"type": "websocket.connect"})
    handler = asyncio.ensure_future(_run_handler(socket))

    for _ in range(100):
        await asyncio.sleep(0.01)
        if event_bus.active_subscribers(SLUG) == 1:
            break

    await socket.incoming.put({"type": "websocket.receive", "text": "ping"})
    await asyncio.sleep(0.05)
    assert not handler.done(), "сообщение клиента не должно закрывать поток"

    await socket.incoming.put({"type": "websocket.disconnect", "code": 1001})
    try:
        await asyncio.wait_for(handler, timeout=SHUTDOWN_BUDGET_S)
    except TimeoutError:
        handler.cancel()
        pytest.fail("после сообщения клиента разрыв перестал наблюдаться")


def test_serve_caps_graceful_shutdown() -> None:
    """Страховка от того же класса ошибки в любом другом обработчике.

    Без потолка uvicorn ждёт открытые соединения неограниченно, и один
    заснувший обработчик делает Ctrl-C бесполезным. Проверяем, что флаг
    вообще доезжает до uvicorn: причину этого бага мы вылечили, а таймаут
    стоит ради следующей.
    """
    from unittest.mock import patch

    from click.testing import CliRunner

    from cod_doc.cli.cmd_serve import GRACEFUL_SHUTDOWN_TIMEOUT_S, serve

    cfg = Config(api_key="sk-test", model="test/m", base_url="https://x")
    with patch("uvicorn.run") as run:
        CliRunner().invoke(serve, [], obj={"config": cfg})

    assert run.call_args is not None, "uvicorn.run не был вызван"
    assert run.call_args.kwargs["timeout_graceful_shutdown"] == GRACEFUL_SHUTDOWN_TIMEOUT_S
