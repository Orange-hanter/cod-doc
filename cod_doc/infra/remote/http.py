"""``HttpRemote`` — умный remote ``https://host`` поверх ``/api/sync/v1`` (RFC 29 §10.9, CLO-035).

Сервер (``cod-doc serve``, маршруты ``api/sync``) хранит объекты в своём
``FileRemote``; этот транспорт только переводит протокол ``RemoteStore`` в HTTP.
URL remote — корень сервера (``https://cod-doc.example.dev``); пути
``/api/sync/v1/projects/<uid>/…`` дописываются здесь. ``http://`` разрешён
только для loopback — токен по открытому каналу наружу не уходит.

**Токен** — поле ``token`` кредов ``https://…`` (``~/.cod-doc/credentials``), иначе
Keychain macOS (``infra/keychain``); оба пишет ``cod-doc remote login``. Без кредов запрос идёт без
``Authorization`` и получает 401 — ``RemoteAuthError`` (``SYNC-201``).

Ответы → исключения §10.10: 401/403 — ``RemoteAuthError`` (``SYNC-201``/
``SYNC-202``), 422 с ``SYNC-203`` — тоже ``RemoteAuthError`` с этим кодом
(``AuthorMismatchError`` живёт в ``services``, а ``infra`` вверх не смотрит),
404 — ``None`` или ``ObjectMissingError``, 412 — CAS отвергнут (``None``), 429
— ``RemoteRateLimitedError``, сеть и 5xx — ``RemoteUnavailableError``. ``etag``
— значение заголовка ``ETag`` без кавычек.
"""

from __future__ import annotations

import ipaddress
import json
from typing import TYPE_CHECKING, Final, NoReturn, Self, cast
from urllib.parse import quote, urlparse

import httpx

