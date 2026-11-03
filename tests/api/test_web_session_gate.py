"""0056: SessionGate — веб за OIDC-логином (Pocket ID).

Сеть патчится на границе `oidc_service` (house style — без HTTP-моков):
`discover`/`exchange_code`/`fetch_userinfo` подменяются monkeypatch'ем, всё
остальное — настоящий флоу через TestClient с куками.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cod_doc.infra.db import transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.services import access_service as acc
from cod_doc.services import oidc_service

if TYPE_CHECKING:
    from collections.abc import Iterator

    from cod_doc.config import Config

ADMIN = "human:root"
LINKED_SUB = "sub-alice-1"
_IDP_DISCOVERY = {
    "authorization_endpoint": "https://idp.example/authorize",
    "token_endpoint": "https://idp.example/token",
    "userinfo_endpoint": "https://idp.example/userinfo",
}


@pytest.fixture(autouse=True)
def _reset_login_limiter() -> Iterator[None]:
    """Лимитер логина — модульная переменная: между тестами чистим окно."""
    from cod_doc.api.web.pages import auth as auth_page

    auth_page._FAILS._hits.clear()
    yield
    auth_page._FAILS._hits.clear()


@pytest.fixture
def store() -> Iterator[None]:
    acc.init_store()
    yield
    acc._ENGINES.clear()


@pytest.fixture
def oidc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COD_DOC_OIDC_ISSUER", "https://idp.example")
    monkeypatch.setenv("COD_DOC_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("COD_DOC_OIDC_CLIENT_SECRET", "sec")
    monkeypatch.setenv(
        "COD_DOC_OIDC_REDIRECT_URI", "https://cod-doc.example.dev/auth/oidc/callback"
    )


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Провайдер на границе oidc_service: discovery/обмен/userinfo."""

    def fake_exchange(cfg: Config, discovery: dict[str, Any], *, code: str, verifier: str) -> str:
        if code != "good-code":
            msg = "bad code"
            raise oidc_service.OidcError(msg)
        return "access-token"

    monkeypatch.setattr(oidc_service, "discover", lambda cfg: dict(_IDP_DISCOVERY))
    monkeypatch.setattr(oidc_service, "exchange_code", fake_exchange)
    monkeypatch.setattr(
        oidc_service, "fetch_userinfo", lambda d, at: {"sub": LINKED_SUB, "preferred_username": "a"}
    )


@pytest.fixture
def linked(store: None) -> None:
    factory = acc.open_store()
    assert factory is not None
    with transactional(factory) as s:
        acc.add_member(s, handle="alice", project_uid="p1", role="admin", author=ADMIN)
        acc.oidc_link(s, handle="alice", sub=LINKED_SUB, author=ADMIN)


@pytest.fixture
def web_client() -> Iterator[TestClient]:
    from cod_doc.api.server import app

    with TestClient(app, follow_redirects=False) as client:
        yield client


def _denials() -> list[dict[str, Any]]:
    factory = acc.open_store()
    assert factory is not None
    with factory() as s:
        rows = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "auth.denied")
        ).all()
        return [r.payload for r in rows]


def _login(client: TestClient) -> None:
    """login → callback: полный флоу, куки живут в client."""
    r = client.get("/auth/oidc/login", params={"next": "/p/demo"})
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith("https://idp.example/authorize?")
    state = client.cookies[oidc_service.STATE_COOKIE]
    r = client.get("/auth/oidc/callback", params={"code": "good-code", "state": state})
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/p/demo"


# --- фича выключена: как раньше -------------------------------------------------


def test_gate_off_web_open_api_bearer_only(store: None, web_client: TestClient) -> None:
    """Issuer пуст — гейт пропускает веб; /api/v1 остаётся за Bearer."""
    assert web_client.get("/").status_code == 200
    assert web_client.get("/api/v1/projects").status_code == 401


# --- фича включена ---------------------------------------------------------------


def test_gate_redirects_web_and_leaves_auth_static_api(
    store: None, oidc_env: None, idp: None, web_client: TestClient
) -> None:
    r = web_client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/oidc/login?next=/"
    # маршруты логина открыты, статика — тоже
    login = web_client.get("/auth/oidc/login")
    assert login.status_code == 303  # → провайдер
    assert web_client.get("/static/favicon.svg").status_code != 303
    # /api/v1 остаётся за Bearer-гейтом; legacy API — как раньше
    assert web_client.get("/api/v1/projects").status_code == 401
    assert web_client.get("/api/health").status_code == 200


def test_gate_503_when_oidc_on_but_store_missing(oidc_env: None, web_client: TestClient) -> None:
    r = web_client.get("/")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SYNC-205"


