"""Контракт ``RemoteStore`` (RFC 29 §10.5, CLO-011) — общий для всех реализаций.

Набор параметризован реализацией: фабрика из ``FACTORIES`` получает
``tmp_path`` и возвращает URL, который понимает ``open_remote``. ``S3Remote``
(CLO-028) и ``HttpRemote`` (CLO-035) добавляют сюда свою строку и проходят те
же тесты, включая межпроцессную гонку CAS.

Гонка идёт через URL, а не через объект: каждый процесс открывает remote сам,
как это делают две реплики на разных машинах.

Строка ``s3`` (CLO-028) ходит в настоящее S3-совместимое хранилище: бакет —
``TEST_S3_BUCKET``, адрес и креды — стандартная цепочка AWS
(``AWS_ENDPOINT_URL``, ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``). Без
переменной бакета строка пропускается; в CI её гоняет джоба ``S3 remote
(MinIO)`` с ``TEST_S3_REQUIRED=1``, где пропуск — падение. Каждый тест получает свой префикс и убирает его за собой.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import queue
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.remote.base import RefValue, RemoteStore, SnapshotRef, open_remote
from cod_doc.infra.remote.errors import ObjectMissingError, RemoteConfigError
from cod_doc.services.sync_service import checkout_lease, leases
from cod_doc.services.sync_service.errors import CheckoutLeaseHeldError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from multiprocessing.synchronize import Barrier
    from pathlib import Path

    from cod_doc.infra.remote.base import JsonValue

PROJECT = "0199f2b0-7c3a-7000-8000-0000000000ff"
OTHER = "0199f2b0-7c3a-7000-8000-0000000000ee"


def _file_url(tmp_path: Path) -> str:
    return (tmp_path / "remote").as_uri()


#: Бакет для строки ``s3``; без него строка пропускается.
S3_BUCKET_ENV = "TEST_S3_BUCKET"
#: В CI строка ``s3`` обязана идти: пропуск при этом флаге — падение.
S3_REQUIRED_ENV = "TEST_S3_REQUIRED"


def _s3_url(tmp_path: Path) -> str:
    del tmp_path
    bucket = os.environ.get(S3_BUCKET_ENV, "").strip()
    if not bucket:
        if os.environ.get(S3_REQUIRED_ENV):
            pytest.fail(f"{S3_REQUIRED_ENV} задан, а {S3_BUCKET_ENV} нет")
        pytest.skip(f"{S3_BUCKET_ENV} не задан — S3-хранилища для контракта нет")
    return f"s3://{bucket}/contract-{uuid.uuid4().hex}"


def _https_url(tmp_path: Path) -> str:
    """Настоящий сервер ``/api/sync/v1`` за Bearer-гейтом (CLO-035); loopback — http."""
    del tmp_path
    from tests.infra.remote._https_server import provision

    return provision((PROJECT, OTHER))


FACTORIES: dict[str, Callable[[Path], str]] = {
    "file": _file_url,
    "s3": _s3_url,
    "https": _https_url,
}


def _object(remote_url: str, seed: str) -> tuple[str, bytes]:
    """Адрес и тело объекта. Умный remote хранит только changeset'ы владельца токена
    (CLO-037), поэтому ему — настоящий changeset; байтовым транспортам — любые байты."""
    if not remote_url.startswith("http"):
        return _sha(seed), b"CDC1payload"
    from cod_doc.services.sync_service import changeset
    from cod_doc.services.sync_service.changeset import Changeset, Op
    from tests.infra.remote._https_server import ACTOR

    replica = "0199f2b0-7c3a-7000-8000-000000000001"
    hlc = f"{1760000000001:013d}.0000.{replica}"
    cs = Changeset(
        project=PROJECT,
        parents=(),
        replica=replica,
        actor=ACTOR,
        epoch="0050_sync_rank",
        schema_head="0050_sync_rank",
        hlc_max=hlc,
        digest="d" * 64,
        ops=(
            Op(
                op_id=f"01K7Z{hashlib.sha256(seed.encode()).hexdigest()[:21].upper()}",
                hlc=hlc,
                entity_kind="task",
                entity_uid="0199f2b0-7c3a-7000-8000-0000000000aa",
                kind="upsert",
                fields={"title": seed},
                base={},
                author=f"human:{ACTOR}",
            ),
        ),
    )
    return changeset.sha(cs), changeset.encode(cs)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _drop_s3_prefix(url: str) -> None:
    """Убрать всё под префиксом теста: бакет может быть долгоживущим."""
    from cod_doc.infra.remote.s3 import S3Remote

    store = S3Remote.from_url(url)
    paginator = store.client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=store.bucket, Prefix=f"{store.prefix}/"):
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if keys:
            store.client.delete_objects(Bucket=store.bucket, Delete={"Objects": keys})


@pytest.fixture(params=sorted(FACTORIES))
def remote_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    url = FACTORIES[request.param](tmp_path)
    yield url
    if url.startswith("s3://"):
        _drop_s3_prefix(url)


@pytest.fixture
def remote(remote_url: str) -> RemoteStore:
    return open_remote(remote_url, credentials=None)


# --- refs -----------------------------------------------------------------------


def test_missing_ref_is_none(remote: RemoteStore) -> None:
    assert remote.get_ref(PROJECT, "main") is None


def test_cas_creates_ref_when_absent(remote: RemoteStore) -> None:
    head = _sha("c1")
    new = remote.cas_ref(PROJECT, "main", None, head)
    assert new is not None
    assert new.sha == head
    assert remote.get_ref(PROJECT, "main") == new


def test_cas_create_rejected_when_ref_exists(remote: RemoteStore) -> None:
    first = remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    assert remote.cas_ref(PROJECT, "main", None, _sha("c2")) is None
    assert remote.get_ref(PROJECT, "main") == first


def test_cas_advances_from_current_value(remote: RemoteStore) -> None:
    first = remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    second = remote.cas_ref(PROJECT, "main", first, _sha("c2"))
    assert second is not None
    assert second.sha == _sha("c2")
    assert second.etag != first.etag  # type: ignore[union-attr]
    assert remote.get_ref(PROJECT, "main") == second


def test_cas_rejects_stale_expected(remote: RemoteStore) -> None:
    first = remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    second = remote.cas_ref(PROJECT, "main", first, _sha("c2"))
    assert remote.cas_ref(PROJECT, "main", first, _sha("c3")) is None
    assert remote.get_ref(PROJECT, "main") == second


def test_cas_rejects_expected_when_ref_absent(remote: RemoteStore) -> None:
    ghost = RefValue(sha=_sha("c1"), etag="whatever")
    assert remote.cas_ref(PROJECT, "main", ghost, _sha("c2")) is None
    assert remote.get_ref(PROJECT, "main") is None


def test_refs_are_per_project_and_name(remote: RemoteStore) -> None:
    remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    assert remote.get_ref(OTHER, "main") is None
    assert remote.get_ref(PROJECT, "other") is None


def test_cas_rejects_malformed_sha(remote: RemoteStore) -> None:
    with pytest.raises(ValueError, match="sha"):
        remote.cas_ref(PROJECT, "main", None, "HEAD")


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", ".hidden", "x\x00"])
def test_rejects_unsafe_project_and_ref_names(remote: RemoteStore, bad: str) -> None:
    with pytest.raises(ValueError):
        remote.get_ref(bad, "main")
    with pytest.raises(ValueError):
        remote.get_ref(PROJECT, bad)


# --- objects --------------------------------------------------------------------


def test_put_get_has_objects(remote: RemoteStore, remote_url: str) -> None:
    a, data = _object(remote_url, "a")
    b = _sha("b")
    assert remote.has_objects(PROJECT, [a, b]) == set()
    remote.put_object(PROJECT, a, data)
    assert remote.has_objects(PROJECT, [a, b]) == {a}
    assert remote.has_objects(PROJECT, []) == set()
    assert remote.get_object(PROJECT, a) == data


def test_put_object_is_idempotent(remote: RemoteStore, remote_url: str) -> None:
    a, data = _object(remote_url, "a")
    remote.put_object(PROJECT, a, data)
    remote.put_object(PROJECT, a, data)
    assert remote.get_object(PROJECT, a) == data


def test_missing_object_is_sync_102(remote: RemoteStore) -> None:
    with pytest.raises(ObjectMissingError) as exc:
        remote.get_object(PROJECT, _sha("nope"))
    assert exc.value.code == "SYNC-102"


def test_objects_are_per_project(remote: RemoteStore, remote_url: str) -> None:
    a, data = _object(remote_url, "a")
    remote.put_object(PROJECT, a, data)
    assert remote.has_objects(OTHER, [a]) == set()


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, "../" + "a" * 61])
def test_object_api_rejects_malformed_sha(remote: RemoteStore, bad: str) -> None:
    with pytest.raises(ValueError):
        remote.put_object(PROJECT, bad, b"x")
    with pytest.raises(ValueError):
        remote.get_object(PROJECT, bad)
    with pytest.raises(ValueError):
        remote.has_objects(PROJECT, [bad])


# --- json (leases, meta) --------------------------------------------------------


def test_json_cas_lifecycle(remote: RemoteStore) -> None:
    key = "leases/ADO"
    assert remote.get_json(PROJECT, key) is None
    etag1 = remote.cas_json(PROJECT, key, None, {"next": 241, "holders": {}})
    assert etag1 is not None
    assert remote.get_json(PROJECT, key) == ({"next": 241, "holders": {}}, etag1)
    assert remote.cas_json(PROJECT, key, None, {"next": 1}) is None

    etag2 = remote.cas_json(PROJECT, key, etag1, {"next": 261, "holders": {"r": [241, 260]}})
    assert etag2 is not None
    assert etag2 != etag1
    assert remote.cas_json(PROJECT, key, etag1, {"next": 999}) is None
    assert remote.get_json(PROJECT, key) == ({"next": 261, "holders": {"r": [241, 260]}}, etag2)


def test_json_delete_requires_current_etag(remote: RemoteStore) -> None:
    key = "leases/checkout/0199f2b0-7c3a-7000-8000-0000000000aa"
    etag = remote.cas_json(PROJECT, key, None, {"replica_uid": "r", "actor": "dakh"})
    assert etag is not None
    assert remote.delete_json(PROJECT, key, "stale") is False
    assert remote.get_json(PROJECT, key) is not None
    assert remote.delete_json(PROJECT, key, etag) is True
    assert remote.get_json(PROJECT, key) is None
    assert remote.delete_json(PROJECT, key, etag) is False
    # Удалённый ключ снова создаётся «с нуля» и только один раз.
    again = remote.cas_json(PROJECT, key, None, {"replica_uid": "r2", "actor": "dakh"})
    assert again is not None
    assert remote.cas_json(PROJECT, key, None, {"replica_uid": "r3"}) is None
    assert remote.get_json(PROJECT, key) == ({"replica_uid": "r2", "actor": "dakh"}, again)


def test_meta_json_is_addressable(remote: RemoteStore) -> None:
    etag = remote.cas_json(PROJECT, "meta.json", None, {"v": 1, "epoch": "0050_sync_rank"})
    assert remote.get_json(PROJECT, "meta.json") == ({"v": 1, "epoch": "0050_sync_rank"}, etag)


def test_json_unicode_roundtrip(remote: RemoteStore) -> None:
    etag = remote.cas_json(PROJECT, "leases/Q", None, {"holder": "даш@макбук"})
    assert remote.get_json(PROJECT, "leases/Q") == ({"holder": "даш@макбук"}, etag)


@pytest.mark.parametrize(
    "bad", ["refs/main", "objects/ab/cd", "snapshots/x", "leases/../refs/main", "leases/", "x.json"]
)
def test_json_keys_limited_to_meta_and_leases(remote: RemoteStore, bad: str) -> None:
    with pytest.raises(ValueError):
        remote.cas_json(PROJECT, bad, None, {"x": 1})
    with pytest.raises(ValueError):
        remote.get_json(PROJECT, bad)


def test_list_prefix_lists_direct_json_keys(remote: RemoteStore) -> None:
    """CLO-048: ключи JSON-документов прямо под префиксом; удалённый (надгробие) — не ключ."""
    checkout = "leases/checkout/"
    assert remote.list_prefix(PROJECT, checkout) == []
    first = remote.cas_json(PROJECT, f"{checkout}t1", None, {"n": 1})
    assert first is not None
    remote.cas_json(PROJECT, f"{checkout}t2", None, {"n": 2})
    remote.cas_json(PROJECT, "leases/ADO", None, {"next": 1})
    remote.cas_json(OTHER, f"{checkout}t3", None, {"n": 3})
    assert remote.list_prefix(PROJECT, checkout) == [f"{checkout}t1", f"{checkout}t2"]
    assert remote.list_prefix(PROJECT, "leases/") == ["leases/ADO"]  # без подкаталогов
    assert remote.list_prefix(OTHER, checkout) == [f"{checkout}t3"]
    assert remote.delete_json(PROJECT, f"{checkout}t1", first)
    assert remote.list_prefix(PROJECT, checkout) == [f"{checkout}t2"]


@pytest.mark.parametrize(
    "bad", ["refs/", "objects/", "leases/checkout", "leases/../", "leases//", "meta.json", ""]
)
def test_list_prefix_limited_to_leases_directories(remote: RemoteStore, bad: str) -> None:
    with pytest.raises(ValueError):
        remote.list_prefix(PROJECT, bad)


# --- snapshots ------------------------------------------------------------------


def _snap(epoch: str, seed: str) -> SnapshotRef:
    s = _sha(seed)
    return SnapshotRef(epoch=epoch, sha=s, key=f"snapshots/{epoch}/{s}.db.zst")


def _head_snap(remote: RemoteStore, remote_url: str, epoch: str, seed: str) -> SnapshotRef:
    """Снапшот на голову-объект: умный remote принимает снапшот только на свой changeset."""
    sha, data = _object(remote_url, seed)
    remote.put_object(PROJECT, sha, data)
    return SnapshotRef(epoch=epoch, sha=sha, key=f"snapshots/{epoch}/{sha}.db.zst")


def _snap_data(remote_url: str, ref: SnapshotRef, tag: str) -> bytes:
    """Тело снапшота: умному remote — с заголовком по адресу, байтовым — любые байты."""
    if not remote_url.startswith("http"):
        return tag.encode()
    from tests.infra.remote._https_server import snapshot_bytes

    return snapshot_bytes(PROJECT, ref.epoch, ref.sha, tag)


def test_snapshots(remote: RemoteStore, remote_url: str) -> None:
    assert remote.latest_snapshot(PROJECT) is None
    old_epoch = _head_snap(remote, remote_url, "0050_sync_rank", "s1")
    db1 = _snap_data(remote_url, old_epoch, "db1")
    remote.put_snapshot(PROJECT, old_epoch, db1)
    assert remote.latest_snapshot(PROJECT) == old_epoch
    new_epoch = _head_snap(remote, remote_url, "0052_sync_access", "s0")
    db2 = _snap_data(remote_url, new_epoch, "db2")
    remote.put_snapshot(PROJECT, new_epoch, db2)
    remote.put_snapshot(PROJECT, new_epoch, db2)  # идемпотентно
    assert remote.latest_snapshot(PROJECT) == new_epoch
    assert remote.get_snapshot(PROJECT, old_epoch) == db1
    assert remote.latest_snapshot(OTHER) is None


def test_missing_snapshot_is_sync_102(remote: RemoteStore) -> None:
    with pytest.raises(ObjectMissingError):
        remote.get_snapshot(PROJECT, _snap("0050_sync_rank", "none"))


def test_snapshot_key_must_match_epoch_and_sha(remote: RemoteStore) -> None:
    bad = SnapshotRef(epoch="0050_sync_rank", sha=_sha("s"), key="refs/main")
    with pytest.raises(ValueError, match="key"):
        remote.put_snapshot(PROJECT, bad, b"db")


# --- open_remote ----------------------------------------------------------------


@pytest.mark.parametrize("url", ["ftp://host/x", "remote", "", "file://otherhost/x"])
def test_open_remote_rejects_unknown_url(url: str) -> None:
    with pytest.raises(RemoteConfigError) as exc:
        open_remote(url, credentials=None)
    assert exc.value.code == "SYNC-110"


def test_open_remote_dispatches_s3_without_network() -> None:
    """``s3://`` — ``S3Remote``, а не SYNC-110; открытие в сеть не ходит."""
    from cod_doc.infra.remote.s3 import S3Remote

    store = open_remote(
        "s3://cod-doc-bucket/team/main",
        credentials={
            "access_key": "AKIDEXAMPLE",
            "secret_key": "wJalrXUtnFEMI",
            "endpoint_url": "http://127.0.0.1:1",
        },
    )
    assert isinstance(store, S3Remote)
    assert (store.bucket, store.prefix) == ("cod-doc-bucket", "team/main")


