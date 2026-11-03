"""0056: OIDC веб-логин — привязка sub→actor, кукисессия, ключ подписи.

Сеть (discovery/обмен/userinfo) не мокается — её не зовут: тестируются
чистые функции (`authorize_url`, `pkce_pair`, sign/verify) и доступ к
хранилищу (`oidc_link`, `authenticate_by_sub`, `session_key`).
"""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.config import Config
from cod_doc.infra.db import transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.services import access_service as acc
from cod_doc.services import oidc_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import sessionmaker

ADMIN = "human:root"
SUB = "sub-alice-1"

_DISCOVERY = {
    "authorization_endpoint": "https://idp.example/authorize",
    "token_endpoint": "https://idp.example/token",
    "userinfo_endpoint": "https://idp.example/userinfo",
}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[sessionmaker]:  # type: ignore[type-arg]
    url = f"sqlite:///{tmp_path / 'access.db'}"
    acc.init_store(url)
    factory = acc.open_store(url)
    assert factory is not None
    yield factory
    acc._ENGINES.clear()


def _link_alice(factory: sessionmaker) -> None:  # type: ignore[type-arg]
    with transactional(factory) as s:
        acc.add_member(s, handle="alice", project_uid="p1", role="admin", author=ADMIN)
        acc.oidc_link(s, handle="alice", sub=SUB, author=ADMIN)


# --- конфиг -------------------------------------------------------------------


def test_configured_requires_all_three() -> None:
    assert not oidc_service.oidc_configured(Config())
    assert oidc_service.oidc_configured(
        Config(oidc_issuer="https://i", oidc_client_id="c", oidc_client_secret="s")
    )
    assert not oidc_service.oidc_configured(
        Config(oidc_issuer="https://i", oidc_client_id="c", oidc_client_secret="")
    )


def test_client_secret_is_declared_secret() -> None:
    from cod_doc.config import SECRET_FIELDS

    assert "oidc_client_secret" in SECRET_FIELDS


# --- привязка и вход -----------------------------------------------------------


def test_link_then_login_by_sub(store: sessionmaker) -> None:  # type: ignore[type-arg]
    _link_alice(store)
    with store() as s:
        identity = acc.authenticate_by_sub(s, SUB)
    assert identity is not None
    assert identity.handle == "alice"
    assert identity.kind == acc.KIND_HUMAN
    assert identity.token_label == acc.OIDC_LABEL
    assert identity.roles.get("p1") == "admin"
    assert identity.authors["alice"] == acc.KIND_HUMAN


def test_unknown_and_unlinked_sub_rejected(store: sessionmaker) -> None:  # type: ignore[type-arg]
    _link_alice(store)
    with store() as s:
        assert acc.authenticate_by_sub(s, "nobody") is None
        acc.oidc_unlink(s, handle="alice", author=ADMIN)
        assert acc.authenticate_by_sub(s, SUB) is None


def test_disabled_actor_rejected(store: sessionmaker) -> None:  # type: ignore[type-arg]
    _link_alice(store)
    with transactional(store) as s:
        acc.disable_actor(s, handle="alice", author=ADMIN)
    with store() as s:
        assert acc.authenticate_by_sub(s, SUB) is None


def test_agent_cannot_be_linked(store: sessionmaker) -> None:  # type: ignore[type-arg]
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid="p1", role="admin", author=ADMIN)
        acc.add_agent(s, owner="alice", name="bot", author=ADMIN)
        with pytest.raises(acc.AccessError, match="только человеку"):
            acc.oidc_link(s, handle="alice/bot", sub=SUB, author=ADMIN)
        with pytest.raises(acc.AccessError, match="пробел"):
            acc.oidc_link(s, handle="alice", sub="two words", author=ADMIN)


