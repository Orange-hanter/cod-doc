"""Протокол транспорта remote (RFC 29 §10.5, CLO-011).

Транспорт байтовый: объект — непрозрачные байты под адресом ``sha``; что внутри
(``CDC1`` + zstd канонического JSON) и совпадает ли содержимое с адресом,
проверяет ``services/sync_service/changeset.decode``. Поэтому слой не знает о
changeset'ах и не импортирует ``services``.

Раскладка на remote (§3.5) одинакова для всех реализаций::

    <project_uid>/
      refs/<name>                    # sha головы; меняется только CAS
      leases/<PREFIX>                # JSON; меняется только CAS
      leases/checkout/<task_uid>     # JSON; замок checkout
      objects/ab/cdef….zst           # changeset'ы
      snapshots/<epoch>/<sha>.db.zst
      meta.json

CAS-отказ — не исключение, а ``None`` (``False`` у ``delete_json``): это штатный
исход гонки, на который ``push`` отвечает pull'ом и повтором. ``etag`` —
непрозрачная метка версии; сравнивать её можно только на равенство.

Аргументы проверяются до обращения к хранилищу: имя проекта, ref'а, эпохи и
сегменты ключа — ``[A-Za-z0-9_][A-Za-z0-9._-]*``, sha — 64 hex в нижнем регистре,
JSON-ключи — только ``meta.json`` и ``leases/…``. Нарушение — ``ValueError``:
это ошибка вызывающего, а не remote.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import urlparse

from cod_doc.infra.remote.errors import RemoteConfigError

__all__ = [
    "Credentials",
    "JsonValue",
    "RefValue",
    "RemoteStore",
    "SnapshotRef",
    "check_json_key",
    "check_name",
    "check_sha",
    "check_snapshot",
    "open_remote",
    "snapshot_key",
]

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
#: Креды remote из ``~/.cod-doc/credentials`` (§10.12); file:// их не читает.
type Credentials = Mapping[str, str]

_NAME_RE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_SHA_RE: Final = re.compile(r"[0-9a-f]{64}")
META_KEY: Final = "meta.json"
LEASES_PREFIX: Final = "leases/"


@dataclass(frozen=True, slots=True)
class RefValue:
    sha: str
    etag: str


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    epoch: str
    sha: str
    key: str


class RemoteStore(Protocol):
    def get_ref(self, project_uid: str, name: str) -> RefValue | None: ...

    def cas_ref(
        self, project_uid: str, name: str, expected: RefValue | None, new_sha: str
    ) -> RefValue | None:
        """``None`` — CAS отвергнут (ref не равен ``expected``); иначе новое значение."""
        ...

    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]: ...

    def put_object(self, project_uid: str, sha: str, data: bytes) -> None:
        """Идемпотентно: объект под существующим адресом не перезаписывается."""
        ...

    def get_object(self, project_uid: str, sha: str) -> bytes:
        """``ObjectMissingError`` (SYNC-102), если объекта нет."""
        ...

    def get_json(self, project_uid: str, key: str) -> tuple[dict[str, JsonValue], str] | None:
        """``(тело, etag)`` или ``None``, если ключа нет."""
        ...

    def cas_json(
        self,
        project_uid: str,
        key: str,
        expected_etag: str | None,
        body: Mapping[str, JsonValue],
    ) -> str | None:
        """Новый etag или ``None`` — CAS отвергнут; ``expected_etag=None`` — «ключа нет»."""
        ...

    def delete_json(self, project_uid: str, key: str, expected_etag: str) -> bool: ...

    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None:
        """Снапшот самой поздней эпохи, внутри эпохи — последний записанный."""
        ...

    def put_snapshot(self, project_uid: str, ref: SnapshotRef, data: bytes) -> None: ...

    def get_snapshot(self, project_uid: str, ref: SnapshotRef) -> bytes:
        """Тело снапшота; ``ObjectMissingError`` (SYNC-102), если его нет."""
        ...


def check_name(value: str, what: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        msg = f"{what}: недопустимое имя {value!r}"
        raise ValueError(msg)
    return value


def check_sha(value: str, what: str = "sha") -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        msg = f"{what}: нужен sha256 в нижнем регистре, получено {value!r}"
        raise ValueError(msg)
    return value


def check_json_key(key: str) -> tuple[str, ...]:
    """Сегменты ключа JSON-документа; разрешены только ``meta.json`` и ``leases/…``."""
    if key == META_KEY:
        return (META_KEY,)
    if not isinstance(key, str) or not key.startswith(LEASES_PREFIX):
        msg = f"JSON-ключ {key!r}: разрешены {META_KEY} и {LEASES_PREFIX}…"
        raise ValueError(msg)
    parts = tuple(key.split("/"))
    for part in parts:
        check_name(part, f"JSON-ключ {key!r}")
    return parts


def snapshot_key(epoch: str, sha: str) -> str:
    return f"snapshots/{check_name(epoch, 'epoch')}/{check_sha(sha)}.db.zst"


def check_snapshot(ref: SnapshotRef) -> SnapshotRef:
    expected = snapshot_key(ref.epoch, ref.sha)
    if ref.key != expected:
        msg = f"SnapshotRef.key {ref.key!r} ≠ {expected!r}"
        raise ValueError(msg)
    return ref


def open_remote(url: str, *, credentials: Credentials | None = None) -> RemoteStore:
    """Транспорт по схеме URL; неизвестная схема — ``RemoteConfigError`` (SYNC-110).

    ``s3://`` (CLO-028) и ``https://`` (CLO-035) пока тоже SYNC-110: remote с
    такой схемой настроить нельзя, пока нет реализации.
    """
    del credentials  # file:// без кредов; S3/Http возьмут их, когда появятся
    scheme = urlparse(url).scheme
    if scheme == "file":
        from cod_doc.infra.remote.file import FileRemote

        return FileRemote.from_url(url)
    if scheme in {"s3", "https"}:
        msg = f"remote {url!r}: транспорт {scheme}:// ещё не реализован"
        raise RemoteConfigError(msg)
    msg = f"remote {url!r}: неизвестная схема, ожидается file://, s3:// или https://"
    raise RemoteConfigError(msg)