# --- межпроцессная гонка CAS ----------------------------------------------------

_WORKERS = 4
_ROUNDS = 40
_TIMEOUT_S = 60


def _race_worker(
    url: str,
    idx: int,
    rounds: int,
    barrier: Barrier,
    out: multiprocessing.Queue[tuple[object, ...]],
) -> None:
    remote = open_remote(url, credentials=None)
    for i in range(rounds):
        name = f"race-{i}"
        key = f"leases/RACE{i}"
        created_sha = _sha(f"{idx}:{i}:create")
        updated_sha = _sha(f"{idx}:{i}:update")

        barrier.wait(_TIMEOUT_S)
        created = remote.cas_ref(PROJECT, name, None, created_sha)
        created_json = remote.cas_json(PROJECT, key, None, {"owner": idx, "phase": "create"})
        barrier.wait(_TIMEOUT_S)
        # Все читают одну и ту же голову до того, как кто-то двинет её снова.
        seen = remote.get_ref(PROJECT, name)
        seen_json = remote.get_json(PROJECT, key)
        barrier.wait(_TIMEOUT_S)
        updated = remote.cas_ref(PROJECT, name, seen, updated_sha)
        updated_json = remote.cas_json(
            PROJECT, key, seen_json[1] if seen_json else None, {"owner": idx, "phase": "update"}
        )
        out.put(
            (
                i,
                idx,
                created is not None,
                created_json is not None,
                updated is not None,
                updated_json is not None,
            )
        )


