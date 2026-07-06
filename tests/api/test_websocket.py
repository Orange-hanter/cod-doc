"""COD-062 / COD-065: WebSocket endpoint pushes event_bus messages to browsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def ws_client(tmp_path: Path):
    repo = tmp_path / "ws-demo"
    repo.mkdir()
    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/m", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_ws_rejects_unknown_project(ws_client) -> None:
    client, _ = ws_client
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/projects/nope") as ws:
            ws.receive_json()


def test_ws_sends_hello_then_streams_events(ws_client) -> None:
    client, entry = ws_client
    from cod_doc.services import event_bus

    with client.websocket_connect(f"/ws/projects/{entry.name}") as ws:
        hello = ws.receive_json()
        assert hello["kind"] == "hello"
        assert hello["project"] == entry.name

        async def fire() -> None:
            await event_bus.publish(
                entry.name,
                "task.status_changed",
                {"task_id": "DEMO-001", "old": "pending", "new": "in-progress"},
            )

        # The TestClient runs the websocket handler on its own portal/loop;
        # publish via the same portal so subscribers actually fire.
        from anyio.from_thread import start_blocking_portal

        with start_blocking_portal() as portal:
            portal.call(fire)

        msg = ws.receive_json()
        assert msg["kind"] == "task.status_changed"
        assert msg["payload"]["task_id"] == "DEMO-001"


def test_ws_does_not_receive_other_projects_events(ws_client, tmp_path: Path) -> None:
    client, entry = ws_client
    # Register a second project so its events stay isolated.
    other_root = tmp_path / "other-proj"
    other_root.mkdir()
    other = ProjectEntry(name="other", path=str(other_root))
    import cod_doc.api.deps as deps

    deps.get_config().add_project(other)
    Project(other).init()

    from cod_doc.services import event_bus

    with client.websocket_connect(f"/ws/projects/{entry.name}") as ws:
        ws.receive_json()  # hello

        from anyio.from_thread import start_blocking_portal

        with start_blocking_portal() as portal:
            portal.call(event_bus.publish, "other", "task.status_changed", {"x": 1})
            portal.call(event_bus.publish, entry.name, "task.created", {"y": 2})

        msg = ws.receive_json()
        assert msg["kind"] == "task.created"
        assert msg["payload"] == {"y": 2}


def test_project_page_carries_data_project_attr(ws_client) -> None:
    """Pages under /p/{slug}/* expose data-project so the JS opens a WS."""
    client, entry = ws_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    body = r.text
    assert f'data-project="{entry.name}"' in body
    assert 'id="cod-ws-dot"' in body
    assert "cod_doc_ws.js" in body


def test_non_project_page_omits_data_project(ws_client) -> None:
    client, _ = ws_client
    r = client.get("/")
    assert r.status_code == 200
    assert "data-project=" not in r.text
    assert 'id="cod-ws-dot"' not in r.text
