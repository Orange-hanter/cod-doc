"""ADO-194: разрыв соединения сворачивает обработчика, что бы тот ни делал.

Почему не через ``TestClient``. Он на выходе из ``websocket_connect``
отменяет задачу приложения, и отменённое ожидание просыпается само —
подписка закрывается, счётчик падает до нуля, и тест зеленеет **и на
сломанном коде**. Проверено мутацией: наивный ``async for`` проходил такую
проверку целиком, поэтому она была выброшена.

Настоящий uvicorn задачу соединения не отменяет: он закрывает сокет и ждёт,
пока обработчик закончит сам. Поэтому здесь ASGI-интерфейс дёргается
напрямую — никто ничего не отменяет, и уйти обработчик обязан сам.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest
from starlette.websockets import WebSocket

from cod_doc.api.websocket import project_event_stream
from cod_doc.services import event_bus

if TYPE_CHECKING:
    from pathlib import Path

#: Бюджет на завершение после разрыва. Обработчик должен укладываться в
#: миллисекунды; секунды здесь — запас на медленный CI, а не ожидание.
SHUTDOWN_BUDGET_S = 5.0

#: Потолок на ожидание вспомогательных условий в самом тесте. Отдельная
#: константа, потому что смысл другой: не «сколько можно», а «когда сдаться
#: и сказать, что не дождались».
SETUP_BUDGET_S = 3.0

SLUG = "ws-shutdown-demo"


class FakeSocket:
    """Минимальный ASGI-дуплекс: очередь входящих плюс журнал исходящих."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        #: Пока событие не взведено, каждая отправка залипает. Так
        #: воспроизводится клиент, переставший читать: буфер переполнен, и
        #: `send` паркуется — ровно то состояние, в котором простой «флаг
        #: разрыва» обработчика не спас бы.
        self.send_allowed = asyncio.Event()
        self.send_allowed.set()

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send(self, message: dict[str, Any]) -> None:
        await self.send_allowed.wait()
        self.sent.append(message)

    def frames(self) -> list[dict[str, Any]]:
        """Полезная нагрузка отправленных текстовых кадров."""
        return [
            json.loads(m["text"])
            for m in self.sent
            if m.get("type") == "websocket.send" and "text" in m
        ]


@pytest.fixture
def configured(tmp_path: Path):
    """Конфиг с одним проектом и заведомо пустая шина по обе стороны теста.

    ``dispose`` до ``yield`` тоже обязателен: реестр подписчиков —
    модульный глобал, и мусор от упавшего соседа по воркеру превратил бы
    ассерт на счётчик в ложное падение.
    """
    from cod_doc.config import Config, ProjectEntry

    repo = tmp_path / SLUG
    repo.mkdir()
    entry = ProjectEntry(name=SLUG, path=str(repo))
    cfg = Config(api_key="sk-test", model="test/m", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    event_bus.dispose()
    yield
    event_bus.dispose()


async def _run_handler(socket: FakeSocket) -> asyncio.Task[None]:
    """Запустить обработчика и дождаться, пока он встанет на ожидание."""
    scope: dict[str, Any] = {
        "type": "websocket",
        "path": f"/ws/projects/{SLUG}",
        "headers": [],
    }
    websocket = WebSocket(scope, receive=socket.receive, send=socket.send)
    await socket.incoming.put({"type": "websocket.connect"})
    handler = asyncio.ensure_future(project_event_stream(websocket, SLUG))
    await _until(lambda: event_bus.active_subscribers(SLUG) == 1, "подписка не открылась")
    return handler


async def _until(condition, what: str, budget_s: float = SETUP_BUDGET_S) -> None:
    """Дождаться условия или упасть, назвав чего именно не дождались.

    Голый цикл без этой проверки превращает подвисший setup в падение
    совсем другого ассерта — и читатель идёт чинить не то.
    """
    deadline = asyncio.get_running_loop().time() + budget_s
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"не дождались за {budget_s} с: {what}")


async def _expect_finished(handler: asyncio.Task[None], why: str) -> None:
    """Обработчик обязан завершиться сам, без отмены снаружи."""
    try:
        await asyncio.wait_for(handler, timeout=SHUTDOWN_BUDGET_S)
    except TimeoutError:
        handler.cancel()
        with pytest.raises((asyncio.CancelledError, TimeoutError)):
            await handler
        pytest.fail(f"обработчик не завершился за {SHUTDOWN_BUDGET_S} с: {why}")


async def test_disconnect_alone_ends_the_handler(configured) -> None:
    """Ни одного события — только разрыв. Обработчик обязан уйти."""
    socket = FakeSocket()
    handler = await _run_handler(socket)

    await socket.incoming.put({"type": "websocket.disconnect", "code": 1001})

    await _expect_finished(handler, "спит на ожидании события и не слышит разрыв")
    assert event_bus.active_subscribers(SLUG) == 0, "подписка пережила разрыв"


