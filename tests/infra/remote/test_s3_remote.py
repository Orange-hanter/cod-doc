"""``S3Remote`` без сети: URL, раскладка ключей, перевод ошибок, потолки чтения.

Контракт протокола против настоящего хранилища — ``test_remote_contract.py``
(строка ``s3``, MinIO в CI). Здесь то, что на живом MinIO не воспроизвести
по заказу: обрыв сети, 403, 503 SlowDown, ответ больше заявленного, — и то,
что не должно зависеть от хранилища вовсе: ключ собирается из проверенных
сегментов, а креды не попадают в текст ошибки.
"""

from __future__ import annotations

import hashlib
import io
from typing import TYPE_CHECKING, cast

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from cod_doc.infra.remote import s3 as s3_mod
from cod_doc.infra.remote.base import RefValue, SnapshotRef
from cod_doc.infra.remote.errors import (
    ObjectMissingError,
    RemoteAuthError,
    RemoteConfigError,
    RemoteRateLimitedError,
    RemoteUnavailableError,
)
from cod_doc.infra.remote.s3 import S3Remote

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

PROJECT = "0199f2b0-7c3a-7000-8000-0000000000ff"
SECRET = "wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY"
PRESIGNED = "https://bucket.s3.amazonaws.com/x?X-Amz-Signature=deadbeef"


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _client_error(code: str, status: int, op: str = "PutObject") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"see {PRESIGNED} key={SECRET}"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        op,
    )


