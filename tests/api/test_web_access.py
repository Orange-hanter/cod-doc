"""F1 аудита 0056: веб под OIDC-сессией пускает только в свои проекты и по роли.

Два слоя: чистое правило ``web_access.decide`` и сквозной флоу через
SessionGate — логин, страницы двух проектов реестра, запись, журнал отказов.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cod_doc.api import web_access
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import access_service as acc
from cod_doc.services import oidc_service, question_service
from cod_doc.services.request_identity import Identity

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

ADMIN = "human:root"
SUB = "sub-alice-1"
_IDP_DISCOVERY = {
    "authorization_endpoint": "https://idp.example/authorize",
    "token_endpoint": "https://idp.example/token",
    "userinfo_endpoint": "https://idp.example/userinfo",
}


# --- чистое правило ----------------------------------------------------------


def _who(**roles: str) -> Identity:
    return Identity(
        actor_uid="a1",
        handle="alice",
        kind="human",
        token_label="oidc",
        authors=MappingProxyType({"alice": "human"}),
        roles=MappingProxyType(roles),
    )


_UIDS = {"demo": "u-demo", "other": "u-other"}


def _decide(identity: Identity, method: str, path: str, **kw: Any) -> web_access.Verdict:
    return web_access.decide(
        identity,
        method=method,
        path=path,
        websocket=kw.pop("websocket", False),
        uid_of=_UIDS.get,
        registry=lambda: _UIDS,
        **kw,
    )


def test_project_paths_classified() -> None:
    assert web_access.project_of("/p/demo") == "demo"
    assert web_access.project_of("/p/demo/questions/Q-1/resolve") == "demo"
    assert web_access.project_of("/ws/projects/demo") == "demo"
    assert web_access.project_of("/projects/demo/tasks") == "demo"
    assert web_access.project_of("/palette", "project=demo&q=x") == "demo"
    for node_page in ("/", "/settings", "/projects", "/palette", "/standards", "/pdemo"):
        assert web_access.project_of(node_page) is None, node_page


def test_member_reads_own_project_only() -> None:
    alice = _who(**{"u-demo": "reader"})
    assert _decide(alice, "GET", "/p/demo/docs") == web_access.Allow(project="demo")
    assert _decide(alice, "GET", "/palette", query="project=demo") == web_access.Allow(
        project="demo"
    )
    # повтор project: маршрут прочтёт последнее значение, проверено было бы первое
    for query in ("project=demo&project=other", "project=&project=other"):
        assert isinstance(_decide(alice, "GET", "/palette", query=query), web_access.Deny)
    for path in ("/p/other", "/ws/projects/other", "/projects/other/tasks", "/p/ghost"):
        assert isinstance(
            _decide(alice, "GET", path, websocket=path.startswith("/ws")), web_access.Deny
        ), path


@pytest.mark.parametrize(
    ("role", "method", "path", "allowed"),
    [
        ("reader", "POST", "/p/demo/questions/Q-1/resolve", False),
        ("responder", "POST", "/p/demo/questions/Q-1/resolve", True),
        ("responder", "POST", "/p/demo/questions/Q-1/options", True),
        ("responder", "POST", "/p/demo/questions/Q-1/options/2", False),
        ("responder", "POST", "/p/demo/questions/Q-1/drop", False),
        ("responder", "POST", "/p/demo/tasks/T-1/status", False),
        ("responder", "DELETE", "/p/demo/docs/x", False),
        ("writer", "POST", "/p/demo/tasks/T-1/status", True),
        ("admin", "POST", "/p/demo/routines/x/run", True),
    ],
)
def test_write_by_role(role: str, method: str, path: str, *, allowed: bool) -> None:
    verdict = _decide(_who(**{"u-demo": role}), method, path)
    assert isinstance(verdict, web_access.Allow) is allowed


def test_websocket_feed_is_read_run_is_write() -> None:
    reader = _who(**{"u-demo": "reader"})
    assert isinstance(_decide(reader, "GET", "/ws/projects/demo", websocket=True), web_access.Allow)
    assert isinstance(
        _decide(reader, "GET", "/ws/projects/demo/run", websocket=True), web_access.Deny
    )


def test_node_pages_operator_only_member_sees_own_index() -> None:
    operator = _who(**{"u-demo": "admin", "u-other": "admin"})
    for path in ("/", "/settings", "/standards", "/projects", "/palette"):
        assert _decide(operator, "GET", path) == web_access.Allow(), path
    # admin одного проекта — не оператор узла: настройки правят общий конфиг
    half = _who(**{"u-demo": "admin"})
    assert _decide(half, "GET", "/") == web_access.Allow(visible=frozenset({"demo"}))
    assert isinstance(_decide(half, "GET", "/settings"), web_access.Deny)
    assert isinstance(_decide(half, "POST", "/settings"), web_access.Deny)
    assert isinstance(_decide(_who(), "GET", "/"), web_access.Deny)


def test_unreadable_or_empty_registry_has_no_operator() -> None:
    """Сбой чтения реестра не делает оператором узла (fail-closed)."""
    admin_demo = _who(**{"u-demo": "admin"})
    for projects in ({}, {"demo": "u-demo", "broken": None}):
        verdict = web_access.decide(
            admin_demo,
            method="GET",
            path="/settings",
            websocket=False,
            uid_of=_UIDS.get,
            registry=lambda projects=projects: projects,
        )
        assert isinstance(verdict, web_access.Deny), projects


# --- сквозной флоу через SessionGate ----------------------------------------


@pytest.fixture(autouse=True)
def _reset_gate() -> Iterator[None]:
    from cod_doc.api.web.pages import auth as auth_page
    from cod_doc.api.web_session import _GATE_CACHE

    auth_page._FAILS._hits.clear()
    _GATE_CACHE.clear()
    oidc_service._DISCOVERY_CACHE.clear()
    yield
    auth_page._FAILS._hits.clear()
    _GATE_CACHE.clear()
    oidc_service._DISCOVERY_CACHE.clear()


def _register(tmp_path: Path, migrate_db: Callable[[Path], None], slug: str) -> tuple[str, Path]:
    repo = tmp_path / f"{slug}-repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)
    entry = ProjectEntry(name=slug, path=str(repo))
    Config.load().add_project(entry)
    Project(entry).init()
    engine = make_engine(f"sqlite:///{db_path}")
    with transactional(make_session_factory(engine)) as session:
        ProjectRepository(session).add(
            ProjectEntity(slug=slug, title=slug, root_path=str(repo), config={})
        )
    engine.dispose()
    return acc.project_uid_for(entry), db_path


@pytest.fixture
def projects(tmp_path: Path, migrate_db: Callable[[Path], None]) -> dict[str, tuple[str, Path]]:
    return {s: _register(tmp_path, migrate_db, s) for s in ("demo", "other")}


@pytest.fixture
def gate_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Файл конфига (его пишет реестр проектов) перекрывает env: OIDC — в файл.
    cfg = Config.load()
    cfg.oidc_issuer = "https://idp.example"
    cfg.oidc_client_id = "cid"
    cfg.oidc_client_secret = "sec"
    cfg.oidc_redirect_uri = "https://cod-doc.example.dev/auth/oidc/callback"
    cfg.save()
    monkeypatch.setattr(oidc_service, "discover", lambda cfg: dict(_IDP_DISCOVERY))
    monkeypatch.setattr(oidc_service, "exchange_code", lambda *a, **k: "access-token")
    monkeypatch.setattr(oidc_service, "fetch_userinfo", lambda d, at: {"sub": SUB})
    acc.init_store()
    yield
    acc._ENGINES.clear()


def _member(roles: dict[str, str]) -> None:
    factory = acc.open_store()
    assert factory is not None
    with transactional(factory) as s:
        for uid, role in roles.items():
            acc.add_member(s, handle="alice", project_uid=uid, role=role, author=ADMIN)
        acc.oidc_link(s, handle="alice", sub=SUB, author=ADMIN)


@pytest.fixture
def client() -> Iterator[TestClient]:
    from cod_doc.api.server import app

    with TestClient(app, follow_redirects=False) as c:
        c.get("/auth/oidc/login", params={"next": "/"})
        state = c.cookies[oidc_service.STATE_COOKIE]
        r = c.get("/auth/oidc/callback", params={"code": "c", "state": state})
        assert r.status_code == 303, r.text
        yield c


def _denials() -> list[dict[str, Any]]:
    factory = acc.open_store()
    assert factory is not None
    with factory() as s:
        rows = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "auth.denied")
        ).all()
        return [r.payload for r in rows]


def _question(db_path: Path) -> str:
    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with transactional(make_session_factory(engine)) as session:
            project = ProjectRepository(session).get_by_slug("demo")
            assert project is not None
            assert project.row_id is not None
            pid = project.row_id
            q = question_service.create(
                session, project_id=pid, title="Что выбрать?", question="?", author=ADMIN
            )
            return q.question_id
    finally:
        engine.dispose()


def _authors(db_path: Path) -> list[str]:
    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with make_session_factory(engine)() as session:
            return [str(a) for a in session.scalars(select(ActivityEventModel.actor_id)).all()]
    finally:
        engine.dispose()


def test_responder_sees_own_project_answers_and_nothing_else(
    projects: dict[str, tuple[str, Path]], gate_on: None, request: pytest.FixtureRequest
) -> None:
    demo_uid, demo_db = projects["demo"]
    _member({demo_uid: "responder"})
    qid = _question(demo_db)
    c: TestClient = request.getfixturevalue("client")

    index = c.get("/")
    assert index.status_code == 200
    assert 'href="/p/demo"' in index.text
    assert 'href="/p/other"' not in index.text  # чужой проект в индексе не виден
    assert c.get("/p/demo/questions").status_code == 200
    for path in ("/p/other", "/p/other/questions", "/settings", "/projects"):
        r = c.get(path)
        assert r.status_code == 403, path
        assert "Нет доступа" in r.text
        assert 'class="leg leg-a"' in r.text  # отказ гейта — та же страница с котом
        assert 'href="/auth/logout"' in r.text
    assert c.post("/p/demo/questions/" + qid + "/drop").status_code == 403

    r = c.post(f"/p/demo/questions/{qid}/resolve", data={"resolution": "Берём A"})
    assert r.status_code == 303, r.text
    assert any("alice" in a for a in _authors(demo_db))  # ответ подписан владельцем сессии

    denied = _denials()
    assert {d["code"] for d in denied} == {"SYNC-202"}
    assert {d["path"] for d in denied} >= {"/p/other", "/settings", f"/p/demo/questions/{qid}/drop"}


def test_operator_keeps_node_pages(
    projects: dict[str, tuple[str, Path]], gate_on: None, request: pytest.FixtureRequest
) -> None:
    _member({uid: "admin" for uid, _db in projects.values()})
    c: TestClient = request.getfixturevalue("client")
    assert c.get("/").status_code == 200
    assert c.get("/settings").status_code == 200
    assert c.get("/p/other").status_code == 200
    assert _denials() == []


def test_removed_membership_closes_project_on_next_request(
    projects: dict[str, tuple[str, Path]], gate_on: None, request: pytest.FixtureRequest
) -> None:
    demo_uid, _db = projects["demo"]
    _member({demo_uid: "writer"})
    c: TestClient = request.getfixturevalue("client")
    assert c.get("/p/demo").status_code == 200
    factory = acc.open_store()
    assert factory is not None
    with transactional(factory) as s:
        acc.remove_member(s, handle="alice", project_uid=demo_uid, author=ADMIN)
    assert c.get("/p/demo").status_code == 403