def test_concurrent_cas_has_exactly_one_winner(remote_url: str) -> None:
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(_WORKERS)
    out: multiprocessing.Queue[tuple[object, ...]] = ctx.Queue()
    procs = [
        ctx.Process(target=_race_worker, args=(remote_url, idx, _ROUNDS, barrier, out))
        for idx in range(_WORKERS)
    ]
    for p in procs:
        p.start()
    results: list[tuple[object, ...]] = []
    try:
        for _ in range(_WORKERS * _ROUNDS):
            results.append(out.get(timeout=_TIMEOUT_S))
    except queue.Empty:
        pytest.fail(f"гонка не завершилась: {len(results)} из {_WORKERS * _ROUNDS} результатов")
    finally:
        for p in procs:
            p.join(_TIMEOUT_S)
            if p.is_alive():
                p.kill()
    assert all(p.exitcode == 0 for p in procs)

    remote = open_remote(remote_url, credentials=None)
    for i in range(_ROUNDS):
        rows = [r for r in results if r[0] == i]
        for column, phase in (
            (2, "create ref"),
            (3, "create json"),
            (4, "update ref"),
            (5, "update json"),
        ):
            winners = [r[1] for r in rows if r[column]]
            assert len(winners) == 1, f"раунд {i}, {phase}: победители {winners}"
        update_winner = next(r[1] for r in rows if r[4])
        head = remote.get_ref(PROJECT, f"race-{i}")
        assert head is not None
        assert head.sha == _sha(f"{update_winner}:{i}:update")
        json_winner = next(r[1] for r in rows if r[5])
        body = remote.get_json(PROJECT, f"leases/RACE{i}")
        assert body is not None
        assert body[0] == {"owner": json_winner, "phase": "update"}