class _Body:
    """Тело ответа: считает прочитанное, чтобы проверить потолок."""

    def __init__(self, data: bytes) -> None:
        self._io = io.BytesIO(data)
        self.read_total = 0
        self.closed = False

    def read(self, amt: int | None = None) -> bytes:
        chunk = self._io.read(amt)
        self.read_total += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeClient:
    """Минимум клиента S3: запись вызовов и заранее заданный исход."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.raise_on: dict[str, BaseException] = {}
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.declared_length: int | None = None
        self.last_body: _Body | None = None

    def _call(self, op: str, kwargs: dict[str, object]) -> None:
        self.calls.append((op, kwargs))
        if op in self.raise_on:
            raise self.raise_on[op]

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self._call("put_object", kwargs)
        key = cast("str", kwargs["Key"])
        current = self.objects.get(key)
        if kwargs.get("IfNoneMatch") == "*" and current is not None:
            raise _client_error("PreconditionFailed", 412)
        if "IfMatch" in kwargs and (current is None or current[1] != kwargs["IfMatch"]):
            raise _client_error("PreconditionFailed", 412)
        body = cast("bytes", kwargs["Body"])
        etag = f'"{hashlib.md5(body).hexdigest()}"'  # ETag S3 для PUT без multipart
        self.objects[key] = (body, etag)
        return {"ETag": etag}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self._call("get_object", kwargs)
        key = cast("str", kwargs["Key"])
        if key not in self.objects:
            raise _client_error("NoSuchKey", 404, "GetObject")
        data, etag = self.objects[key]
        self.last_body = _Body(data)
        length = len(data) if self.declared_length is None else self.declared_length
        return {"Body": self.last_body, "ETag": etag, "ContentLength": length}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self._call("head_object", kwargs)
        key = cast("str", kwargs["Key"])
        if key not in self.objects:
            raise _client_error("404", 404, "HeadObject")
        return {"ETag": self.objects[key][1]}


def _remote(prefix: str = "team/main") -> tuple[S3Remote, FakeClient]:
    fake = FakeClient()
    return S3Remote(cast("S3Client", fake), "cod-doc-bucket", prefix), fake


# --- URL и креды ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "bucket", "prefix"),
    [
        ("s3://cod-doc-bucket", "cod-doc-bucket", ""),
        ("s3://cod-doc-bucket/", "cod-doc-bucket", ""),
        ("s3://cod-doc-bucket/team/main", "cod-doc-bucket", "team/main"),
        ("s3://my.bucket-1/p", "my.bucket-1", "p"),
    ],
)
def test_from_url_parses_bucket_and_prefix(url: str, bucket: str, prefix: str) -> None:
    store = S3Remote.from_url(url, credentials={"access_key": "AKID", "secret_key": SECRET})
    assert (store.bucket, store.prefix) == (bucket, prefix)


@pytest.mark.parametrize(
    "url",
    [
        "s3://",
        "s3:///prefix",
        "s3://Bad_Bucket/x",
        "s3://ab/x",
        "s3://bucket/../refs",
        "s3://bucket/a//b",
        "s3://bucket/.hidden",
        "s3://bucket/x?versionId=1",
        "s3://bucket/x#frag",
        "s3://bucket:9000/x",
        "file:///tmp/x",
    ],
)
def test_from_url_rejects_malformed(url: str) -> None:
    with pytest.raises(RemoteConfigError) as exc:
        S3Remote.from_url(url)
    assert exc.value.code == "SYNC-110"


def test_credentials_in_url_are_refused_without_echo() -> None:
    with pytest.raises(RemoteConfigError) as exc:
        S3Remote.from_url(f"s3://AKID:{SECRET}@bucket/x")
    assert SECRET not in str(exc.value)
    assert "AKID" not in str(exc.value)


def test_partial_credentials_are_refused_without_echo() -> None:
    with pytest.raises(RemoteConfigError) as exc:
        S3Remote.from_url("s3://cod-doc-bucket/x", credentials={"secret_key": SECRET})
    assert exc.value.code == "SYNC-110"
    assert SECRET not in str(exc.value)


def test_unknown_credential_field_is_refused() -> None:
    with pytest.raises(RemoteConfigError, match="token"):
        S3Remote.from_url("s3://cod-doc-bucket/x", credentials={"token": SECRET})


# --- раскладка ключей --------------------------------------------------------------


def test_keys_follow_remote_layout_under_prefix() -> None:
    remote, fake = _remote()
    sha = _sha("o")
    remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    remote.put_object(PROJECT, sha, b"CDC1")
    remote.cas_json(PROJECT, "leases/checkout/t1", None, {"a": 1})
    remote.put_snapshot(
        PROJECT,
        SnapshotRef(epoch="0050_sync_rank", sha=sha, key=f"snapshots/0050_sync_rank/{sha}.db.zst"),
        b"db",
    )
    keys = [call[1]["Key"] for call in fake.calls if call[0] == "put_object"]
    assert keys == [
        f"team/main/{PROJECT}/refs/main",
        f"team/main/{PROJECT}/objects/{sha[:2]}/{sha[2:]}.zst",
        f"team/main/{PROJECT}/leases/checkout/t1",
        f"team/main/{PROJECT}/snapshots/0050_sync_rank/{sha}.db.zst",
    ]
    assert all(call[1]["Bucket"] == "cod-doc-bucket" for call in fake.calls)


def test_empty_prefix_puts_project_at_bucket_root() -> None:
    remote, fake = _remote(prefix="")
    remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    assert fake.calls[-1][1]["Key"] == f"{PROJECT}/refs/main"


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", ".hidden", "x\x00"])
def test_unsafe_names_never_reach_the_client(bad: str) -> None:
    remote, fake = _remote()
    with pytest.raises(ValueError):
        remote.get_ref(bad, "main")
    with pytest.raises(ValueError):
        remote.cas_ref(PROJECT, bad, None, _sha("c"))
    with pytest.raises(ValueError):
        remote.put_object(bad, _sha("o"), b"x")
    assert fake.calls == []


# --- CAS ------------------------------------------------------------------------


def test_cas_create_uses_if_none_match_and_update_uses_if_match() -> None:
    remote, fake = _remote()
    first = remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    assert first is not None
    assert fake.calls[-1][1]["IfNoneMatch"] == "*"
    assert "IfMatch" not in fake.calls[-1][1]
    remote.cas_ref(PROJECT, "main", first, _sha("c2"))
    assert fake.calls[-1][1]["IfMatch"] == first.etag
    assert "IfNoneMatch" not in fake.calls[-1][1]


@pytest.mark.parametrize(
    ("code", "status"),
    [("PreconditionFailed", 412), ("ConditionalRequestConflict", 409), ("NoSuchKey", 404)],
)
def test_lost_cas_is_none_not_an_error(code: str, status: int) -> None:
    remote, fake = _remote()
    fake.raise_on["put_object"] = _client_error(code, status)
    expected = RefValue(sha=_sha("c1"), etag='"abc"')
    assert remote.cas_ref(PROJECT, "main", expected, _sha("c2")) is None
    assert remote.cas_json(PROJECT, "leases/ADO", '"abc"', {"next": 1}) is None


def test_put_object_existing_address_is_a_noop() -> None:
    remote, fake = _remote()
    fake.raise_on["put_object"] = _client_error("PreconditionFailed", 412)
    remote.put_object(PROJECT, _sha("o"), b"CDC1")  # не бросает
    assert fake.calls[-1][1]["IfNoneMatch"] == "*"


def test_delete_json_writes_conditional_tombstone_not_delete() -> None:
    remote, fake = _remote()
    etag = remote.cas_json(PROJECT, "leases/ADO", None, {"next": 1})
    assert etag is not None
    assert remote.delete_json(PROJECT, "leases/ADO", '"stale"') is False
    assert remote.delete_json(PROJECT, "leases/ADO", etag) is True
    assert not [call for call in fake.calls if call[0] == "delete_object"]
    tombstone = fake.calls[-1][1]
    assert (tombstone["Body"], tombstone["IfMatch"]) == (b"", etag)
    assert remote.get_json(PROJECT, "leases/ADO") is None
    assert remote.delete_json(PROJECT, "leases/ADO", etag) is False


def test_create_over_tombstone_is_conditional_on_its_etag() -> None:
    remote, fake = _remote()
    etag = remote.cas_json(PROJECT, "leases/ADO", None, {"next": 1})
    assert etag is not None
    assert remote.delete_json(PROJECT, "leases/ADO", etag)
    tomb_etag = fake.objects[f"team/main/{PROJECT}/leases/ADO"][1]
    again = remote.cas_json(PROJECT, "leases/ADO", None, {"next": 2})
    assert again is not None
    assert fake.calls[-1][1]["IfMatch"] == tomb_etag
    assert remote.get_json(PROJECT, "leases/ADO") == ({"next": 2}, again)
    # Над живым документом создание по-прежнему отвергается.
    assert remote.cas_json(PROJECT, "leases/ADO", None, {"next": 3}) is None


def test_delete_json_lost_race_is_false() -> None:
    remote, fake = _remote()
    etag = remote.cas_json(PROJECT, "leases/ADO", None, {"next": 1})
    assert etag is not None
    fake.raise_on["put_object"] = _client_error("PreconditionFailed", 412)
    assert remote.delete_json(PROJECT, "leases/ADO", etag) is False


# --- ошибки транспорта ----------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind", "code"),
    [
        (EndpointConnectionError(endpoint_url=PRESIGNED), RemoteUnavailableError, "SYNC-101"),
        (_client_error("InternalError", 500), RemoteUnavailableError, "SYNC-101"),
        (_client_error("AccessDenied", 403), RemoteAuthError, "SYNC-201"),
        (_client_error("InvalidAccessKeyId", 403), RemoteAuthError, "SYNC-201"),
        (_client_error("SignatureDoesNotMatch", 403), RemoteAuthError, "SYNC-201"),
        (NoCredentialsError(), RemoteAuthError, "SYNC-201"),
        (_client_error("SlowDown", 503), RemoteRateLimitedError, "SYNC-204"),
        (_client_error("NoSuchBucket", 404), RemoteConfigError, "SYNC-110"),
    ],
)
def test_transport_errors_map_to_sync_codes(
    exc: BaseException, kind: type[Exception], code: str
) -> None:
    remote, fake = _remote()
    fake.raise_on["get_object"] = exc
    fake.raise_on["put_object"] = exc
    with pytest.raises(kind) as caught:
        remote.get_ref(PROJECT, "main")
    assert caught.value.code == code  # type: ignore[attr-defined]
    with pytest.raises(kind):
        remote.cas_ref(PROJECT, "main", None, _sha("c1"))
    text = str(caught.value)
    assert SECRET not in text
    assert "X-Amz-Signature" not in text
    assert caught.value.__cause__ is None or SECRET not in text


def test_head_errors_other_than_404_are_not_absence() -> None:
    remote, fake = _remote()
    fake.raise_on["head_object"] = _client_error("InternalError", 500, "HeadObject")
    with pytest.raises(RemoteUnavailableError):
        remote.has_objects(PROJECT, [_sha("o")])


# --- потолки чтения ------------------------------------------------------------


def test_declared_oversize_object_is_refused_unread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s3_mod, "MAX_OBJECT_BYTES", 16)
    remote, fake = _remote()
    sha = _sha("big")
    fake.objects[f"team/main/{PROJECT}/objects/{sha[:2]}/{sha[2:]}.zst"] = (b"x" * 64, '"e"')
    with pytest.raises(ObjectMissingError) as exc:
        remote.get_object(PROJECT, sha)
    assert exc.value.code == "SYNC-102"
    assert fake.last_body is not None
    assert fake.last_body.read_total == 0
    assert fake.last_body.closed


def test_body_longer_than_declared_is_cut_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s3_mod, "MAX_OBJECT_BYTES", 16)
    remote, fake = _remote()
    sha = _sha("liar")
    fake.objects[f"team/main/{PROJECT}/objects/{sha[:2]}/{sha[2:]}.zst"] = (b"x" * 4096, '"e"')
    fake.declared_length = 8
    with pytest.raises(ObjectMissingError):
        remote.get_object(PROJECT, sha)
    assert fake.last_body is not None
    assert fake.last_body.read_total <= 17


def test_oversize_ref_is_refused() -> None:
    remote, fake = _remote()
    fake.objects[f"team/main/{PROJECT}/refs/main"] = (b"a" * 4096, '"e"')
    with pytest.raises(ObjectMissingError):
        remote.get_ref(PROJECT, "main")


def test_garbage_ref_is_sync_102() -> None:
    remote, fake = _remote()
    fake.objects[f"team/main/{PROJECT}/refs/main"] = (b"HEAD\n", '"e"')
    with pytest.raises(ObjectMissingError):
        remote.get_ref(PROJECT, "main")


def test_non_object_json_is_sync_102() -> None:
    remote, fake = _remote()
    fake.objects[f"team/main/{PROJECT}/meta.json"] = (b"[1, 2]", '"e"')
    with pytest.raises(ObjectMissingError):
        remote.get_json(PROJECT, "meta.json")


def test_missing_object_is_sync_102() -> None:
    remote, _fake = _remote()
    with pytest.raises(ObjectMissingError):
        remote.get_object(PROJECT, _sha("none"))
