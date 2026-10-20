"""Маршруты умного remote ``/api/sync/v1`` (RFC 29 §10.9; CLO-035, CLO-037, CLO-038).

Каждый маршрут — один вызов ``RemoteStore`` хранилища сервера
(``sync_service.server.store``) после проверки роли. Гейт ``api/auth.py`` уже
отбил запрос без живого токена; здесь — роль в проекте из пути.

Ошибки — ``{"error": {"code": "SYNC-NNN", "message", "details"}}``, а не
FastAPI-шный ``{"detail"}``: клиент (``infra/remote/http.HttpRemote``) решает
по коду. CAS-отказ — 412, как условный запрос HTTP; ``ETag`` — непрозрачная
метка версии ``RemoteStore`` в кавычках.

Сверх таблицы §10.9 (её не хватало протоколу ``RemoteStore`` и доступу CLO-038):

- ``GET /projects/{uid}/json?prefix=leases/…/`` — ``list_prefix``;
- ``GET /projects/{uid}/snapshots/{epoch}/{sha}`` — тело снапшота по адресу;
- ``meta.json`` пишет и writer через ``PUT json/meta.json``: первый push новой
  эпохи поднимает ``epoch``/``schema_min`` тем же CAS (CLO-032); ``PUT meta`` —
  ручная правка ``schema_min`` admin'ом;
- ``members`` и ``tokens`` — администрирование доступа (admin проекта). Токен
  выпускается только себе или своему агенту: выпуск чужого токена дал бы admin'у
  одного проекта все роли владельца в других.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Final

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from cod_doc.services.sync_service import server
from cod_doc.services.sync_service.server import ROLE_ADMIN, ROLE_READER, ROLE_WRITER

if TYPE_CHECKING:
    from collections.abc import Callable

router = APIRouter(prefix="/api/sync/v1", tags=["sync"])

_HTTP_BAD_REQUEST: Final = 400
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_FORBIDDEN: Final = 403
_HTTP_NOT_FOUND: Final = 404
_HTTP_PRECONDITION: Final = 412
_HTTP_TOO_LARGE: Final = 413
_HTTP_UNPROCESSABLE: Final = 422
_HTTP_BAD_GATEWAY: Final = 502
#: Код → HTTP-статус (§10.10). Незнакомый код — 400.
_STATUS_BY_CODE: Final = {
    "SYNC-101": _HTTP_BAD_GATEWAY,
    "SYNC-102": _HTTP_NOT_FOUND,
    "SYNC-104": _HTTP_UNPROCESSABLE,
    "SYNC-201": _HTTP_UNAUTHORIZED,
    "SYNC-202": _HTTP_FORBIDDEN,
    "SYNC-203": _HTTP_UNPROCESSABLE,
}
#: Потолок тела запроса: самый большой объект ``FileRemote`` — снапшот.
_OCTET: Final = "application/octet-stream"

UidPath = Annotated[str, "project.uid"]


def _error(status: int, code: str, message: str, **details: Any) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details}}, status_code=status
    )


def _quote(etag: str) -> str:
    return f'"{etag}"'


def _unquote(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value.startswith("W/"):
        value = value[2:]
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value  # noqa: PLR2004


def _expected(if_match: str | None, if_none_match: str | None) -> tuple[bool, str | None]:
    """``(задан ли CAS, ожидаемый etag)``: ``If-None-Match: *`` — «ключа нет» (``None``)."""
    if if_none_match is not None and if_none_match.strip() == "*":
        return True, None
    if if_match is not None:
        return True, _unquote(if_match)
    return False, None


def _guard(fn: Callable[[], Response]) -> Response:
    """Ошибки ``RemoteStore`` и доступа → тело §10.9."""
    from cod_doc.infra.remote.errors import SyncError
    from cod_doc.services.access_service import AccessError

    try:
        return fn()
    except SyncError as exc:
        status = _STATUS_BY_CODE.get(exc.code, _HTTP_BAD_REQUEST)
        return _error(status, exc.code, str(exc))
    except AccessError as exc:
        return _error(_HTTP_BAD_REQUEST, "SYNC-202", str(exc))
    except ValueError as exc:
        # Недопустимое имя/sha/ключ — ошибка вызывающего (base.check_*).
        return _error(_HTTP_BAD_REQUEST, "SYNC-110", str(exc))


async def _body(request: Request, cap: int) -> bytes | Response:
    """Тело потоком, не больше ``cap`` байт; зовётся **после** проверки роли.

    Чтение целиком (``request.body()``) держало бы в памяти любой объём до
    сравнения с потолком; нечисловой ``Content-Length`` — отказ сразу.
    """
    too_large = _error(_HTTP_TOO_LARGE, "SYNC-110", f"тело запроса больше {cap} байт")
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "Content-Length не число")
        if int(declared) > cap:
            return too_large
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            return too_large
        chunks.append(chunk)
    return b"".join(chunks)


def _role_first(project_uid: str, minimum: str) -> Response | None:
    """Роль — до чтения тела: аноним и reader не заставят сервер читать гигабайт.

    ``None`` — пускать; иначе готовый отказ. Маршрут всё равно повторяет
    ``require`` внутри ``run``: проверка дешёвая, а ``run`` не зависит от порядка.
    """
    from cod_doc.infra.remote.errors import SyncError

    try:
        _check_uid(project_uid)
        server.require(project_uid, minimum)
    except SyncError as exc:
        return _error(_STATUS_BY_CODE.get(exc.code, _HTTP_BAD_REQUEST), exc.code, str(exc))
    except ValueError as exc:
        return _error(_HTTP_BAD_REQUEST, "SYNC-110", str(exc))
    return None


def _check_uid(project_uid: str) -> None:
    from cod_doc.infra.remote.base import check_name

    check_name(project_uid, "project_uid")


# --- refs ---------------------------------------------------------------------


@router.get("/projects/{project_uid}/refs/{name}")
def get_ref(project_uid: UidPath, name: str) -> Response:
    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_READER)
        ref = server.store().get_ref(project_uid, name)
        if ref is None:
            return _error(_HTTP_NOT_FOUND, "SYNC-102", f"ref {name!r} нет")
        return JSONResponse({"sha": ref.sha}, headers={"ETag": _quote(ref.etag)})

    return _guard(run)


@router.put("/projects/{project_uid}/refs/{name}")
def put_ref(
    project_uid: UidPath,
    name: str,
    payload: Annotated[dict[str, Any], Body()],
    if_match: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    from cod_doc.infra.remote.base import RefValue

    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_WRITER)
        given, etag = _expected(if_match, if_none_match)
        if not given:
            return _error(_HTTP_PRECONDITION, "SYNC-110", "нужен If-Match или If-None-Match: *")
        sha = payload.get("sha")
        if not isinstance(sha, str):
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "тело: {sha}")
        store = server.store()
        expected = None
        if etag is not None:
            current = store.get_ref(project_uid, name)
            if current is None or current.etag != etag:
                return _error(_HTTP_PRECONDITION, "SYNC-103", "CAS ref отвергнут")
            expected = RefValue(sha=current.sha, etag=etag)
        new = store.cas_ref(project_uid, name, expected, sha)
        if new is None:
            return _error(_HTTP_PRECONDITION, "SYNC-103", "CAS ref отвергнут")
        return JSONResponse({"sha": new.sha}, headers={"ETag": _quote(new.etag)})

    return _guard(run)


# --- objects ------------------------------------------------------------------


@router.post("/projects/{project_uid}/objects/has")
def has_objects(project_uid: UidPath, payload: Annotated[dict[str, Any], Body()]) -> Response:
    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_READER)
        shas = payload.get("shas")
        if not isinstance(shas, list) or not all(isinstance(s, str) for s in shas):
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "тело: {shas: [...]}")
        present = server.store().has_objects(project_uid, shas)
        return JSONResponse({"present": sorted(present)})

    return _guard(run)


@router.put("/projects/{project_uid}/objects/{sha}")
async def put_object(project_uid: UidPath, sha: str, request: Request) -> Response:
    from cod_doc.infra.remote.file import MAX_OBJECT_BYTES

    if (denied := _role_first(project_uid, ROLE_WRITER)) is not None:
        return denied
    data = await _body(request, MAX_OBJECT_BYTES)
    if isinstance(data, Response):
        return data

    def run() -> Response:
        _check_uid(project_uid)
        identity = server.require(project_uid, ROLE_WRITER)
        store = server.store()
        if store.has_objects(project_uid, [sha]):
            return Response(status_code=200)
        server.check_changeset(identity, project_uid, sha, data)
        store.put_object(project_uid, sha, data)
        return Response(status_code=201)

    return _guard(run)


@router.get("/projects/{project_uid}/objects/{sha}")
def get_object(project_uid: UidPath, sha: str) -> Response:
    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_READER)
        return Response(server.store().get_object(project_uid, sha), media_type=_OCTET)

    return _guard(run)


# --- JSON-документы (аренды, meta.json) -----------------------------------------


@router.get("/projects/{project_uid}/json")
def list_json(project_uid: UidPath, prefix: Annotated[str, Query()]) -> Response:
    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_READER)
        return JSONResponse({"keys": server.store().list_prefix(project_uid, prefix)})

    return _guard(run)


@router.get("/projects/{project_uid}/json/{key:path}")
def get_json(project_uid: UidPath, key: str) -> Response:
    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_READER)
        found = server.store().get_json(project_uid, key)
        if found is None:
            return _error(_HTTP_NOT_FOUND, "SYNC-102", f"ключа {key!r} нет")
        body, etag = found
        return JSONResponse(body, headers={"ETag": _quote(etag)})

    return _guard(run)


@router.put("/projects/{project_uid}/json/{key:path}")
async def put_json(
    project_uid: UidPath,
    key: str,
    request: Request,
    if_match: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    from cod_doc.infra.remote.file import MAX_JSON_BYTES

    if (denied := _role_first(project_uid, ROLE_WRITER)) is not None:
        return denied
    raw = await _body(request, MAX_JSON_BYTES)
    if isinstance(raw, Response):
        return raw

    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_WRITER)
        given, etag = _expected(if_match, if_none_match)
        if not given:
            return _error(_HTTP_PRECONDITION, "SYNC-110", "нужен If-Match или If-None-Match: *")
        try:
            body = json.loads(raw)
        except ValueError:
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "тело — не JSON")
        if not isinstance(body, dict):
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "тело — JSON-объект")
        new = server.store().cas_json(project_uid, key, etag, body)
        if new is None:
            return _error(_HTTP_PRECONDITION, "SYNC-103", "CAS JSON отвергнут")
        return Response(status_code=200, headers={"ETag": _quote(new)})

    return _guard(run)


@router.delete("/projects/{project_uid}/json/{key:path}")
def delete_json(
    project_uid: UidPath, key: str, if_match: Annotated[str | None, Header()] = None
) -> Response:
    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_WRITER)
        etag = _unquote(if_match)
        if etag is None:
            return _error(_HTTP_PRECONDITION, "SYNC-110", "нужен If-Match")
        if not server.store().delete_json(project_uid, key, etag):
            return _error(_HTTP_PRECONDITION, "SYNC-103", "CAS JSON отвергнут")
        return Response(status_code=204)

    return _guard(run)


@router.get("/projects/{project_uid}/meta")
def get_meta(project_uid: UidPath) -> Response:
    return get_json(project_uid, "meta.json")


@router.put("/projects/{project_uid}/meta")
def put_meta(project_uid: UidPath, payload: Annotated[dict[str, Any], Body()]) -> Response:
    """admin: поднять ``schema_min`` (§10.9). CAS по текущему ``meta.json``."""

    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_ADMIN)
        schema_min = payload.get("schema_min")
        if not isinstance(schema_min, str):
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "тело: {schema_min}")
        store = server.store()
        found = store.get_json(project_uid, "meta.json")
        if found is None:
            return _error(_HTTP_NOT_FOUND, "SYNC-102", "meta.json нет — remote пуст")
        body, etag = found
        new = store.cas_json(project_uid, "meta.json", etag, {**body, "schema_min": schema_min})
        if new is None:
            return _error(_HTTP_PRECONDITION, "SYNC-103", "meta.json изменился, повторите")
        return JSONResponse({**body, "schema_min": schema_min}, headers={"ETag": _quote(new)})

    return _guard(run)


# --- снапшоты -----------------------------------------------------------------


@router.get("/projects/{project_uid}/snapshots/latest")
def latest_snapshot(project_uid: UidPath) -> Response:
    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_READER)
        ref = server.store().latest_snapshot(project_uid)
        if ref is None:
            return _error(_HTTP_NOT_FOUND, "SYNC-102", "снапшотов нет")
        return JSONResponse({"epoch": ref.epoch, "sha": ref.sha})

    return _guard(run)


@router.get("/projects/{project_uid}/snapshots/{epoch}/{sha}")
def get_snapshot(project_uid: UidPath, epoch: str, sha: str) -> Response:
    from cod_doc.infra.remote.base import SnapshotRef, snapshot_key

    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_READER)
        ref = SnapshotRef(epoch=epoch, sha=sha, key=snapshot_key(epoch, sha))
        return Response(server.store().get_snapshot(project_uid, ref), media_type=_OCTET)

    return _guard(run)


@router.put("/projects/{project_uid}/snapshots/{epoch}/{sha}")
async def put_snapshot(project_uid: UidPath, epoch: str, sha: str, request: Request) -> Response:
    from cod_doc.infra.remote.base import SnapshotRef, snapshot_key
    from cod_doc.infra.remote.file import MAX_SNAPSHOT_BYTES

    if (denied := _role_first(project_uid, ROLE_WRITER)) is not None:
        return denied
    data = await _body(request, MAX_SNAPSHOT_BYTES)
    if isinstance(data, Response):
        return data

    def run() -> Response:
        _check_uid(project_uid)
        server.require(project_uid, ROLE_WRITER)
        ref = SnapshotRef(epoch=epoch, sha=sha, key=snapshot_key(epoch, sha))
        store = server.store()
        # Снапшот адресуется головой: она обязана быть проверенным changeset'ом
        # этого проекта (PUT objects прошёл check_changeset). Содержимое клиент
        # сверяет сам при загрузке — дайджест против головы (snapshot/clone).
        if not store.has_objects(project_uid, [sha]):
            return _error(
                _HTTP_UNPROCESSABLE,
                "SYNC-102",
                f"снапшот на {sha}: такого changeset'а на remote нет",
            )
        store.put_snapshot(project_uid, ref, data)
        return Response(status_code=201)

    return _guard(run)


# --- доступ (CLO-038): участники и токены -----------------------------------------


@router.get("/projects/{project_uid}/members")
def list_members(project_uid: UidPath) -> Response:
    from cod_doc.services import access_service

    def run() -> Response:
        _check_uid(project_uid)
        server.require_human(project_uid, ROLE_ADMIN)
        with access_service.store_session() as session:
            rows = access_service.list_members(session, project_uid)
        return JSONResponse([{"handle": r.handle, "kind": r.kind, "role": r.role} for r in rows])

    return _guard(run)


@router.put("/projects/{project_uid}/members/{handle:path}")
def put_member(
    project_uid: UidPath, handle: str, payload: Annotated[dict[str, Any], Body()]
) -> Response:
    from cod_doc.services import access_service

    def run() -> Response:
        _check_uid(project_uid)
        admin = server.require_human(project_uid, ROLE_ADMIN)
        role = payload.get("role")
        if not isinstance(role, str):
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "тело: {role}")
        with access_service.store_session() as session:
            row = access_service.add_member(
                session,
                handle=handle,
                project_uid=project_uid,
                role=role,
                author=f"{admin.kind}:{admin.handle}",
            )
        return JSONResponse({"handle": row.handle, "kind": row.kind, "role": row.role})

    return _guard(run)


@router.delete("/projects/{project_uid}/members/{handle:path}")
def delete_member(project_uid: UidPath, handle: str) -> Response:
    from cod_doc.services import access_service

    def run() -> Response:
        _check_uid(project_uid)
        admin = server.require_human(project_uid, ROLE_ADMIN)
        with access_service.store_session() as session:
            removed = access_service.remove_member(
                session,
                handle=handle,
                project_uid=project_uid,
                author=f"{admin.kind}:{admin.handle}",
            )
        return Response(status_code=204 if removed else _HTTP_NOT_FOUND)

    return _guard(run)


@router.get("/projects/{project_uid}/tokens")
def list_tokens(project_uid: UidPath) -> Response:
    """Свои токены и токены своих агентов (без открытого текста)."""
    from cod_doc.services import access_service

    def run() -> Response:
        _check_uid(project_uid)
        me = server.require_human(project_uid, ROLE_ADMIN)
        with access_service.store_session() as session:
            rows = [r for r in access_service.list_tokens(session) if r.handle in me.authors]
        return JSONResponse(
            [
                {
                    "uid": r.uid,
                    "handle": r.handle,
                    "label": r.label,
                    "created": r.created.isoformat(),
                    "last_used": r.last_used.isoformat() if r.last_used else None,
                    "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                }
                for r in rows
            ]
        )

    return _guard(run)


@router.post("/projects/{project_uid}/tokens")
def create_token(project_uid: UidPath, payload: Annotated[dict[str, Any], Body()]) -> Response:
    """Токен себе или своему агенту: открытый текст — в ответе, один раз."""
    from cod_doc.services import access_service

    def run() -> Response:
        _check_uid(project_uid)
        me = server.require_human(project_uid, ROLE_ADMIN)
        handle = payload.get("handle") or me.handle
        label = payload.get("label")
        if not isinstance(handle, str) or not isinstance(label, str):
            return _error(_HTTP_BAD_REQUEST, "SYNC-110", "тело: {label, handle?}")
        if handle not in me.authors:
            return _error(
                _HTTP_FORBIDDEN,
                "SYNC-202",
                f"токен выпускается только себе или своему агенту, не {handle!r}",
            )
        with access_service.store_session() as session:
            issued = access_service.create_token(
                session, handle=handle, label=label, author=f"{me.kind}:{me.handle}"
            )
        return JSONResponse(
            {
                "uid": issued.uid,
                "handle": issued.handle,
                "label": issued.label,
                "token": issued.token,
            },
            status_code=201,
        )

    return _guard(run)


@router.delete("/projects/{project_uid}/tokens/{token_uid}")
def revoke_token(project_uid: UidPath, token_uid: str) -> Response:
    from cod_doc.services import access_service

    def run() -> Response:
        _check_uid(project_uid)
        me = server.require_human(project_uid, ROLE_ADMIN)
        with access_service.store_session() as session:
            mine = {r.uid for r in access_service.list_tokens(session) if r.handle in me.authors}
            if token_uid not in mine:
                return _error(_HTTP_NOT_FOUND, "SYNC-102", "такого своего токена нет")
            access_service.revoke_token(
                session, token_uid=token_uid, author=f"{me.kind}:{me.handle}"
            )
        return Response(status_code=204)

    return _guard(run)
