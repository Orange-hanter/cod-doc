"""Гейт веб-сессии: HTML, HTMX-фрагменты и websocket за OIDC-логином (миграция 0056).

Чистое ASGI-middleware по образцу :class:`cod_doc.api.auth.BearerGate`; оба
оборачивают приложение в ``api/server.py``. Под гейтом — всё, что не ``/api*``
(его закрывает Bearer), не ``/auth/*`` (маршруты логина) и не ``/static``
(картинки и скрипты без секретов).

- **Фича выключена** (`oidc_service.oidc_configured` — issuer пуст) — гейт
  пропускает всё: локальный ``cod-doc serve`` и ноутбук работают как раньше.
- **Включена, но хранилища доступа нет** — 503 ``SYNC-205``: включённый
  конфиг без ``access.db`` молча открыть веб не должен.
- **Включена** — защищённому пути нужна кукисессия (`oidc_service` HMAC,
  ключ в ``oidc_meta`` хранилища). Отказ — 303 на ``/auth/oidc/login`` с
  безопасным ``next``; websocket без сессии — close 1008.

Гейт только аутентифицирует: identity в ``request_identity`` **не** биндится,
семантика авторизации веба прежняя (binding с пустой ролью сломал бы
``check_write`` веб-фрагментов). Хендл для шапки кладётся в
``scope["state"]["web_handle"]`` — шаблоны читают ``request.state.web_handle``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import anyio.to_thread

from cod_doc.services import oidc_service

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session, sessionmaker
    from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = ["SessionGate", "is_web_session_path"]

_HTTP_SEE_OTHER: Final = 303
_HTTP_UNAVAILABLE: Final = 503
#: Код закрытия websocket «policy violation» (RFC 6455 §7.4.1).
_WS_POLICY_VIOLATION: Final = 1008
#: Префиксы вне гейта: JSON API — у BearerGate, логин — сам гейт, статика — открыта.
_OPEN_SEGMENTS: Final = ("/api", "/auth", "/static")
_LOGIN_PATH: Final = "/auth/oidc/login"
#: ``store_url → (factory, ключ)`` после первого успешного резолва (см. SessionGate).
_GATE_CACHE: dict[str | None, tuple[sessionmaker[Session], bytes]] = {}


def is_web_session_path(path: str) -> bool:
    """Пути веба под гейтом: всё вне ``/api*``, ``/auth/*`` и ``/static``.

    Проверка по границе сегмента: ``/apiary`` и ``/staticfoo`` — обычные
    веб-страницы и под гейтом, иначе будущий маршрут с таким началом молча
    оказался бы без сессии.
    """
    for segment in _OPEN_SEGMENTS:
        if path == segment or path.startswith(segment + "/"):
            return False
    return True


class SessionGate:
    """ASGI-middleware веб-сессии; ``protected(path)`` решает, какие пути закрыты.

    Хранилище и ключ подписи резолвятся один раз и живут в модульном
    ``_GATE_CACHE`` по ``store_url`` (ревью #282, раунд 4): до включения
    фичи (issuer пуст или хранилища нет) проверка остаётся на каждый запрос,
    чтобы ``access init`` включал гейт без рестарта, а горячий путь не платил
    за диски. Гейт у приложения один, но кэш — модульный: экземпляр
    middleware живёт на модульном ``app``, и тесты сбрасывают кэш фикстурой.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        protected: Callable[[str], bool],
        store_url: str | None = None,
    ) -> None:
        self.app = app
        self.protected = protected
        self.store_url = store_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.protected(scope["path"]):
            await self.app(scope, receive, send)
            return
        resolved = _GATE_CACHE.get(self.store_url)
        if resolved is None:
            resolved = await self._resolve(scope, receive, send)
            if resolved is None:
                return  # либо пропущено (фича выключена), либо отказ уже отправлен
            _GATE_CACHE[self.store_url] = resolved
        factory, key = resolved
        payload = oidc_service.read_session(key, _cookie(scope, oidc_service.SESSION_COOKIE))
        ws = scope["type"] == "websocket"
        if payload is None:
            if ws:
                await _send_close(send)
                return
            await _send_login(send, scope)
            return
        # F3 аудита (ревью #282, раунд 4): stateless-кука не отзывается сама —
        # гейт сверяет актёра, disable/unlink закрывают вход сразу, не дожидаясь TTL.
        # Сбой проверки (хранилище мигнуло) — fail-closed на логин, не 500:
        # незнакомое состояние доступа не должно открывать веб (ревью #282, раунд 5).
        actor_uid = str(payload.get("actor_uid", ""))
        try:
            alive = await anyio.to_thread.run_sync(
                oidc_service.session_actor_alive, factory, actor_uid
            )
        except Exception:
            alive = False
        if not alive:
            if ws:
                await _send_close(send)
                return
            await _send_login(send, scope)
            return
        # Хендл для шапки шаблонов; identity не биндим — авторизация веба не менялась.
        scope.setdefault("state", {})["web_handle"] = str(payload.get("handle", ""))
        await self.app(scope, receive, send)

    async def _resolve(
        self, scope: Scope, receive: Receive, send: Send
    ) -> tuple[sessionmaker[Session], bytes] | None:
        """Конфиг → хранилище → ключ; ``None`` — пропуск или отказ уже отправлены."""
        from cod_doc.config import Config
        from cod_doc.services import access_service

        # Config.load() читает диск (mtime-кэш) — вне event loop, как и
        # open_store/session_key ниже.
        cfg = await anyio.to_thread.run_sync(Config.load)
        if not oidc_service.oidc_configured(cfg):
            await self.app(scope, receive, send)
            return None
        factory = await anyio.to_thread.run_sync(access_service.open_store, self.store_url)
        if factory is None:
            if scope["type"] == "websocket":
                await _send_close(send)
            else:
                await _send_unavailable(send)
            return None
        key = await anyio.to_thread.run_sync(oidc_service.session_key, factory)
        return (factory, key)


def _cookie(scope: Scope, name: str) -> str | None:
    for header, value in scope.get("headers", []):
        if header == b"cookie":
            raw = bytes(value).decode("latin-1")
            for part in raw.split(";"):
                part_name, _, part_value = part.strip().partition("=")
                if part_name == name:
                    return part_value
    return None


async def _send_login(send: Send, scope: Scope) -> None:
    """303 на страницу логина; ``next`` — путь+query запроса (свой ASGI scope).

    ``next`` кладётся как значение параметра через urlencode: сырой ``&``
    внутри query обрезал бы адрес возврата (ревью #282, раунд 2), а
    percent-кодировка оставляет заголовок latin-1-безопасным.
    """
    nxt = scope["path"]
    query = scope.get("query_string")
    if query:
        nxt += "?" + bytes(query).decode("utf-8", "replace")
    body = b""
    headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
        (b"location", (_LOGIN_PATH + "?" + urlencode({"next": nxt})).encode("latin-1")),
    ]
    await send({"type": "http.response.start", "status": _HTTP_SEE_OTHER, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _send_unavailable(send: Send) -> None:
    body = json.dumps(
        {
            "error": {
                "code": "SYNC-205",
                "message": "OIDC включён, но хранилища доступа нет: cod-doc access init",
                "details": {},
            }
        },
        ensure_ascii=False,
    ).encode()
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    await send({"type": "http.response.start", "status": _HTTP_UNAVAILABLE, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _send_close(send: Send) -> None:
    await send({"type": "websocket.close", "code": _WS_POLICY_VIOLATION})
