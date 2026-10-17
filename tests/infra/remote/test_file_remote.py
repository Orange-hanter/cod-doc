"""``FileRemote`` поверх недоверенного каталога: ссылки, не-файлы, потолки чтения.

Контракт протокола — ``test_remote_contract.py``; здесь то, что специфично для
ФС: содержимое каталога remote пишут другие машины, и ни ссылка, ни FIFO, ни
раздутый файл не должны вывести запись за корень, подвесить чтение или съесть
память.
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.remote import file as file_mod
from cod_doc.infra.remote.base import SnapshotRef
from cod_doc.infra.remote.errors import ObjectMissingError, RemoteUnavailableError
from cod_doc.infra.remote.file import FileRemote

if TYPE_CHECKING:
    from pathlib import Path

PROJECT = "0199f2b0-7c3a-7000-8000-0000000000ff"


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "remote"
    path.mkdir()
    return path


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside"
    path.mkdir()
    return path


def _tree(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def test_symlinked_project_dir_is_refused(root: Path, outside: Path) -> None:
    (root / PROJECT).symlink_to(outside, target_is_directory=True)
    remote = FileRemote(root)
    with pytest.raises(RemoteUnavailableError) as exc:
        remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    assert exc.value.code == "SYNC-101"
    with pytest.raises(RemoteUnavailableError):
        remote.put_object(PROJECT, _sha("o"), b"x")
    with pytest.raises(RemoteUnavailableError):
        remote.cas_json(PROJECT, "leases/ADO", None, {"next": 1})
    with pytest.raises(RemoteUnavailableError):
        remote.get_ref(PROJECT, "main")
    assert _tree(outside) == []


def test_symlinked_intermediate_dir_is_refused(root: Path, outside: Path) -> None:
    (root / PROJECT).mkdir()
    (root / PROJECT / "leases").symlink_to(outside, target_is_directory=True)
    remote = FileRemote(root)
    with pytest.raises(RemoteUnavailableError):
        remote.cas_json(PROJECT, "leases/checkout/task", None, {"actor": "x"})
    assert _tree(outside) == []


def test_symlinked_ref_file_is_refused(root: Path, outside: Path) -> None:
    target = outside / "head"
    target.write_text(_sha("foreign") + "\n")
    (root / PROJECT / "refs").mkdir(parents=True)
    (root / PROJECT / "refs" / "main").symlink_to(target)
    remote = FileRemote(root)
    with pytest.raises(RemoteUnavailableError):
        remote.get_ref(PROJECT, "main")
    with pytest.raises(RemoteUnavailableError):
        remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    assert target.read_text() == _sha("foreign") + "\n"


def test_symlinked_object_is_not_present(root: Path, outside: Path) -> None:
    sha = _sha("o")
    (outside / "obj").write_bytes(b"foreign")
    shard = root / PROJECT / "objects" / sha[:2]
    shard.mkdir(parents=True)
    (shard / f"{sha[2:]}.zst").symlink_to(outside / "obj")
    remote = FileRemote(root)
    assert remote.has_objects(PROJECT, [sha]) == set()
    with pytest.raises(RemoteUnavailableError):
        remote.get_object(PROJECT, sha)
    # put заменяет саму ссылку настоящим файлом, цель ссылки не трогается.
    remote.put_object(PROJECT, sha, b"CDC1own")
    assert remote.get_object(PROJECT, sha) == b"CDC1own"
    assert (outside / "obj").read_bytes() == b"foreign"


def test_symlinked_snapshot_entries_are_skipped(root: Path, outside: Path) -> None:
    (root / PROJECT / "snapshots").mkdir(parents=True)
    (outside / f"{_sha('s')}.db.zst").write_bytes(b"db")
    (root / PROJECT / "snapshots" / "0099_evil").symlink_to(outside, target_is_directory=True)
    assert FileRemote(root).latest_snapshot(PROJECT) is None


def test_symlinked_root_is_resolved_once(tmp_path: Path, root: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    remote = FileRemote.from_url(link.as_uri())
    assert remote.cas_ref(PROJECT, "main", None, _sha("c1")) is not None
    assert (root / PROJECT / "refs" / "main").is_file()


def test_fifo_in_place_of_ref_is_refused_without_blocking(root: Path) -> None:
    (root / PROJECT / "refs").mkdir(parents=True)
    os.mkfifo(root / PROJECT / "refs" / "main")
    with pytest.raises(RemoteUnavailableError):
        FileRemote(root).get_ref(PROJECT, "main")


def test_directory_in_place_of_object_is_refused(root: Path) -> None:
    sha = _sha("o")
    (root / PROJECT / "objects" / sha[:2] / f"{sha[2:]}.zst").mkdir(parents=True)
    remote = FileRemote(root)
    assert remote.has_objects(PROJECT, [sha]) == set()
    with pytest.raises(RemoteUnavailableError):
        remote.get_object(PROJECT, sha)


def test_file_in_place_of_directory_is_refused(root: Path) -> None:
    (root / PROJECT).mkdir()
    (root / PROJECT / "refs").write_text("not a dir")
    with pytest.raises(RemoteUnavailableError):
        FileRemote(root).cas_ref(PROJECT, "main", None, _sha("c1"))


def _count_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    sizes: list[int] = []
    real_read = os.read

    def counting_read(fd: int, n: int) -> bytes:
        chunk = real_read(fd, n)
        sizes.append(len(chunk))
        return chunk

    monkeypatch.setattr(file_mod.os, "read", counting_read)
    return sizes


def test_oversized_json_is_refused_before_reading(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / PROJECT / "leases").mkdir(parents=True)
    (root / PROJECT / "leases" / "ADO").write_bytes(b'{"x":"' + b"a" * 4096 + b'"}')
    monkeypatch.setattr(file_mod, "MAX_JSON_BYTES", 1024)
    sizes = _count_reads(monkeypatch)
    with pytest.raises(ObjectMissingError) as exc:
        FileRemote(root).get_json(PROJECT, "leases/ADO")
    assert exc.value.code == "SYNC-102"
    assert sum(sizes) == 0


def test_oversized_object_is_refused_before_reading(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = FileRemote(root)
    sha = _sha("big")
    remote.put_object(PROJECT, sha, b"x" * 10_000)
    monkeypatch.setattr(file_mod, "MAX_OBJECT_BYTES", 100)
    sizes = _count_reads(monkeypatch)
    with pytest.raises(ObjectMissingError):
        remote.get_object(PROJECT, sha)
    assert sum(sizes) == 0


def test_read_never_exceeds_cap_plus_one(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Файл, выросший после fstat, читается не дальше ``потолок + 1``."""
    remote = FileRemote(root)
    sha = _sha("grow")
    remote.put_object(PROJECT, sha, b"x" * 50)
    path = root / PROJECT / "objects" / sha[:2] / f"{sha[2:]}.zst"
    real_fstat = os.fstat

    def fstat_then_grow(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        path.write_bytes(b"x" * 10_000)  # дописали между fstat и read
        return info

    monkeypatch.setattr(file_mod, "MAX_OBJECT_BYTES", 100)
    monkeypatch.setattr(file_mod.os, "fstat", fstat_then_grow)
    sizes = _count_reads(monkeypatch)
    with pytest.raises(ObjectMissingError):
        remote.get_object(PROJECT, sha)
    assert sum(sizes) <= 101


def test_oversized_snapshot_is_refused(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = FileRemote(root)
    sha = _sha("snap")
    ref = SnapshotRef(epoch="0050_sync_rank", sha=sha, key=f"snapshots/0050_sync_rank/{sha}.db.zst")
    remote.put_snapshot(PROJECT, ref, b"x" * 1000)
    monkeypatch.setattr(file_mod, "MAX_SNAPSHOT_BYTES", 10)
    with pytest.raises(ObjectMissingError):
        remote.get_snapshot(PROJECT, ref)


def test_cas_json_refuses_body_over_cap(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_mod, "MAX_JSON_BYTES", 16)
    with pytest.raises(ValueError, match="потолка"):
        FileRemote(root).cas_json(PROJECT, "leases/ADO", None, {"holders": "x" * 100})