# --- аренда номеров (CLO-030) ----------------------------------------------------


class _RivalBeforeCas:
    """Транспорт, перед ``cas_json`` которого соперник успевает взять блок."""

    def __init__(self, inner: RemoteStore, rounds: int) -> None:
        self.inner = inner
        self.rounds = rounds

    def get_json(self, project_uid: str, key: str) -> tuple[dict[str, JsonValue], str] | None:
        return self.inner.get_json(project_uid, key)

    def cas_json(
        self,
        project_uid: str,
        key: str,
        expected_etag: str | None,
        body: Mapping[str, JsonValue],
    ) -> str | None:
        if self.rounds > 0:
            self.rounds -= 1
            leases.claim_block(self.inner, project_uid, key.split("/")[-1], replica="rival")
        return self.inner.cas_json(project_uid, key, expected_etag, body)


def test_lease_blocks_never_overlap_under_cas_contention(remote: RemoteStore) -> None:
    """``leases/<PREFIX>`` (RFC 29 §3.5): проигравший CAS перечитывает и берёт следующий блок."""
    racing = _RivalBeforeCas(remote, rounds=2)
    mine = leases.claim_block(racing, PROJECT, "ADO", replica="me", floor=5)  # type: ignore[arg-type]
    got = remote.get_json(PROJECT, "leases/ADO")
    assert got is not None
    body, _etag = got
    holders = body["holders"]
    assert isinstance(holders, dict)
    rival = holders["rival"]
    assert isinstance(rival, list)
    assert holders["me"] == list(mine)
    assert not set(range(mine[0], mine[1] + 1)) & set(range(int(rival[0]), int(rival[1]) + 1))
    assert body["next"] == mine[1] + 1  # последний CAS — мой, после двух блоков соперника
    # Соперник начал с 1 (его floor), два его блока — 1..40; мой floor 5 уже позади.
    assert mine[0] == 2 * leases.LEASE_BLOCK + 1


