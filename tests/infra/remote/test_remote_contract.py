"""Контракт ``RemoteStore`` (RFC 29 §10.5, CLO-011) — общий для всех реализаций.

Набор параметризован реализацией: фабрика из ``FACTORIES`` получает
``tmp_path`` и возвращает URL, который понимает ``open_remote``. ``S3Remote``
(CLO-028) и ``HttpRemote`` (CLO-035) добавляют сюда свою строку и проходят те
же тесты, включая межпроцессную гонку CAS.

Гонка идёт через URL, а не через объект: каждый процесс открывает remote сам,
как это делают две реплики на разных машинах.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import queue
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.remote.base import RefValue, RemoteStore, SnapshotRef, open_remote
from cod_doc.infra.remote.errors import ObjectMissingError, RemoteConfigError

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.synchronize import Barrier
    from pathlib import Path

PROJECT = "0199f2b0-7c3a-7000-8000-0000000000ff"
OTHER = "0199f2b0-7c3a-7000-8000-0000000000ee"


def _file_url(tmp_path: Path) -> str:
    return (tmp_path / "remote").as_uri()


FACTORIES: dict[str, Callable[[Path], str]] = {"file": _file_url}


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


@pytest.fixture(params=sorted(FACTORIES))
def remote_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    return FACTORIES[request.param](tmp_path)


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


def test_put_get_has_objects(remote: RemoteStore) -> None:
    a, b = _sha("a"), _sha("b")
    assert remote.has_objects(PROJECT, [a, b]) == set()
    remote.put_object(PROJECT, a, b"CDC1payload")
    assert remote.has_objects(PROJECT, [a, b]) == {a}
    assert remote.has_objects(PROJECT, []) == set()
    assert remote.get_object(PROJECT, a) == b"CDC1payload"


def test_put_object_is_idempotent(remote: RemoteStore) -> None:
    a = _sha("a")
    remote.put_object(PROJECT, a, b"CDC1payload")
    remote.put_object(PROJECT, a, b"CDC1payload")
    assert remote.get_object(PROJECT, a) == b"CDC1payload"


def test_missing_object_is_sync_102(remote: RemoteStore) -> None:
    with pytest.raises(ObjectMissingError) as exc:
        remote.get_object(PROJECT, _sha("nope"))
    assert exc.value.code == "SYNC-102"


def test_objects_are_per_project(remote: RemoteStore) -> None:
    a = _sha("a")
    remote.put_object(PROJECT, a, b"x")
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


# --- snapshots ------------------------------------------------------------------


def _snap(epoch: str, seed: str) -> SnapshotRef:
    s = _sha(seed)
    return SnapshotRef(epoch=epoch, sha=s, key=f"snapshots/{epoch}/{s}.db.zst")


def test_snapshots(remote: RemoteStore) -> None:
    assert remote.latest_snapshot(PROJECT) is None
    old_epoch = _snap("0050_sync_rank", "s1")
    remote.put_snapshot(PROJECT, old_epoch, b"db1")
    assert remote.latest_snapshot(PROJECT) == old_epoch
    new_epoch = _snap("0052_sync_access", "s0")
    remote.put_snapshot(PROJECT, new_epoch, b"db2")
    remote.put_snapshot(PROJECT, new_epoch, b"db2")  # идемпотентно
    assert remote.latest_snapshot(PROJECT) == new_epoch
    assert remote.get_snapshot(PROJECT, old_epoch) == b"db1"
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
