"""CLO-036: Bearer-гейт на ``/api/v1`` и streamable-http MCP (RFC 29 §3.8).

MCP гоняется через настоящее ASGI-приложение (``gated_http_app``) в режиме
stateless — так же, как его поднимают демоны: проверяется, что владелец токена,
привязанный гейтом, доходит до тула и до ``activity_service.emit``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cod_doc.api.auth import RateLimiter
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, DocumentModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import access_service as acc

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

ADMIN = "human:root"
MCP_BASE = "http://127.0.0.1:8801"
_MCP_HEADERS = {"accept": "application/json, text/event-stream"}


@pytest.fixture
def project(tmp_path: Path, migrate_db: Callable[[Path], None]) -> tuple[str, Path]:
    """Проект ``demo`` в реестре; вернуть его ``uid`` и путь к БД."""
    repo = tmp_path / "demo-repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)
    entry = ProjectEntry(name="demo", path=str(repo))
    Config(api_key="sk-test", model="test/model", base_url="https://x").add_project(entry)
    Project(entry).init()
    engine = make_engine(f"sqlite:///{db_path}")
    with transactional(make_session_factory(engine)) as session:
        ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
    engine.dispose()
    return acc.project_uid_for(entry), db_path


@pytest.fixture
def store() -> Iterator[None]:
    acc.init_store()
    yield
    acc._ENGINES.clear()


def _issue(handle: str, project_uid: str | None, role: str | None) -> str:
    factory = acc.open_store()
    assert factory is not None
    with transactional(factory) as s:
        if project_uid and role:
            acc.add_member(s, handle=handle, project_uid=project_uid, role=role, author=ADMIN)
        else:
            acc.add_member(
                s, handle=handle, project_uid="other-project", role="admin", author=ADMIN
            )
        return acc.create_token(s, handle=handle, label="laptop", author=ADMIN).token


def _denials() -> list[dict[str, Any]]:
    factory = acc.open_store()
    assert factory is not None
    with factory() as s:
        rows = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "auth.denied")
        ).all()
        return [r.payload for r in rows]


@pytest.fixture
def mcp_client() -> Iterator[TestClient]:
    from cod_doc.mcp import server

    saved = server.mcp.settings.stateless_http
    server.mcp.settings.stateless_http = True
    server.mcp._session_manager = None
    try:
        with TestClient(server.gated_http_app(), base_url=MCP_BASE) as client:
            yield client
    finally:
        server.mcp._session_manager = None
        server.mcp.settings.stateless_http = saved


def _call(client: TestClient, tool: str, token: str | None, **arguments: Any) -> Any:
    headers = dict(_MCP_HEADERS)
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    return client.post("/mcp", json=body, headers=headers)


def _doc_args(key: str, author: str) -> dict[str, Any]:
    return {
        "project": "demo",
        "doc_key": key,
        "type": "module-spec",
        "status": "draft",
        "title": key,
        "author": author,
    }


def _tool_result(resp: Any) -> tuple[bool, str]:
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    return bool(result.get("isError")), json.dumps(result, ensure_ascii=False)


def _doc_keys(db_path: Path) -> set[str]:
    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with make_session_factory(engine)() as s:
            return set(s.scalars(select(DocumentModel.doc_key)).all())
    finally:
        engine.dispose()


# --- без хранилища доступа: как раньше ----------------------------------------


def test_no_store_mcp_open_as_before(project: tuple[str, Path], mcp_client: TestClient) -> None:
    _uid, db_path = project
    is_error, text = _tool_result(_call(mcp_client, "doc_create", None, **_doc_args("a", "mcp")))
    assert not is_error, text
    assert "a" in _doc_keys(db_path)


# --- с хранилищем: токен обязателен -------------------------------------------


def test_missing_token_is_401_and_logged(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    resp = _call(mcp_client, "doc_list", None, project="demo")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")
    assert resp.json()["error"]["code"] == "SYNC-201"
    assert [d["code"] for d in _denials()] == ["SYNC-201"]


def test_wrong_and_revoked_token_rejected(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    uid, _db = project
    token = _issue("alice", uid, "writer")
    assert _call(mcp_client, "doc_list", "cdt_forged", project="demo").status_code == 401
    assert _call(mcp_client, "doc_list", token, project="demo").status_code == 200
    factory = acc.open_store()
    assert factory is not None
    with transactional(factory) as s:
        (row,) = acc.list_tokens(s, "alice")
        acc.revoke_token(s, token_uid=row.uid, author=ADMIN)
    assert _call(mcp_client, "doc_list", token, project="demo").status_code == 401


def test_writer_writes_with_own_author(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    uid, db_path = project
    token = _issue("alice", uid, "writer")
    is_error, text = _tool_result(
        _call(mcp_client, "doc_create", token, **_doc_args("mine", "human:alice"))
    )
    assert not is_error, text
    assert "mine" in _doc_keys(db_path)


def test_foreign_author_rejected_and_rolled_back(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    uid, db_path = project
    token = _issue("alice", uid, "writer")
    is_error, text = _tool_result(
        _call(mcp_client, "doc_create", token, **_doc_args("spoof", "human:bob"))
    )
    assert is_error
    assert "SYNC-202" in text
    assert "spoof" not in _doc_keys(db_path)


def test_reader_cannot_write(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    uid, db_path = project
    token = _issue("alice", uid, "reader")
    is_error, _ = _tool_result(_call(mcp_client, "doc_list", token, project="demo"))
    assert not is_error
    is_error, text = _tool_result(
        _call(mcp_client, "doc_create", token, **_doc_args("r", "human:alice"))
    )
    assert is_error
    assert "SYNC-202" in text
    assert "r" not in _doc_keys(db_path)


def test_non_member_cannot_enter_project(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    token = _issue("mallory", None, None)
    is_error, text = _tool_result(_call(mcp_client, "doc_list", token, project="demo"))
    assert is_error
    assert "не участник" in text


def test_failed_attempts_rate_limited(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    from cod_doc.api.auth import FAILURES_PER_WINDOW

    codes = [
        _call(mcp_client, "doc_list", "cdt_x", project="demo").status_code
        for _ in range(FAILURES_PER_WINDOW + 2)
    ]
    assert codes[:FAILURES_PER_WINDOW] == [401] * FAILURES_PER_WINDOW
    assert codes[FAILURES_PER_WINDOW:] == [429, 429]
    last = _call(mcp_client, "doc_list", "cdt_x", project="demo")
    assert int(last.headers["retry-after"]) > 0
    # блок по частоте — одна запись на окно, а не на каждую попытку
    assert [d["code"] for d in _denials()].count("SYNC-204") == 1


def test_forwarded_for_ignored_from_untrusted_peer(
    project: tuple[str, Path], store: None, mcp_client: TestClient
) -> None:
    # TestClient приходит с адреса "testclient" — не loopback и не доверенный прокси
    headers = {**_MCP_HEADERS, "x-forwarded-for": "198.51.100.9"}
    mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers)
    assert [d["ip"] for d in _denials()] == ["testclient"]


# --- /api/v1 ------------------------------------------------------------------


@pytest.fixture
def web_client() -> Iterator[TestClient]:
    from cod_doc.api.server import app

    with TestClient(app) as client:
        yield client


def test_v1_gated_legacy_api_not(
    project: tuple[str, Path], store: None, web_client: TestClient
) -> None:
    uid, _db = project
    assert web_client.get("/api/v1/projects/demo/search?q=x").status_code == 401
    assert web_client.get("/api/health").status_code == 200
    token = _issue("alice", uid, "reader")
    auth = {"authorization": f"Bearer {token}"}
    assert web_client.get("/api/v1/projects/demo/search?q=x", headers=auth).status_code == 200
    stranger = _issue("mallory", None, None)
    resp = web_client.get(
        "/api/v1/projects/demo/search?q=x", headers={"authorization": f"Bearer {stranger}"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "SYNC-202"


def test_v1_open_without_store(project: tuple[str, Path], web_client: TestClient) -> None:
    assert web_client.get("/api/v1/projects/demo/search?q=x").status_code == 200


# --- лимитер ------------------------------------------------------------------


def test_rate_limiter_window() -> None:
    limiter = RateLimiter(limit=2, window=10.0)
    assert limiter.hit("k", now=0.0) == 0
    assert limiter.hit("k", now=1.0) == 0
    assert limiter.hit("k", now=2.0) > 0
    assert limiter.retry_after("k", now=10.5) == 0


# --- запуск MCP наружу ----------------------------------------------------------


def test_mcp_refuses_public_bind_without_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import click
    import uvicorn

    from cod_doc.mcp import server
    from cod_doc.mcp.tools import _workspace

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("не должен стартовать"))
    try:
        with pytest.raises(click.UsageError, match="access init"):
            server.run_mcp_server(
                transport="streamable-http", host="0.0.0.0", port=1, profile="full"
            )
    finally:
        _workspace.set_shared(False)


def test_mcp_public_bind_with_store_and_allowed_host(
    store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from cod_doc.mcp import server

    started: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(k["host"]))
    security = server.mcp.settings.transport_security
    assert security is not None
    saved = (list(security.allowed_hosts), list(security.allowed_origins))
    try:
        server.run_mcp_server(
            transport="streamable-http",
            host="0.0.0.0",
            port=1,
            profile="full",
            allowed_hosts=("cod-doc.example.dev",),
        )
        assert "cod-doc.example.dev" in security.allowed_hosts
        assert "https://cod-doc.example.dev" in security.allowed_origins
    finally:
        from cod_doc.mcp.tools import _workspace

        security.allowed_hosts[:], security.allowed_origins[:] = saved
        server.mcp._session_manager = None
        _workspace.set_shared(False)
    assert started == ["0.0.0.0"]