# --- аренда замка checkout (CLO-031) ---------------------------------------------------

TASK_UID = "0199f2b0-7c3a-7000-8000-0000000000aa"


def _holder(agent: str, replica: str, *, minutes_ago: int = 0) -> checkout_lease.Holder:
    return checkout_lease.Holder(
        task_id="ADO-230",
        agent=agent,
        replica=replica,
        acquired_at=datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=minutes_ago),
    )


def test_checkout_lease_claim_is_exclusive_and_drop_is_owner_only(remote: RemoteStore) -> None:
    """``leases/checkout/<task_uid>`` (RFC 29 §3.6): CAS «ключа нет», снятие — по etag."""
    mine, rival = _holder("agent:a", "rep-a"), _holder("agent:b", "rep-b")
    got, etag, created, _previous = checkout_lease.claim(remote, PROJECT, TASK_UID, mine)
    assert (got, created) == (mine, True)
    assert checkout_lease.read(remote, PROJECT, TASK_UID) == (mine, etag)
    assert checkout_lease.claim(remote, PROJECT, TASK_UID, mine)[:3] == (mine, etag, False)
    with pytest.raises(CheckoutLeaseHeldError) as held:
        checkout_lease.claim(remote, PROJECT, TASK_UID, rival)
    assert held.value.code == "SYNC-108"
    assert not checkout_lease.drop(remote, PROJECT, TASK_UID, replica="rep-b")
    assert not checkout_lease.drop(remote, PROJECT, TASK_UID, replica="rep-a", agent="agent:b")
    assert checkout_lease.drop(
        remote, PROJECT, TASK_UID, replica="rep-a", agent="agent:a", etag=etag
    )
    assert checkout_lease.read(remote, PROJECT, TASK_UID) is None
    # После снятия (на S3 — надгробие) ключ снова берётся CAS'ом «ключа нет».
    assert checkout_lease.claim(remote, PROJECT, TASK_UID, rival)[2] is True