from cod_doc.infra.remote.base import (
    MAX_LIST_KEYS,
    RefValue,
    SnapshotRef,
    check_json_key,
    check_json_prefix,
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
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from cod_doc.infra.remote.base import Credentials, JsonValue

__all__ = ["API_PREFIX", "HttpRemote"]

API_PREFIX: Final = "/api/sync/v1"
_TIMEOUT: Final = httpx.Timeout(30.0, read=300.0)
_OK: Final = 200
_CREATED: Final = 201
_NO_CONTENT: Final = 204
_UNAUTHORIZED: Final = 401
_FORBIDDEN: Final = 403
_NOT_FOUND: Final = 404
_PRECONDITION: Final = 412
_UNPROCESSABLE: Final = 422
_TOO_MANY: Final = 429
_SERVER_ERROR: Final = 500
_LOOPBACK_NAMES: Final = frozenset({"localhost"})


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _unquote(etag: str | None) -> str | None:
    if etag is None:
        return None
    etag = etag.removeprefix("W/")
    return etag[1:-1] if len(etag) >= 2 and etag[0] == etag[-1] == '"' else etag  # noqa: PLR2004


class HttpRemote:
    """``RemoteStore`` через REST умного remote."""

    def __init__(
        self, base_url: str, *, token: str | None, client: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = client or httpx.Client(timeout=_TIMEOUT)

    @classmethod
    def from_url(cls, url: str, *, credentials: Credentials | None = None) -> Self:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            msg = f"remote {url!r}: нужен https://хост"
            raise RemoteConfigError(msg)
        if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
            msg = f"remote {url!r}: http:// только для loopback — токен не идёт открытым каналом"
            raise RemoteConfigError(msg)
        if parsed.query or parsed.fragment:
            msg = f"remote {url!r}: query и fragment в URL remote не нужны"
            raise RemoteConfigError(msg)
        if credentials is None:
            from cod_doc.infra.credentials import credentials_for

            credentials = credentials_for(url)
        token = (credentials or {}).get("token")
        if token is None:
            from cod_doc.infra import keychain

            token = keychain.read_token(url)
        return cls(url, token=token)

    # --- HTTP -----------------------------------------------------------------

    def _url(self, project_uid: str, *parts: str) -> str:
        check_name(project_uid, "project_uid")
        tail = "/".join(quote(p, safe="") for p in parts)
        return f"{self.base_url}{API_PREFIX}/projects/{project_uid}/{tail}"

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        json_body: object = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        all_headers = dict(headers or {})
        if self.token:
            all_headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._client.request(
                method,
                url,
                headers=all_headers,
                content=content,
                json=json_body,
                params=params,
            )
        except httpx.HTTPError as exc:
            msg = f"remote {self.base_url}: {type(exc).__name__}"
            raise RemoteUnavailableError(msg) from exc
        self._raise_for(response)
        return response

    @staticmethod
    def _error(response: httpx.Response) -> tuple[str | None, str]:
        try:
            err = response.json().get("error", {})
            return err.get("code"), str(err.get("message", ""))
        except ValueError, AttributeError:
            return None, response.reason_phrase

    def _raise_for(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in (_UNAUTHORIZED, _FORBIDDEN):
            code, message = self._error(response)
            default = "SYNC-201" if status == _UNAUTHORIZED else "SYNC-202"
            raise RemoteAuthError(message or "доступ запрещён", code=code or default)
        if status == _UNPROCESSABLE:
            code, message = self._error(response)
            if code == "SYNC-203":
                raise RemoteAuthError(message, code=code)
            raise RemoteConfigError(message or "remote отверг запрос", code=code or "SYNC-104")
        if status == _TOO_MANY:
            retry = response.headers.get("retry-after", "?")
            msg = f"remote {self.base_url}: частота запросов, повтор через {retry} с"
            raise RemoteRateLimitedError(msg)
        if status >= _SERVER_ERROR:
            msg = f"remote {self.base_url}: HTTP {status}"
            raise RemoteUnavailableError(msg)

    def _rejected(self, response: httpx.Response, what: str) -> NoReturn:
        """Сервер не принял запись: код и текст — из тела ответа (§10.9)."""
        code, message = self._error(response)
        text = f"{what}: {message or f'HTTP {response.status_code}'}"
        raise ObjectMissingError(text, code=code or "SYNC-102")

    # --- refs -----------------------------------------------------------------

    def get_ref(self, project_uid: str, name: str) -> RefValue | None:
        check_name(name, "ref")
        r = self._request("GET", self._url(project_uid, "refs", name))
        if r.status_code == _NOT_FOUND:
            return None
        return RefValue(sha=check_sha(r.json()["sha"]), etag=_unquote(r.headers["etag"]) or "")

    def cas_ref(
        self, project_uid: str, name: str, expected: RefValue | None, new_sha: str
    ) -> RefValue | None:
        check_name(name, "ref")
        check_sha(new_sha)
        cond = {"If-None-Match": "*"} if expected is None else {"If-Match": f'"{expected.etag}"'}
        r = self._request(
            "PUT", self._url(project_uid, "refs", name), headers=cond, json_body={"sha": new_sha}
        )
        if r.status_code in (_PRECONDITION, _NOT_FOUND):
            return None
        return RefValue(sha=new_sha, etag=_unquote(r.headers["etag"]) or "")

    # --- objects --------------------------------------------------------------

    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]:
        wanted = [check_sha(s) for s in shas]
        if not wanted:
            return set()
        r = self._request(
            "POST", self._url(project_uid, "objects", "has"), json_body={"shas": wanted}
        )
        return set(r.json()["present"]) & set(wanted)

    def put_object(self, project_uid: str, sha: str, data: bytes) -> None:
        check_sha(sha)
        r = self._request("PUT", self._url(project_uid, "objects", sha), content=data)
        if r.status_code not in (_OK, _CREATED):
            self._rejected(r, f"объект {sha}")

    def get_object(self, project_uid: str, sha: str) -> bytes:
        check_sha(sha)
        r = self._request("GET", self._url(project_uid, "objects", sha))
        if r.status_code != _OK:
            msg = f"объекта {sha} на remote нет"
            raise ObjectMissingError(msg)
        return r.content

    # --- JSON -----------------------------------------------------------------

    def _json_url(self, project_uid: str, key: str) -> str:
        parts = check_json_key(key)
        return self._url(project_uid, "json", *parts)

    def get_json(self, project_uid: str, key: str) -> tuple[dict[str, JsonValue], str] | None:
        r = self._request("GET", self._json_url(project_uid, key))
        if r.status_code == _NOT_FOUND:
            return None
        body = r.json()
        if not isinstance(body, dict):
            msg = f"{key}: на remote не JSON-объект"
            raise ObjectMissingError(msg)
        return cast("dict[str, JsonValue]", body), _unquote(r.headers["etag"]) or ""

    def cas_json(
        self,
        project_uid: str,
        key: str,
        expected_etag: str | None,
        body: Mapping[str, JsonValue],
    ) -> str | None:
        cond = (
            {"If-None-Match": "*"} if expected_etag is None else {"If-Match": f'"{expected_etag}"'}
        )
        r = self._request(
            "PUT",
            self._json_url(project_uid, key),
            headers={**cond, "Content-Type": "application/json"},
            content=json.dumps(dict(body), ensure_ascii=False).encode(),
        )
        if r.status_code in (_PRECONDITION, _NOT_FOUND):
            return None
        return _unquote(r.headers["etag"])

    def delete_json(self, project_uid: str, key: str, expected_etag: str) -> bool:
        r = self._request(
            "DELETE", self._json_url(project_uid, key), headers={"If-Match": f'"{expected_etag}"'}
        )
        return r.status_code == _NO_CONTENT

    def list_prefix(self, project_uid: str, prefix: str) -> list[str]:
        check_json_prefix(prefix)
        r = self._request("GET", self._url(project_uid, "json"), params={"prefix": prefix})
        keys = r.json()["keys"]
        if len(keys) > MAX_LIST_KEYS:
            msg = f"под {prefix} больше {MAX_LIST_KEYS} ключей"
            raise RemoteUnavailableError(msg)
        return [str(k) for k in keys]

    # --- снапшоты -------------------------------------------------------------

    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None:
        r = self._request("GET", self._url(project_uid, "snapshots", "latest"))
        if r.status_code == _NOT_FOUND:
            return None
        body = r.json()
        epoch, sha = check_name(body["epoch"], "epoch"), check_sha(body["sha"])
        return SnapshotRef(epoch=epoch, sha=sha, key=snapshot_key(epoch, sha))

    def put_snapshot(self, project_uid: str, ref: SnapshotRef, data: bytes) -> None:
        check_snapshot(ref)
        r = self._request(
            "PUT", self._url(project_uid, "snapshots", ref.epoch, ref.sha), content=data
        )
        if r.status_code not in (_OK, _CREATED):
            self._rejected(r, f"снапшот {ref.key}")

    def get_snapshot(self, project_uid: str, ref: SnapshotRef) -> bytes:
        check_snapshot(ref)
        r = self._request("GET", self._url(project_uid, "snapshots", ref.epoch, ref.sha))
        if r.status_code != _OK:
            msg = f"снапшота {ref.key} на remote нет"
            raise ObjectMissingError(msg)
        return r.content

    # --- доступ (CLO-038) -----------------------------------------------------

    def admin(self, method: str, project_uid: str, *parts: str, body: object = None) -> object:
        """Вызов маршрута доступа (``members``/``tokens``); тело ответа или ``None`` (204)."""
        r = self._request(method, self._url(project_uid, *parts), json_body=body)
        if r.status_code == _NOT_FOUND:
            code, message = self._error(r)
            raise ObjectMissingError(message or "не найдено", code=code or "SYNC-102")
        if r.status_code >= _FORBIDDEN:
            code, message = self._error(r)
            raise RemoteConfigError(message or f"HTTP {r.status_code}", code=code or "SYNC-110")
        return None if r.status_code == _NO_CONTENT else r.json()
