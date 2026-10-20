"""CLO-037 / SCN-084: роли и проверка ``actor``/``author`` на push умного remote.

Настоящий сервер ``/api/sync/v1`` за Bearer-гейтом (``tests/infra/remote/_https_server``)
и настоящий клиент ``HttpRemote``: reader не двигает ref и не кладёт объекты,
writer не кладёт changeset с чужим ``author`` или чужим ``actor``, ``refs/main``
после отказов не изменился. Плюс маршруты доступа (CLO-038): admin выдаёт роли,
токен выпускается только себе или своему агенту.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.infra.remote.errors import RemoteAuthError
from cod_doc.infra.remote.http import HttpRemote
from cod_doc.services import access_service
from cod_doc.services.sync_service import changeset, push
from cod_doc.services.sync_service.changeset import Changeset, Op
from tests.infra.remote._https_server import base_url, snapshot_bytes
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

PROJECT = "0199f2b0-7c3a-7000-8000-0000000000ff"
_REPLICA = "0199f2b0-7c3a-7000-8000-000000000001"


def _token(handle: str, role: str | None, project: str = PROJECT) -> str:
    access_service.init_store()
    with access_service.store_session() as s:
        if role is not None:
            access_service.add_member(
                s, handle=handle, project_uid=project, role=role, author="human:root"
            )
        return access_service.create_token(s, handle=handle, label="t", author="human:root").token


def _remote(token: str | None) -> HttpRemote:
    return HttpRemote(base_url(), token=token)


def _changeset(actor: str, author: str, seed: str = "x") -> tuple[str, bytes]:
    hlc = f"{1760000000001:013d}.0000.{_REPLICA}"
    cs = Changeset(
        project=PROJECT,
        parents=(),
        replica=_REPLICA,
        actor=actor,
        epoch="0050_sync_rank",
        schema_head="0050_sync_rank",
        hlc_max=hlc,
        digest="d" * 64,
        ops=(
            Op(
                op_id="01K7Z" + hashlib.sha256(seed.encode()).hexdigest()[:21].upper(),
                hlc=hlc,
                entity_kind="task",
                entity_uid="0199f2b0-7c3a-7000-8000-0000000000aa",
                kind="upsert",
                fields={"title": seed},
                base={},
                author=author,
            ),
        ),
    )
    return changeset.sha(cs), changeset.encode(cs)


def test_scn_084_reader_cannot_push(tmp_path: Path) -> None:
    """Шаг 1: реплика под токеном reader — push отвергнут 403, ref не создан."""
    a = r.replica_a(tmp_path)
    with transactional(a.sf) as s:
        uid = s.scalars(select(ProjectModel.uid).where(ProjectModel.row_id == a.project_id)).one()
    assert uid is not None
    store = _remote(_token("dakh", "reader", project=uid))
    r.make_task(a, "DEM-001")
    with pytest.raises(RemoteAuthError) as exc:
        push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)
    assert exc.value.code == "SYNC-202"
    assert store.get_ref(uid, "main") is None  # reader читает, но двигать не может


def test_scn_084_foreign_author_rejected() -> None:
    """Шаг 2: writer alice кладёт changeset, где author операции — bob: 422 SYNC-203."""
    alice = _remote(_token("alice", "writer"))
    _token("bob", "writer")
    sha, data = _changeset("human:alice", "human:bob")
    with pytest.raises(RemoteAuthError) as exc:
        alice.put_object(PROJECT, sha, data)
    assert exc.value.code == "SYNC-203"
    assert "bob" in str(exc.value)
    assert alice.has_objects(PROJECT, [sha]) == set()
    assert alice.get_ref(PROJECT, "main") is None


def test_actor_must_be_token_owner() -> None:
    alice = _remote(_token("alice", "writer"))
    sha, data = _changeset("human:bob", "human:alice")
    with pytest.raises(RemoteAuthError) as exc:
        alice.put_object(PROJECT, sha, data)
    assert exc.value.code == "SYNC-203"


def test_own_agent_author_accepted_kind_must_match() -> None:
    alice = _remote(_token("alice", "writer"))
    with access_service.store_session() as s:
        access_service.add_agent(s, owner="alice", name="claude", author="human:root")
    sha, data = _changeset("human:alice", "agent:alice/claude", seed="ok")
    alice.put_object(PROJECT, sha, data)
    assert alice.has_objects(PROJECT, [sha]) == {sha}
    sha, data = _changeset("human:alice", "system:alice", seed="spoof")
    with pytest.raises(RemoteAuthError):
        alice.put_object(PROJECT, sha, data)


def test_garbage_object_is_rejected_not_stored() -> None:
    alice = _remote(_token("alice", "writer"))
    sha = hashlib.sha256(b"x").hexdigest()
    with pytest.raises(Exception, match="SYNC-102"):
        alice.put_object(PROJECT, sha, b"not a changeset")
    assert alice.has_objects(PROJECT, [sha]) == set()


def test_non_member_and_no_token() -> None:
    stranger = _remote(_token("mallory", "admin", project="0199f2b0-7c3a-7000-8000-0000000000ee"))
    with pytest.raises(RemoteAuthError) as exc:
        stranger.get_ref(PROJECT, "main")
    assert exc.value.code == "SYNC-202"
    with pytest.raises(RemoteAuthError) as exc:
        _remote(None).get_ref(PROJECT, "main")
    assert exc.value.code == "SYNC-201"


def test_schema_min_admin_only() -> None:
    writer = _remote(_token("alice", "writer"))
    assert writer.cas_json(PROJECT, "meta.json", None, {"v": 1, "schema_min": "0050"}) is not None
    with pytest.raises(RemoteAuthError):
        writer.admin("PUT", PROJECT, "meta", body={"schema_min": "0053"})
    admin = _remote(_token("root", "admin"))
    meta = admin.admin("PUT", PROJECT, "meta", body={"schema_min": "0053"})
    assert isinstance(meta, dict)
    assert meta["schema_min"] == "0053"
    found = writer.get_json(PROJECT, "meta.json")
    assert found is not None
    assert found[0]["schema_min"] == "0053"


def test_admin_routes_members_and_own_tokens() -> None:
    admin = _remote(_token("root", "admin"))
    writer = _remote(_token("alice", "writer"))
    row = admin.admin("PUT", PROJECT, "members", "carol", body={"role": "reader"})
    assert row == {"handle": "carol", "kind": "human", "role": "reader"}
    handles = {m["handle"] for m in admin.admin("GET", PROJECT, "members")}  # type: ignore[union-attr]
    assert {"root", "alice", "carol"} <= handles
    with pytest.raises(RemoteAuthError):
        writer.admin("GET", PROJECT, "members")
    # токен — только себе или своему агенту
    issued = admin.admin("POST", PROJECT, "tokens", body={"label": "laptop"})
    assert isinstance(issued, dict)
    assert issued["handle"] == "root"
    assert issued["token"].startswith(access_service.TOKEN_PREFIX)
    with pytest.raises(RemoteAuthError):
        admin.admin("POST", PROJECT, "tokens", body={"label": "x", "handle": "carol"})
    # новым токеном можно ходить, после отзыва — нельзя
    fresh = _remote(issued["token"])
    assert fresh.get_ref(PROJECT, "main") is None
    assert admin.admin("DELETE", PROJECT, "tokens", issued["uid"]) is None
    with pytest.raises(RemoteAuthError):
        fresh.get_ref(PROJECT, "main")
    assert admin.admin("DELETE", PROJECT, "members", "carol") is None


def test_agent_token_cannot_administer() -> None:
    _token("root", "admin")
    with access_service.store_session() as s:
        agent = access_service.add_agent(s, owner="root", name="bot", author="human:root")
        tok = access_service.create_token(s, handle=agent, label="a", author="human:root").token
    bot = _remote(tok)
    assert bot.get_ref(PROJECT, "main") is None  # роль владельца у агента есть
    for method, parts, body in (
        ("POST", ("tokens",), {"label": "self-issued"}),
        ("GET", ("members",), None),
        ("PUT", ("members", "eve"), {"role": "admin"}),
    ):
        with pytest.raises(RemoteAuthError, match="человек"):
            bot.admin(method, PROJECT, *parts, body=body)


def test_actor_kind_must_match_owner() -> None:
    alice = _remote(_token("alice", "writer"))
    sha, data = _changeset("agent:alice", "human:alice", seed="kind")
    with pytest.raises(RemoteAuthError) as exc:
        alice.put_object(PROJECT, sha, data)
    assert exc.value.code == "SYNC-203"


def test_snapshot_needs_known_head() -> None:
    from cod_doc.infra.remote.base import SnapshotRef, snapshot_key

    alice = _remote(_token("alice", "writer"))
    ghost = hashlib.sha256(b"ghost").hexdigest()
    ref = SnapshotRef(epoch="0050_sync_rank", sha=ghost, key=snapshot_key("0050_sync_rank", ghost))
    with pytest.raises(Exception, match="SYNC-102"):
        alice.put_snapshot(PROJECT, ref, b"forged db")
    assert alice.latest_snapshot(PROJECT) is None
    sha, data = _changeset("human:alice", "human:alice", seed="head")
    alice.put_object(PROJECT, sha, data)
    real = SnapshotRef(epoch="0050_sync_rank", sha=sha, key=snapshot_key("0050_sync_rank", sha))
    # голова есть, но заголовок о другом проекте/эпохе — отказ
    for bad in (
        b"not a snapshot",
        snapshot_bytes("0199f2b0-7c3a-7000-8000-0000000000ee", "0050_sync_rank", sha),
        snapshot_bytes(PROJECT, "0053_sync_access", sha),
    ):
        with pytest.raises(Exception, match="SYNC-102"):
            alice.put_snapshot(PROJECT, real, bad)
    alice.put_snapshot(PROJECT, real, snapshot_bytes(PROJECT, "0050_sync_rank", sha))
    assert alice.latest_snapshot(PROJECT) == real


def test_body_caps_and_role_before_body() -> None:
    import httpx

    from cod_doc.infra.remote.file import MAX_JSON_BYTES

    writer = _token("alice", "writer")
    reader = _token("bob", "reader")
    url = f"{base_url()}/api/sync/v1/projects/{PROJECT}/json/leases/BIG"
    big = b"{" + b" " * (MAX_JSON_BYTES + 1) + b"}"
    auth_w = {"authorization": f"Bearer {writer}", "if-none-match": "*"}
    assert httpx.put(url, content=big, headers=auth_w).status_code == 413
    auth_r = {"authorization": f"Bearer {reader}", "if-none-match": "*"}
    r = httpx.put(url, content=b"{}", headers=auth_r)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SYNC-202"