def test_checkout_lease_take_over_and_expired_drop(remote: RemoteStore) -> None:
    """Разрешение ``double_checkout`` переписывает аренду; истёкшую чужую снимает ``expired_ok``."""
    stale = _holder("agent:ghost", "rep-x", minutes_ago=checkout_lease.DEFAULT_TTL_MINUTES + 5)
    checkout_lease.claim(remote, PROJECT, TASK_UID, stale)
    fresh = _holder("agent:a", "rep-a")
    checkout_lease.take_over(remote, PROJECT, TASK_UID, fresh)
    assert checkout_lease.read(remote, PROJECT, TASK_UID) is not None
    assert not checkout_lease.drop(remote, PROJECT, TASK_UID, replica="rep-x", expired_ok=True)
    checkout_lease.take_over(remote, PROJECT, TASK_UID, stale)
    assert checkout_lease.drop(remote, PROJECT, TASK_UID, replica="rep-a", expired_ok=True)
    assert checkout_lease.read(remote, PROJECT, TASK_UID) is None


class _RivalTakesLease:
    """Транспорт, перед ``cas_json`` которого соперник берёт аренду checkout."""

    def __init__(self, inner: RemoteStore, *, take_expired: bool = False) -> None:
        self.inner = inner
        self.armed = True
        self.take_expired = take_expired

    def get_json(self, project_uid: str, key: str) -> tuple[dict[str, JsonValue], str] | None:
        return self.inner.get_json(project_uid, key)

    def cas_json(
        self,
        project_uid: str,
        key: str,
        expected_etag: str | None,
        body: Mapping[str, JsonValue],
    ) -> str | None:
        if self.armed:
            self.armed = False
            rival = _holder("agent:b", "rep-b")
            if self.take_expired:
                checkout_lease.claim(self.inner, project_uid, TASK_UID, rival, take_expired=True)
            else:
                checkout_lease.claim(self.inner, project_uid, TASK_UID, rival)
        return self.inner.cas_json(project_uid, key, expected_etag, body)


