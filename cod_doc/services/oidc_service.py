"""OIDC веб-логина: вход в веб через внешний провайдер (Pocket ID, миграция 0056).

Провайдер отвечает за пасскей-церемонию, здесь — только authorization code
flow: discovery → authorize (PKCE S256 + state) → обмен кода → userinfo.
Токены получаем напрямую по TLS от token endpoint, поэтому подпись id_token
локально не проверяем и JWT-библиотека не нужна; личность — ``sub`` из
userinfo.

**Привязка** — ``actor.oidc_sub`` в хранилище доступа (`access_service.oidc_link`):
логин без неё — отказ, регистрации нет. Ключ подписи сессии — 32 случайных
байта в ``oidc_meta`` того же хранилища (файл chmod 600), генерируется при
первом входе.

Фича выключена, пока ``oidc_issuer`` пуст (`oidc_configured`): гейт пропускает
всё, маршруты редиректят на ``/`` — веб как до 0056. Сеть (discovery/обмен/
userinfo) — только эти три функции: их и патчит тест вместо HTTP-моков.

Все параметры входа передаются между запросами короткоживущими куками —
state, PKCE-verifier и ``next`` серверного state-хранилища нет.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

import httpx

from cod_doc.infra.models import OidcMetaModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config
    from cod_doc.services.request_identity import Identity

__all__ = [
    "LOGIN_TTL",
    "NEXT_COOKIE",
    "SESSION_COOKIE",
    "SESSION_TTL",
    "STATE_COOKIE",
    "VERIFIER_COOKIE",
    "OidcError",
    "authorize_url",
    "discover",
    "exchange_code",
    "fetch_userinfo",
    "issue_session_value",
    "journal_denied",
    "login_with_store",
    "make_state",
    "oidc_configured",
    "pkce_pair",
    "read_session",
    "session_key",
]

#: Имя кукисессии; payload + HMAC-SHA256 (стиль подписи webhooks).
SESSION_COOKIE: Final = "cod_doc_session"
STATE_COOKIE: Final = "cod_doc_oidc_state"
VERIFIER_COOKIE: Final = "cod_doc_oidc_verifier"
NEXT_COOKIE: Final = "cod_doc_oidc_next"
#: Сессия — рабочий день; дальше — повторный вход через пасскей.
SESSION_TTL: Final = 12 * 3600
#: State/verifier/next живут одну попытку логина.
LOGIN_TTL: Final = 600
#: OIDC-скоупы: ``sub`` и читаемое имя (preferred_username/email) для шапки.
_SCOPES: Final = "openid profile email"
_SESSION_KEY_NAME: Final = "session_key"
_KEY_BYTES: Final = 32
_HTTP_OK: Final = 200


class OidcError(ValueError):
    """Сбой флоу: провайдер недоступен, код не обменян, конфиг неполон."""


def oidc_configured(cfg: Config) -> bool:
    """Issuer, client_id и secret заданы — гейт и маршруты включаются."""
    return bool(cfg.oidc_issuer and cfg.oidc_client_id and cfg.oidc_client_secret)


def _json_body(response: httpx.Response, *, what: str) -> dict[str, Any]:
    """Тело ответа как JSON-объект; провайдеру и прокси можно отдать HTML/пустоту.

    502 от прокси перед Pocket ID не должен превращаться в необработанный
    JSONDecodeError на пути логина — это ``OidcError`` и страница ошибки.
    """
    try:
        body: Any = response.json()
    except ValueError as exc:  # JSONDecodeError — его подкласс
        msg = f"{what}: HTTP {response.status_code}, тело не JSON"
        raise OidcError(msg) from exc
    if not isinstance(body, dict):
        msg = f"{what}: HTTP {response.status_code}, тело — не JSON-объект"
        raise OidcError(msg)
    return body


def discover(cfg: Config) -> dict[str, Any]:
    """Документ ``/.well-known/openid-configuration`` провайдера."""
    url = cfg.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        response = httpx.get(url, timeout=10)
    except httpx.HTTPError as exc:
        msg = f"провайдер недоступен: {url}: {type(exc).__name__}"
        raise OidcError(msg) from exc
    if response.status_code != _HTTP_OK:
        msg = f"discovery {url}: HTTP {response.status_code}"
        raise OidcError(msg)
    return _json_body(response, what="discovery")


def authorize_url(cfg: Config, discovery: dict[str, Any], *, state: str, challenge: str) -> str:
    """URL провайдера, на который редиректит ``/auth/oidc/login``."""
    endpoint = discovery.get("authorization_endpoint")
    if not endpoint:
        msg = "в discovery нет authorization_endpoint"
        raise OidcError(msg)
    redirect_uri = cfg.oidc_redirect_uri.strip()
    if not redirect_uri:
        msg = "COD_DOC_OIDC_REDIRECT_URI обязателен: полный URL /auth/oidc/callback, известный провайдеру"
        raise OidcError(msg)
    params = {
        "response_type": "code",
        "client_id": cfg.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return str(endpoint) + "?" + urlencode(params)


def exchange_code(cfg: Config, discovery: dict[str, Any], *, code: str, verifier: str) -> str:
    """Обменять код авторизации на токены; вернуть ``access_token``.

    Клиентская пара — HTTP Basic (client_secret_basic); провайдер по
    сертификации обязан её принимать.
    """
    endpoint = discovery.get("token_endpoint")
    if not endpoint:
        msg = "в discovery нет token_endpoint"
        raise OidcError(msg)
    try:
        response = httpx.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": cfg.oidc_redirect_uri.strip(),
                # PKCE S256 (RFC 7636 §4.6): без верификатора провайдер
                # отвергнет обмен — grant был выдан под challenge.
                "code_verifier": verifier,
            },
            auth=(cfg.oidc_client_id, cfg.oidc_client_secret),
            timeout=10,
        )
    except httpx.HTTPError as exc:
        msg = f"token endpoint недоступен: {type(exc).__name__}"
        raise OidcError(msg) from exc
    if response.status_code != _HTTP_OK:
        # Тело отказа — что угодно: HTML прокси, JSON-массив, пустота.
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        detail = (
            body.get("error", response.status_code)
            if isinstance(body, dict)
            else response.status_code
        )
        msg = f"обмен кода не удался: {detail}"
        raise OidcError(msg)
    token: str | None = _json_body(response, what="token endpoint").get("access_token")
    if not token:
        msg = "в ответе token endpoint нет access_token"
        raise OidcError(msg)
    return token


def fetch_userinfo(discovery: dict[str, Any], access_token: str) -> dict[str, Any]:
    """Claims по ``access_token``; ``sub`` обязателен."""
    endpoint = discovery.get("userinfo_endpoint")
    if not endpoint:
        msg = "в discovery нет userinfo_endpoint"
        raise OidcError(msg)
    try:
        response = httpx.get(
            endpoint, headers={"Authorization": "Bearer " + access_token}, timeout=10
        )
    except httpx.HTTPError as exc:
        msg = f"userinfo недоступен: {type(exc).__name__}"
        raise OidcError(msg) from exc
    if response.status_code != _HTTP_OK:
        msg = f"userinfo: HTTP {response.status_code}"
        raise OidcError(msg)
    claims = _json_body(response, what="userinfo")
    if not claims.get("sub"):
        msg = "в userinfo нет sub"
        raise OidcError(msg)
    return claims


# --- PKCE и state -------------------------------------------------------------


def make_state() -> str:
    """Одноразовый анти-CSRF маркер; сверяется кукой на callback."""
    return secrets.token_urlsafe(32)


def pkce_pair() -> tuple[str, str]:
    """PKCE S256: ``(verifier, challenge)`` (RFC 7636 §4.2/§4.3)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# --- кукисессия ---------------------------------------------------------------