def test_sub_taken_by_other_actor(store: sessionmaker) -> None:  # type: ignore[type-arg]
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid="p1", role="admin", author=ADMIN)
        acc.add_member(s, handle="bob", project_uid="p1", role="admin", author=ADMIN)
        acc.oidc_link(s, handle="alice", sub=SUB, author=ADMIN)
        with pytest.raises(acc.AccessError, match="уже привязан"):
            acc.oidc_link(s, handle="bob", sub=SUB, author=ADMIN)
        # тот же актёр, тот же sub — идемпотентно
        assert acc.oidc_link(s, handle="alice", sub=SUB, author=ADMIN) is None


def test_record_login_writes_event(store: sessionmaker) -> None:  # type: ignore[type-arg]
    _link_alice(store)
    with transactional(store) as s:
        acc.record_login(s, handle="alice", ip="203.0.113.5")
    with store() as s:
        events = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "access.login")
        ).all()
    assert [e.payload for e in events] == [{"ip": "203.0.113.5"}]


# --- кукисессия ----------------------------------------------------------------


def test_session_roundtrip_and_tamper() -> None:
    key = b"k" * 32
    value = oidc_service.issue_session_value(key, actor_uid="u1", handle="alice")
    payload = oidc_service.read_session(key, value)
    assert payload is not None
    assert payload["handle"] == "alice"
    assert payload["actor_uid"] == "u1"
    assert payload["exp"] > 0
    assert oidc_service.read_session(b"other-key".ljust(32, b"-"), value) is None
    assert oidc_service.read_session(key, value[:-2] + "xx") is None
    assert oidc_service.read_session(key, None) is None
    assert oidc_service.read_session(key, "garbage") is None


def test_expired_session_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_service, "SESSION_TTL", -10)
    value = oidc_service.issue_session_value(b"k" * 32, actor_uid="u", handle="h")
    assert oidc_service.read_session(b"k" * 32, value) is None


def test_session_key_stable_per_store(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'access.db'}"
    acc.init_store(url)
    factory = acc.open_store(url)
    assert factory is not None
    try:
        key = oidc_service.session_key(factory)
        assert len(key) == 32
        assert oidc_service.session_key(factory) == key
    finally:
        acc._ENGINES.clear()
    # новое хранилище — новый ключ
    url2 = f"sqlite:///{tmp_path / 'other.db'}"
    acc.init_store(url2)
    factory2 = acc.open_store(url2)
    assert factory2 is not None
    try:
        assert oidc_service.session_key(factory2) != key
    finally:
        acc._ENGINES.clear()


# --- PKCE и authorize URL --------------------------------------------------------


def test_pkce_pair_matches_s256() -> None:
    verifier, challenge = oidc_service.pkce_pair()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected
    assert oidc_service.pkce_pair()[0] != verifier


def test_authorize_url_contains_oidc_params() -> None:
    cfg = Config(
        oidc_issuer="https://idp.example",
        oidc_client_id="cid",
        oidc_client_secret="sec",
        oidc_redirect_uri="https://app.example/auth/oidc/callback",
    )
    url = oidc_service.authorize_url(cfg, _DISCOVERY, state="st", challenge="ch")
    assert url.startswith("https://idp.example/authorize?")
    for part in (
        "response_type=code",
        "client_id=cid",
        "redirect_uri=https%3A%2F%2Fapp.example%2Fauth%2Foidc%2Fcallback",
        "scope=openid+profile+email",
        "state=st",
        "code_challenge=ch",
        "code_challenge_method=S256",
    ):
        assert part in url


def test_authorize_url_requires_redirect_uri() -> None:
    cfg = Config(oidc_issuer="https://i", oidc_client_id="c", oidc_client_secret="s")
    with pytest.raises(oidc_service.OidcError, match="REDIRECT_URI"):
        oidc_service.authorize_url(cfg, _DISCOVERY, state="st", challenge="ch")
    with pytest.raises(oidc_service.OidcError, match="authorization_endpoint"):
        oidc_service.authorize_url(cfg, {}, state="st", challenge="ch")
