"""CLO-034: хранилище доступа — actor, членство, токены (RFC 29 §3.8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import transactional
from cod_doc.infra.models import ActivityEventModel, ApiTokenModel
from cod_doc.services import access_service as acc
from cod_doc.services.access_service import AccessError

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

ADMIN = "human:root"
P1 = "proj-uid-1"
P2 = "proj-uid-2"


@pytest.fixture
def store(tmp_path: Path) -> sessionmaker[Session]:
    url = acc.init_store(f"sqlite:///{tmp_path / 'access.db'}")
    factory = acc.open_store(url)
    assert factory is not None
    return factory


def test_no_store_file_means_gate_off(tmp_path: Path) -> None:
    assert acc.open_store(f"sqlite:///{tmp_path / 'absent.db'}") is None


def test_default_store_lives_in_cod_doc_home(tmp_path: Path) -> None:
    # autouse-фикстура подменила COD_DOC_HOME на tmp
    assert acc.store_url().endswith(f"/{acc.STORE_FILENAME}")
    assert acc.open_store() is None


def test_init_is_idempotent_and_file_is_private(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'access.db'}"
    acc.init_store(url)
    acc.init_store(url)
    assert (tmp_path / "access.db").stat().st_mode & 0o077 == 0


def test_token_stored_as_hash_and_shown_once(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P1, role="writer", author=ADMIN)
        issued = acc.create_token(s, handle="alice", label="macbook", author=ADMIN)
    assert issued.token.startswith(acc.TOKEN_PREFIX)
    with store() as s:
        row = s.scalars(select(ApiTokenModel)).one()
        assert row.token_hash == acc.hash_token(issued.token)
        assert issued.token not in {row.token_hash, row.label, row.uid}
        listed = acc.list_tokens(s)
    assert [(t.handle, t.label) for t in listed] == [("alice", "macbook")]
    assert not hasattr(listed[0], "token")


def test_authenticate_resolves_roles_and_authors(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P1, role="writer", author=ADMIN)
        acc.add_member(s, handle="alice", project_uid=P2, role="reader", author=ADMIN)
        acc.add_agent(s, owner="alice", name="claude-code", author=ADMIN)
        issued = acc.create_token(s, handle="alice", label="macbook", author=ADMIN)
    with transactional(store) as s:
        ident = acc.authenticate(s, issued.token)
    assert ident is not None
    assert ident.handle == "alice"
    assert dict(ident.roles) == {P1: "writer", P2: "reader"}
    assert ident.authors == {"alice", "alice/claude-code"}


def test_unknown_or_malformed_token_rejected(store: sessionmaker[Session]) -> None:
    with store() as s:
        assert acc.authenticate(s, "cdt_nope") is None
        assert acc.authenticate(s, "Bearer something") is None


def test_revoked_token_rejected_immediately(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P1, role="writer", author=ADMIN)
        issued = acc.create_token(s, handle="alice", label="ci", author=ADMIN)
    with transactional(store) as s:
        assert acc.authenticate(s, issued.token) is not None
        assert acc.revoke_token(s, token_uid=issued.uid[:12], author=ADMIN) is True
    with transactional(store) as s:
        assert acc.authenticate(s, issued.token) is None
        assert acc.revoke_token(s, token_uid=issued.uid, author=ADMIN) is False


def test_disabled_owner_disables_agent_tokens(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P1, role="writer", author=ADMIN)
        agent = acc.add_agent(s, owner="alice", name="bot", author=ADMIN)
        issued = acc.create_token(s, handle=agent, label="cursor", author=ADMIN)
    with transactional(store) as s:
        assert acc.authenticate(s, issued.token) is not None
        acc.disable_actor(s, handle="alice", author=ADMIN)
    with transactional(store) as s:
        assert acc.authenticate(s, issued.token) is None


def test_agent_never_above_owner(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P1, role="writer", author=ADMIN)
        agent = acc.add_agent(s, owner="alice", name="bot", author=ADMIN)
        with pytest.raises(AccessError, match="не выше владельца"):
            acc.add_member(s, handle=agent, project_uid=P1, role="admin", author=ADMIN)
        with pytest.raises(AccessError, match="не выше владельца"):
            acc.add_member(s, handle=agent, project_uid=P2, role="reader", author=ADMIN)


def test_agent_inherits_owner_role_narrowed_by_own(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P1, role="admin", author=ADMIN)
        acc.add_member(s, handle="alice", project_uid=P2, role="writer", author=ADMIN)
        agent = acc.add_agent(s, owner="alice", name="bot", author=ADMIN)
        acc.add_member(s, handle=agent, project_uid=P1, role="reader", author=ADMIN)
        issued = acc.create_token(s, handle=agent, label="cursor", author=ADMIN)
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P2, role="reader", author=ADMIN)
    with transactional(store) as s:
        ident = acc.authenticate(s, issued.token)
    assert ident is not None
    # P1 сужен своим членством; P2 — роль владельца, понизившаяся после выдачи
    assert dict(ident.roles) == {P1: "reader", P2: "reader"}
    assert ident.authors == {agent}


def test_every_mutation_leaves_activity_event(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.add_member(s, handle="alice", project_uid=P1, role="writer", author=ADMIN)
        acc.add_agent(s, owner="alice", name="bot", author=ADMIN)
        issued = acc.create_token(s, handle="alice", label="ci", author=ADMIN)
        acc.revoke_token(s, token_uid=issued.uid, author=ADMIN)
        acc.remove_member(s, handle="alice", project_uid=P1, author=ADMIN)
        acc.disable_actor(s, handle="alice", author=ADMIN)
    with store() as s:
        kinds = s.scalars(select(ActivityEventModel.kind).order_by(ActivityEventModel.row_id)).all()
        payloads = s.scalars(select(ActivityEventModel.payload)).all()
    assert kinds == [
        "access.actor_created",
        "access.member_set",
        "access.actor_created",
        "access.token_created",
        "access.token_revoked",
        "access.member_removed",
        "access.actor_disabled",
    ]
    assert all(issued.token not in str(p) for p in payloads)


def test_record_denied_writes_auth_denied(store: sessionmaker[Session]) -> None:
    with transactional(store) as s:
        acc.record_denied(s, code="SYNC-201", ip="203.0.113.7", path="/mcp")
    with store() as s:
        ev = s.scalars(select(ActivityEventModel)).one()
    assert ev.kind == "auth.denied"
    assert ev.payload["code"] == "SYNC-201"
    assert ev.payload["ip"] == "203.0.113.7"


@pytest.mark.parametrize("bad", ["Alice", "a b", "", "x" * 70, "a/b"])
def test_handle_validation(store: sessionmaker[Session], bad: str) -> None:
    with transactional(store) as s, pytest.raises(AccessError):
        acc.add_member(s, handle=bad, project_uid=P1, role="reader", author=ADMIN)


def test_unknown_role_rejected(store: sessionmaker[Session]) -> None:
    with transactional(store) as s, pytest.raises(AccessError, match="роль"):
        acc.add_member(s, handle="alice", project_uid=P1, role="owner", author=ADMIN)