def test_checkout_lease_lost_race_is_sync_108_not_overwrite(remote: RemoteStore) -> None:
    """Соперник взял аренду между чтением и CAS: мой CAS отвергнут, перечитал — SYNC-108."""
    racing = _RivalTakesLease(remote)
    with pytest.raises(CheckoutLeaseHeldError, match="agent:b"):
        checkout_lease.claim(racing, PROJECT, TASK_UID, _holder("agent:a", "rep-a"))  # type: ignore[arg-type]
    current = checkout_lease.read(remote, PROJECT, TASK_UID)
    assert current is not None
    assert current[0].agent == "agent:b"


# --- осиротевшая аренда checkout (CLO-048) ---------------------------------------------

_PAST_TTL = checkout_lease.DEFAULT_TTL_MINUTES + 10


def test_checkout_lease_expired_is_taken_over_only_on_request(remote: RemoteStore) -> None:
    """Истёкшая чужая аренда перехватывается CAS'ом по её etag и только с ``take_expired``."""
    ghost = _holder("agent:ghost", "rep-x", minutes_ago=_PAST_TTL)
    checkout_lease.claim(remote, PROJECT, TASK_UID, ghost)
    mine = _holder("agent:a", "rep-a")
    with pytest.raises(CheckoutLeaseHeldError):
        checkout_lease.claim(remote, PROJECT, TASK_UID, mine)
    got = checkout_lease.claim(remote, PROJECT, TASK_UID, mine, take_expired=True)
    assert (got.holder, got.created, got.previous) == (mine, True, ghost)
    assert checkout_lease.read(remote, PROJECT, TASK_UID) == (mine, got.etag)
    with pytest.raises(CheckoutLeaseHeldError):  # моя свежая — не перехватывается
        checkout_lease.claim(remote, PROJECT, TASK_UID, ghost, take_expired=True)


def test_checkout_lease_takeover_race_has_one_winner(remote: RemoteStore) -> None:
    """Две реплики перехватывают одну истёкшую аренду: соперник успел — мой CAS отвергнут."""
    checkout_lease.claim(
        remote, PROJECT, TASK_UID, _holder("agent:ghost", "rep-x", minutes_ago=_PAST_TTL)
    )
    racing = _RivalTakesLease(remote, take_expired=True)
    with pytest.raises(CheckoutLeaseHeldError, match="agent:b"):
        checkout_lease.claim(  # type: ignore[arg-type]
            racing, PROJECT, TASK_UID, _holder("agent:a", "rep-a"), take_expired=True
        )
    current = checkout_lease.read(remote, PROJECT, TASK_UID)
    assert current is not None
    assert (current[0].agent, current[0].replica) == ("agent:b", "rep-b")


def test_checkout_lease_sweep_drops_only_expired(remote: RemoteStore) -> None:
    """``release_stale`` чистит ``leases/checkout/`` листингом: истёкшие — да, живые и битые — нет."""
    expired, live, bad = (f"0199f2b0-7c3a-7000-8000-00000000000{n}" for n in (1, 2, 3))
    checkout_lease.claim(
        remote, PROJECT, expired, _holder("agent:x", "rep-x", minutes_ago=_PAST_TTL)
    )
    checkout_lease.claim(remote, PROJECT, live, _holder("agent:y", "rep-y"))
    remote.cas_json(PROJECT, checkout_lease.lease_key(bad), None, {"agent": 1})
    assert checkout_lease.sweep(remote, PROJECT) == [expired]
    assert checkout_lease.read(remote, PROJECT, expired) is None
    assert checkout_lease.read(remote, PROJECT, live) is not None
    assert remote.get_json(PROJECT, checkout_lease.lease_key(bad)) is not None
