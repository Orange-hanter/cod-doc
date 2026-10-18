"""``S3Remote`` — remote в S3-совместимом бакете, ``s3://bucket/prefix`` (RFC 29 §3.5, CLO-028).

Раскладка та же, что у ``FileRemote``, под префиксом URL:
``<prefix>/<project_uid>/refs/main``, ``…/objects/ab/cdef….zst`` и т. д.
Ключ собирается только из сегментов, прошедших ``check_name``/``check_sha``
(те же валидаторы, что у ``FileRemote``), — ``..``, ``/`` и пустой сегмент в
ключ не попадают ни из аргументов, ни из префикса URL.

**CAS — conditional PUT.** Создание ref'а или JSON — ``PutObject`` с
``If-None-Match: *``, замена — с ``If-Match: <ETag>``, удаление аренды —
PUT надгробия с ``If-Match`` (``DeleteObject`` условие в MinIO игнорирует, см.
``delete_json``). Хранилище само сравнивает и пишет атомарно;
проигравший гонку получает ``412 PreconditionFailed`` (или ``409
ConditionalRequestConflict``, если запись-соперник ещё идёт), и это ``None``,
а не исключение. ``If-Match`` на отсутствующий ключ — ``404``: ref не равен
``expected``, тоже ``None``. Сервер обязан так и отвечать: замороженный
bitnamilegacy/minio 2025.7.23 на такой PUT молча создаёт объект, и CAS от
призрачного ``expected`` проходил (контракт
``test_cas_rejects_expected_when_ref_absent`` ловит это на живом сервере;
MinIO RELEASE.2025-09-07 и новее отвечают верно). etag — ETag объекта как есть (в кавычках); для
PUT без multipart и без SSE-KMS это MD5 тела, и ABA (X → Y → X) CAS пропускает
— так же, как ``FileRemote`` с sha256, и по той же причине это безопасно.

Объекты и снапшоты пишутся с ``If-None-Match: *``: существующий адрес не
перезаписывается, и проверка «есть ли» не отделена от записи.

**Повтор после таймаута.** botocore повторяет запрос, ответ на который не
дошёл. Если первый conditional PUT ref'а на самом деле прошёл, повтор получит
412, и ``cas_ref`` вернёт ``None`` при уже сдвинутой голове. Это ложный отказ,
не ложный успех: ``push`` ответит на него pull'ом, узнает свой changeset по
журналу и только зафиксирует его (тот же путь, что после сбоя между CAS и
фиксацией).

**Креды** — из ``credentials`` (``access_key``/``secret_key``/
``session_token``, ``endpoint_url``, ``region``; §10.12) или из стандартной
цепочки AWS (``AWS_ACCESS_KEY_ID``, ``AWS_ENDPOINT_URL``, ``~/.aws/…``). В URL
кредов быть не может: ``s3://key:secret@bucket`` отвергается, и URL в тексте
ошибки не повторяется. Текст ошибок транспорта — операция, ``s3://бакет/ключ``,
код и HTTP-статус ответа; ни сообщение сервера, ни строка исключения botocore
туда не попадают (в них бывают адреса запроса с подписью).

**Бакет — недоверенный**, как каталог ``FileRemote``: объект больше потолка
(по заявленному ``ContentLength`` — без чтения тела) — ``ObjectMissingError``
(SYNC-102); тело читается кусками не дальше ``потолок + 1`` байт, даже если
сервер прислал больше, чем заявил.

Ошибки (§10.5, §10.10): сеть, таймаут, 5xx — ``RemoteUnavailableError``
(SYNC-101); 401/403, нет кредов — ``RemoteAuthError`` (SYNC-201); ``SlowDown``
/ 429 — ``RemoteRateLimitedError`` (SYNC-204); нет бакета — ``RemoteConfigError``
(SYNC-110).
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Final, Self
from urllib.parse import urlparse

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)

from cod_doc.infra.remote.base import (
    JsonValue,
    RefValue,
    SnapshotRef,
    check_json_key,
    check_name,
    check_sha,
    check_snapshot,
    snapshot_key,
)
from cod_doc.infra.remote.errors import (
    ObjectMissingError,
    RemoteAuthError,
    RemoteConfigError,
    RemoteRateLimitedError,
    RemoteUnavailableError,
    SyncError,
)
from cod_doc.infra.remote.file import (
    MAX_JSON_BYTES,
    MAX_OBJECT_BYTES,
    MAX_REF_BYTES,
    MAX_SNAPSHOT_BYTES,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from mypy_boto3_s3 import S3Client

    from cod_doc.infra.remote.base import Credentials

__all__ = ["S3Remote", "parse_url"]

#: Имя бакета по правилам S3 (без подчёркиваний и заглавных, 3–63 символа).
_BUCKET_RE: Final = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_SNAPSHOT_SUFFIX: Final = ".db.zst"
_READ_CHUNK: Final = 1024 * 1024
#: Листинг снапшотов — не больше стольких страниц по 1000 ключей.
_MAX_LIST_PAGES: Final = 100
#: Повтор PUT объекта, пока соперник ещё пишет тот же адрес (409).
_PUT_CONFLICT_ATTEMPTS: Final = 3
_CONNECT_TIMEOUT_S: Final = 5
_READ_TIMEOUT_S: Final = 30
_CREDENTIAL_FIELDS: Final = frozenset(
    {"access_key", "secret_key", "session_token", "endpoint_url", "region"}
)
_DEFAULT_REGION: Final = "us-east-1"
#: Надгробие удалённого JSON-документа (см. ``S3Remote.delete_json``).
_TOMBSTONE: Final = b""

_HTTP_NOT_FOUND: Final = 404
_HTTP_CONFLICT: Final = 409
_HTTP_PRECONDITION: Final = 412
_HTTP_TOO_MANY: Final = 429
_HTTP_AUTH: Final = frozenset({401, 403})
_LOST_CAS: Final = frozenset({_HTTP_CONFLICT, _HTTP_PRECONDITION, _HTTP_NOT_FOUND})
_AUTH_CODES: Final = frozenset(
    {
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "InvalidToken",
        "TokenRefreshRequired",
    }
)
_MISSING_CODES: Final = frozenset({"NoSuchKey", "404", "NotFound"})


def _status(exc: ClientError) -> tuple[str, int]:
    error = exc.response.get("Error", {})
    meta = exc.response.get("ResponseMetadata", {})
    return str(error.get("Code", "")), int(meta.get("HTTPStatusCode", 0))


def _is_missing(exc: ClientError) -> bool:
    code, status = _status(exc)
    if code == "NoSuchBucket":
        return False
    return code in _MISSING_CODES or status == _HTTP_NOT_FOUND


def _translate(exc: BotoCoreError | ClientError, where: str) -> SyncError:
    """Ошибка botocore → код §10.10; текст без сообщения сервера и строки исключения."""
    if isinstance(exc, NoCredentialsError | PartialCredentialsError):
        return RemoteAuthError(f"{where}: нет S3-кредов ({type(exc).__name__})")
    if isinstance(exc, BotoCoreError):
        return RemoteUnavailableError(f"{where}: {type(exc).__name__}")
    code, status = _status(exc)
    detail = f"{where}: {code or '?'} (HTTP {status})"
    if code in _AUTH_CODES or status in _HTTP_AUTH:
        return RemoteAuthError(detail)
    if code == "SlowDown" or status == _HTTP_TOO_MANY:
        return RemoteRateLimitedError(detail)
    if code == "NoSuchBucket":
        return RemoteConfigError(detail)
    return RemoteUnavailableError(detail)


def _dumps(body: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def parse_url(url: str) -> tuple[str, str]:
    """``(бакет, префикс)``; URL в тексте ошибки — без userinfo."""
    parsed = urlparse(url)
    if parsed.scheme != "s3":
        msg = "remote: нужен URL вида s3://бакет/префикс"
        raise RemoteConfigError(msg)
    if "@" in parsed.netloc:
        msg = "remote s3://: креды в URL недопустимы — ~/.cod-doc/credentials или цепочка AWS"
        raise RemoteConfigError(msg)
    shown = f"s3://{parsed.netloc}{parsed.path}"
    bucket = parsed.netloc
    if _BUCKET_RE.fullmatch(bucket) is None:
        msg = f"remote {shown!r}: недопустимое имя бакета {bucket!r}"
        raise RemoteConfigError(msg)
    if parsed.query or parsed.fragment or parsed.params:
        msg = f"remote {shown!r}: параметры и фрагмент в s3-URL не поддерживаются"
        raise RemoteConfigError(msg)
    path = parsed.path.removeprefix("/").removesuffix("/")
    if path:
        try:
            for part in path.split("/"):
                check_name(part, "сегмент префикса")
        except ValueError as exc:
            msg = f"remote {shown!r}: {exc}"
            raise RemoteConfigError(msg) from None
    return bucket, path


def _make_client(credentials: Credentials | None) -> S3Client:
    import boto3
    from botocore.config import Config

    creds = dict(credentials or {})
    unknown = sorted(set(creds) - _CREDENTIAL_FIELDS)
    if unknown:
        msg = f"S3-креды: незнакомые поля {unknown}, ожидаются {sorted(_CREDENTIAL_FIELDS)}"
        raise RemoteConfigError(msg)
    access, secret = creds.get("access_key"), creds.get("secret_key")
    if (access is None) != (secret is None) or ("session_token" in creds and access is None):
        msg = "S3-креды: access_key и secret_key задаются вместе"
        raise RemoteConfigError(msg)
    session = boto3.session.Session(
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        aws_session_token=creds.get("session_token"),
        region_name=creds.get("region"),
    )
    endpoint = creds.get("endpoint_url")
    custom_endpoint = bool(
        endpoint or os.environ.get("AWS_ENDPOINT_URL_S3") or os.environ.get("AWS_ENDPOINT_URL")
    )
    config = Config(
        connect_timeout=_CONNECT_TIMEOUT_S,
        read_timeout=_READ_TIMEOUT_S,
        retries={"mode": "standard"},
        # MinIO и прочие S3-совместимые — path-style: имя бакета не в DNS.
        s3={"addressing_style": "path"} if custom_endpoint else None,
        # Целостность объекта сверяет changeset.decode по sha; контрольные суммы
        # boto3 ≥ 1.36 по умолчанию ломают часть S3-совместимых серверов.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return session.client(
        "s3",
        endpoint_url=endpoint,
        region_name=session.region_name or _DEFAULT_REGION,
        config=config,
    )


class S3Remote:
    """``RemoteStore`` поверх бакета S3; клиент boto3 потокобезопасен."""

    def __init__(self, client: S3Client, bucket: str, prefix: str) -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    @classmethod
    def from_url(cls, url: str, *, credentials: Credentials | None = None) -> Self:
        """Сети не трогает: клиент создаётся, запросов нет до первой операции."""
        bucket, prefix = parse_url(url)
        return cls(_make_client(credentials), bucket, prefix)

    # --- ключи ----------------------------------------------------------------

    def _key(self, project_uid: str, *parts: str) -> str:
        segments = (check_name(project_uid, "project_uid"), *parts)
        return "/".join((self.prefix, *segments) if self.prefix else segments)

    def _ref_key(self, project_uid: str, name: str) -> str:
        return self._key(project_uid, "refs", check_name(name, "ref"))

    def _object_key(self, project_uid: str, sha: str) -> str:
        check_sha(sha)
        return self._key(project_uid, "objects", sha[:2], f"{sha[2:]}.zst")

    def _json_key(self, project_uid: str, key: str) -> str:
        return self._key(project_uid, *check_json_key(key))

    def _snapshot_key(self, project_uid: str, ref: SnapshotRef) -> str:
        return self._key(project_uid, *check_snapshot(ref).key.split("/"))

    def _where(self, op: str, key: str) -> str:
        return f"{op} s3://{self.bucket}/{key}"

    # --- примитивы ------------------------------------------------------------

    def _get(self, key: str, cap: int) -> tuple[bytes, str] | None:
        """``(байты, etag)`` или ``None``; больше ``cap`` — SYNC-102."""
        where = self._where("GET", key)
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise _translate(exc, where) from None
        except BotoCoreError as exc:
            raise _translate(exc, where) from None
        body = resp["Body"]
        try:
            declared = int(resp.get("ContentLength", 0))
            if declared > cap:
                msg = f"{where}: {declared} байт больше потолка {cap}"
                raise ObjectMissingError(msg)
            chunks: list[bytes] = []
            total = 0
            while total <= cap:
                chunk = body.read(min(_READ_CHUNK, cap + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > cap:
                msg = f"{where}: тело больше потолка {cap} байт"
                raise ObjectMissingError(msg)
        except BotoCoreError as exc:
            raise _translate(exc, where) from None
        finally:
            body.close()
        return b"".join(chunks), str(resp.get("ETag", ""))

    def _exists(self, key: str) -> bool:
        where = self._where("HEAD", key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return False
            raise _translate(exc, where) from None
        except BotoCoreError as exc:
            raise _translate(exc, where) from None
        return True

    def _conditional_put(self, key: str, data: bytes, expected_etag: str | None) -> str | None:
        """PUT с ``If-None-Match: *`` или ``If-Match``; ``None`` — условие не выполнено."""
        where = self._where("PUT", key)
        try:
            if expected_etag is None:
                resp = self.client.put_object(
                    Bucket=self.bucket, Key=key, Body=data, IfNoneMatch="*"
                )
            else:
                resp = self.client.put_object(
                    Bucket=self.bucket, Key=key, Body=data, IfMatch=expected_etag
                )
        except ClientError as exc:
            code, status = _status(exc)
            if code != "NoSuchBucket" and status in _LOST_CAS:
                return None
            raise _translate(exc, where) from None
        except BotoCoreError as exc:
            raise _translate(exc, where) from None
        return str(resp.get("ETag", ""))

    def _put_immutable(self, key: str, data: bytes) -> None:
        """Записать неизменяемый объект с ``If-None-Match: *``; адрес занят — no-op.

        409 — соперник ещё пишет тот же адрес: повторяем, пока он не допишет.
        """
        where = self._where("PUT", key)
        for _ in range(_PUT_CONFLICT_ATTEMPTS):
            try:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=data, IfNoneMatch="*")
            except ClientError as exc:
                code, status = _status(exc)
                if code != "NoSuchBucket" and status == _HTTP_PRECONDITION:
                    return
                if status == _HTTP_CONFLICT:
                    continue
                raise _translate(exc, where) from None
            except BotoCoreError as exc:
                raise _translate(exc, where) from None
            return
        msg = f"{where}: соперник не дописал объект за {_PUT_CONFLICT_ATTEMPTS} попытки"
        raise RemoteUnavailableError(msg)

    # --- refs -----------------------------------------------------------------

    def get_ref(self, project_uid: str, name: str) -> RefValue | None:
        got = self._get(self._ref_key(project_uid, name), MAX_REF_BYTES)
        if got is None:
            return None
        data, etag = got
        sha = data.decode("ascii", errors="replace").strip()
        try:
            check_sha(sha)
        except ValueError as exc:
            msg = f"ref {name} проекта {project_uid} повреждён: {exc}"
            raise ObjectMissingError(msg) from None
        return RefValue(sha=sha, etag=etag)

    def cas_ref(
        self, project_uid: str, name: str, expected: RefValue | None, new_sha: str
    ) -> RefValue | None:
        key = self._ref_key(project_uid, name)
        data = f"{check_sha(new_sha, 'new_sha')}\n".encode("ascii")
        etag = self._conditional_put(key, data, None if expected is None else expected.etag)
        return None if etag is None else RefValue(sha=new_sha, etag=etag)

    # --- objects --------------------------------------------------------------

    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]:
        keys = {sha: self._object_key(project_uid, sha) for sha in shas}
        return {sha for sha, key in keys.items() if self._exists(key)}

    def put_object(self, project_uid: str, sha: str, data: bytes) -> None:
        self._put_immutable(self._object_key(project_uid, sha), data)

    def get_object(self, project_uid: str, sha: str) -> bytes:
        got = self._get(self._object_key(project_uid, sha), MAX_OBJECT_BYTES)
        if got is None:
            msg = f"объекта {sha} нет в проекте {project_uid}"
            raise ObjectMissingError(msg)
        return got[0]

    # --- json -----------------------------------------------------------------

    def get_json(self, project_uid: str, key: str) -> tuple[dict[str, JsonValue], str] | None:
        got = self._get(self._json_key(project_uid, key), MAX_JSON_BYTES)
        if got is None or got[0] == _TOMBSTONE:
            return None
        data, etag = got
        try:
            body: object = json.loads(data.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            msg = f"{key} проекта {project_uid}: не JSON: {type(exc).__name__}"
            raise ObjectMissingError(msg) from None
        if not isinstance(body, dict):
            msg = f"{key} проекта {project_uid}: нужен JSON-объект"
            raise ObjectMissingError(msg)
        return body, etag

    def cas_json(
        self,
        project_uid: str,
        key: str,
        expected_etag: str | None,
        body: Mapping[str, JsonValue],
    ) -> str | None:
        """Создание — ``If-None-Match: *``, а поверх надгробия — ``If-Match``
        его ETag: оба шага условные, гонка двух создателей даёт одного победителя."""
        s3_key = self._json_key(project_uid, key)
        data = _dumps(body)
        if len(data) > MAX_JSON_BYTES:
            msg = f"{key}: тело {len(data)} байт больше потолка {MAX_JSON_BYTES}"
            raise ValueError(msg)
        if expected_etag is not None:
            return self._conditional_put(s3_key, data, expected_etag)
        etag = self._conditional_put(s3_key, data, None)
        if etag is not None:
            return etag
        current = self._get(s3_key, MAX_JSON_BYTES)
        if current is None or current[0] != _TOMBSTONE:
            return None
        return self._conditional_put(s3_key, data, current[1])

    def delete_json(self, project_uid: str, key: str, expected_etag: str) -> bool:
        """Удаление — conditional PUT надгробия поверх ``expected_etag``.

        ``DeleteObject`` с ``If-Match`` MinIO (RELEASE.2025-09-07) принимает и
        молча игнорирует условие — проверено на живом сервере: удаление по
        устаревшему etag проходило. А безусловное удаление после проверки —
        TOCTOU: между HEAD и DELETE соперник успевает записать новую аренду, и
        её снесли бы. Поэтому ключ не удаляется вовсе: пустое тело —
        надгробие, ``get_json`` видит его как «ключа нет», ``cas_json`` с
        ``expected_etag=None`` пишет поверх него условно. Тело JSON-документа
        пустым не бывает (минимум ``{}``), так что надгробие с ним не спутать.
        """
        s3_key = self._json_key(project_uid, key)
        current = self._get(s3_key, MAX_JSON_BYTES)
        if current is None or current[0] == _TOMBSTONE or current[1] != expected_etag:
            return False
        return self._conditional_put(s3_key, _TOMBSTONE, expected_etag) is not None

    # --- snapshots ------------------------------------------------------------

    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None:
        """Самая поздняя эпоха по имени, внутри — по ``LastModified`` (секунды
        у S3), ничья — по sha. Ключи не по раскладке пропускаются."""
        base = f"{self._key(project_uid, 'snapshots')}/"
        where = self._where("LIST", base)
        best: tuple[str, datetime, str] | None = None
        token: str | None = None
        for _ in range(_MAX_LIST_PAGES):
            try:
                if token is None:
                    page = self.client.list_objects_v2(Bucket=self.bucket, Prefix=base)
                else:
                    page = self.client.list_objects_v2(
                        Bucket=self.bucket, Prefix=base, ContinuationToken=token
                    )
            except (ClientError, BotoCoreError) as exc:
                raise _translate(exc, where) from None
            for item in page.get("Contents", []):
                epoch, _, name = item.get("Key", "").removeprefix(base).partition("/")
                sha = name.removesuffix(_SNAPSHOT_SUFFIX)
                if sha == name or "LastModified" not in item:
                    continue
                try:
                    check_name(epoch, "epoch")
                    check_sha(sha)
                except ValueError:
                    continue
                candidate = (epoch, item["LastModified"], sha)
                if best is None or candidate > best:
                    best = candidate
            token = page.get("NextContinuationToken")
            if not page.get("IsTruncated") or token is None:
                break
        else:
            msg = f"{where}: больше {_MAX_LIST_PAGES} страниц листинга"
            raise RemoteUnavailableError(msg)
        if best is None:
            return None
        epoch, _, sha = best
        return SnapshotRef(epoch=epoch, sha=sha, key=snapshot_key(epoch, sha))

    def put_snapshot(self, project_uid: str, ref: SnapshotRef, data: bytes) -> None:
        self._put_immutable(self._snapshot_key(project_uid, ref), data)

    def get_snapshot(self, project_uid: str, ref: SnapshotRef) -> bytes:
        got = self._get(self._snapshot_key(project_uid, ref), MAX_SNAPSHOT_BYTES)
        if got is None:
            msg = f"снапшота {ref.key} нет в проекте {project_uid}"
            raise ObjectMissingError(msg)
        return got[0]