async def test_disconnect_interrupts_a_stalled_send(configured) -> None:
    """Разрыв прерывает и залипшую отправку, а не только ожидание события.

    Самый частый реальный разрыв — клиент перестал читать. Тогда `send`
    паркуется на переполненном буфере, и обработчик находится НЕ в
    ожидании события. Конструкция, которая смотрит на разрыв только между
    отправками, здесь снова зависает — этот кейс её и ловит.
    """
    socket = FakeSocket()
    handler = await _run_handler(socket)

    socket.send_allowed.clear()  # клиент перестал читать
    await event_bus.publish(SLUG, "task.status_changed", {"task_id": "DEMO-1"})
    await asyncio.sleep(0.05)
    assert not handler.done(), "обработчик обязан ждать в отправке"

    await socket.incoming.put({"type": "websocket.disconnect", "code": 1001})

    await _expect_finished(handler, "залипшая отправка пережила разрыв")
    assert event_bus.active_subscribers(SLUG) == 0


async def test_events_still_reach_the_client(configured) -> None:
    """Починка не должна стоить доставки."""
    socket = FakeSocket()
    handler = await _run_handler(socket)

    await event_bus.publish(SLUG, "task.status_changed", {"task_id": "DEMO-1"})
    await _until(lambda: len(socket.frames()) >= 2, "событие не доехало до клиента")

    await socket.incoming.put({"type": "websocket.disconnect", "code": 1001})
    await _expect_finished(handler, "разрыв не завершил обработчика")

    assert [f["kind"] for f in socket.frames()] == ["hello", "task.status_changed"]


async def test_client_chatter_does_not_swallow_the_next_disconnect(configured) -> None:
    """Сообщение от клиента не должно съедать следующий разрыв.

    Сторож обязан читать входящие в цикле. Прочти он ровно один раз —
    разрыв после первого же сообщения клиента стал бы незаметным, а
    первый тест этой мутации не поймает: там клиент молчит.
    """
    socket = FakeSocket()
    handler = await _run_handler(socket)

    await socket.incoming.put({"type": "websocket.receive", "text": "ping"})
    await asyncio.sleep(0.05)
    assert not handler.done(), "сообщение клиента не должно закрывать поток"

    await socket.incoming.put({"type": "websocket.disconnect", "code": 1001})
    await _expect_finished(handler, "после сообщения клиента разрыв перестал наблюдаться")


async def test_bus_dispose_wakes_a_waiting_consumer(configured) -> None:
    """`dispose()` будит ожидающего, а не только забывает его очередь.

    Причина дефекта была именно здесь: «сброшенная» подписка продолжала
    жить в заснувшем потребителе. Тест на уровне шины, потому что чинить
    это у каждого потребителя по отдельности — та ошибка, которую ревью и
    поймало.
    """
    seen: list[str] = []

    async def consumer() -> None:
        async with event_bus.subscribe(SLUG) as sub:
            async for event in sub:
                seen.append(event.kind)

    task = asyncio.ensure_future(consumer())
    await _until(lambda: event_bus.active_subscribers(SLUG) == 1, "подписка не открылась")

    event_bus.dispose()

    await asyncio.wait_for(task, timeout=SHUTDOWN_BUDGET_S)
    assert seen == []


def test_serve_caps_graceful_shutdown() -> None:
    """Потолок ожидания вообще доезжает до uvicorn.

    Причину этого бага вылечили в обработчике; таймаут стоит ради
    следующего такого же. Значение проверяем константой, а не литералом:
    число выбрано «заведомо больше легитимного запроса», и подгонять тест
    под изменение смысла не должно быть легко.
    """
    from unittest.mock import patch

    from click.testing import CliRunner

    from cod_doc.cli.cmd_serve import GRACEFUL_SHUTDOWN_TIMEOUT_S, serve
    from cod_doc.config import Config

    cfg = Config(api_key="sk-test", model="test/m", base_url="https://x")
    with patch("uvicorn.run") as run:
        CliRunner().invoke(serve, [], obj={"config": cfg})

    assert run.call_args is not None, "uvicorn.run не был вызван"
    assert run.call_args.kwargs["timeout_graceful_shutdown"] == GRACEFUL_SHUTDOWN_TIMEOUT_S
    # LLM-эндпоинты работают минутами; короткий потолок убивал бы их
    # посреди работы при рестарте веб-сервиса.
    assert GRACEFUL_SHUTDOWN_TIMEOUT_S >= 300