def test_full_login_flow_page_and_header(
    store: None, linked: None, oidc_env: None, idp: None, web_client: TestClient
) -> None:
    _login(web_client)
    assert oidc_service.SESSION_COOKIE in web_client.cookies
    page = web_client.get("/")
    assert page.status_code == 200
    assert "alice" in page.text  # хендл в шапке (scope["state"]["web_handle"])
    assert "/auth/logout" in page.text
    # вход без сессии на страницу проекта ведёт обратно на логин с next
    other = TestClient(web_client.app, follow_redirects=False)  # свежий клиент без кук
    r = other.get("/p/demo", follow_redirects=False)
    assert r.status_code == 303
    assert "next=/p/demo" in r.headers["location"]


def test_logout_clears_session(
    store: None, linked: None, oidc_env: None, idp: None, web_client: TestClient
) -> None:
    _login(web_client)
    r = web_client.get("/auth/logout")
    assert r.status_code == 303
    assert web_client.get("/").status_code == 303  # сессии больше нет


def test_unknown_sub_denied_and_logged(
    store: None, oidc_env: None, idp: None, web_client: TestClient
) -> None:
    # linked-фикстуры нет: sub в userinfo никому не привязан
    web_client.get("/auth/oidc/login", follow_redirects=False)
    state = web_client.cookies[oidc_service.STATE_COOKIE]
    r = web_client.get("/auth/oidc/callback", params={"code": "good-code", "state": state})
    assert r.status_code == 403
    assert "не привязан" in r.text
    assert [d["code"] for d in _denials()] == ["SYNC-201"]
    assert [d["path"] for d in _denials()] == ["/auth/oidc/callback"]


def test_state_mismatch_denied(
    store: None, linked: None, oidc_env: None, idp: None, web_client: TestClient
) -> None:
    web_client.get("/auth/oidc/login", follow_redirects=False)
    r = web_client.get("/auth/oidc/callback", params={"code": "good-code", "state": "forged"})
    assert r.status_code == 403
    assert "state" in r.text
    assert [d["code"] for d in _denials()] == ["SYNC-201"]


def test_idp_error_param_denied(
    store: None, linked: None, oidc_env: None, idp: None, web_client: TestClient
) -> None:
    web_client.get("/auth/oidc/login", follow_redirects=False)
    state = web_client.cookies[oidc_service.STATE_COOKIE]
    r = web_client.get("/auth/oidc/callback", params={"error": "access_denied", "state": state})
    assert r.status_code == 403
    assert "access_denied" in r.text


def test_login_attempts_rate_limited(
    store: None, oidc_env: None, idp: None, web_client: TestClient
) -> None:
    from cod_doc.api.auth import FAILURES_PER_WINDOW

    web_client.get("/auth/oidc/login", follow_redirects=False)
    codes = []
    for _ in range(FAILURES_PER_WINDOW + 1):
        r = web_client.get("/auth/oidc/callback", params={"code": "good-code", "state": "forged"})
        codes.append(r.status_code)
    assert codes[:FAILURES_PER_WINDOW] == [403] * FAILURES_PER_WINDOW
    assert codes[-1] == 429
    blocked = web_client.get("/auth/oidc/callback", params={"code": "good-code", "state": "forged"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


async def test_websocket_without_session_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from cod_doc.api.web_session import SessionGate

    # фича включена: без env гейт пропустил бы websocket как до 0056
    monkeypatch.setenv("COD_DOC_OIDC_ISSUER", "https://idp.example")
    monkeypatch.setenv("COD_DOC_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("COD_DOC_OIDC_CLIENT_SECRET", "sec")

    async def app(scope: Any, receive: Any, send: Any) -> None:
        pytest.fail("гейт пропустил websocket без сессии")

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    acc.init_store()
    try:
        gate = SessionGate(app, protected=lambda _p: True)
        scope = {
            "type": "websocket",
            "path": "/ws/projects/x",
            "headers": [],
            "client": ("1.2.3.4", 1),
        }
        await gate(scope, receive, send)
    finally:
        acc._ENGINES.clear()
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_web_session_path_classification() -> None:
    from cod_doc.api.web_session import is_web_session_path

    assert is_web_session_path("/")
    assert is_web_session_path("/p/demo")
    assert is_web_session_path("/settings")
    assert is_web_session_path("/ws/projects/x")
    assert not is_web_session_path("/api/v1/x")
    assert not is_web_session_path("/api/health")
    assert not is_web_session_path("/auth/oidc/login")
    assert not is_web_session_path("/static/js/app.js")
    # границы сегментов (ревью #282): похожий префикс — обычная веб-страница
    assert is_web_session_path("/apiary")
    assert is_web_session_path("/staticfoo")
    assert is_web_session_path("/authentication")