def _b64decode(data: str) -> bytes:
    """urlsafe-декод без паддинга: выпущенная кука срезала ``=``."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_session_value(key: bytes, *, actor_uid: str, handle: str) -> str:
    """``payload_b64.sig_b64``: подпись — HMAC-SHA256 ключа по байтам payload."""
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "actor_uid": actor_uid,
                "handle": handle,
                "exp": int(time.time()) + SESSION_TTL,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).rstrip(b"=")
    sig = hmac.new(key, payload, hashlib.sha256).digest()
    return (
        payload.decode("ascii") + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    )


def read_session(key: bytes, value: str | None) -> dict[str, Any] | None:
    """Кука → payload; ``None`` — нет, битая подпись или истёкшая.

    Кука — клиентский вход: не-ASCII в ней не должен бросать
    ``UnicodeEncodeError`` (его подкласс) наружу и давать 500 на каждый
    запрос, а молча считаться «нет сессии».
    """
    if not value or "." not in value:
        return None
    payload_b64, _, sig_b64 = value.rpartition(".")
    try:
        expected = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        sig = _b64decode(sig_b64)
    except ValueError:  # UnicodeEncodeError, binascii.Error — битая кука
        return None
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload: dict[str, Any] = json.loads(_b64decode(payload_b64))
    except ValueError:  # JSONDecodeError и binascii.Error — оба его подклассы
        return None
    if not isinstance(payload.get("exp"), int) or payload["exp"] < time.time():
        return None
    return payload


# --- ключ подписи -------------------------------------------------------------


def session_key(factory: sessionmaker[Session]) -> bytes:
    """Ключ подписи кукисессии из ``oidc_meta``; генерируется при первом входе.

    Хранилище обязано быть: гейт при ``factory is None`` закрывает узел сам
    (SYNC-205), а не зовёт сюда.
    """
    with factory() as session:
        row = session.get(OidcMetaModel, _SESSION_KEY_NAME)
        if row is not None:
            return bytes.fromhex(row.value)
        key = secrets.token_bytes(_KEY_BYTES)
        session.add(OidcMetaModel(key=_SESSION_KEY_NAME, value=key.hex()))
        session.commit()
        return key


# --- транзакционные обёртки для маршрута ---------------------------------------
# Веб-слой не импортирует ``cod_doc.infra`` (WEB-040): здесь — открытие
# транзакции и журнал, маршруту остаётся только open_store.


def login_with_store(factory: sessionmaker[Session], *, sub: str, ip: str) -> Identity | None:
    """``authenticate_by_sub`` + ``access.login`` одной транзакцией; ``None`` — не пускать."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import access_service

    with transactional(factory) as session:
        identity = access_service.authenticate_by_sub(session, sub)
        if identity is None:
            return None
        access_service.record_login(session, handle=identity.handle, ip=ip)
    return identity


def journal_denied(factory: sessionmaker[Session], *, ip: str, path: str) -> None:
    """``auth.denied`` SYNC-201 о неудачном логине — отдельной транзакцией."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import access_service

    with transactional(factory) as session:
        access_service.record_denied(session, code="SYNC-201", ip=ip, path=path)
