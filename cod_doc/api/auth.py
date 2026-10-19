"""Bearer-гейт: ``/api/v1``, ``/api/sync/v1`` и streamable-http MCP (RFC 29 §3.8, CLO-036).

Чистое ASGI-middleware, одно на обе поверхности: веб (``api/server.py``) и
MCP (``mcp/server.py``) оборачивают им своё приложение.

- **Хранилища доступа нет** (``access_service.open_store`` → ``None``) — гейт
  пропускает всё: локальные демоны ``:8801``/``:8802`` и ``cod-doc serve`` на
  ноутбуке работают как раньше. Файл проверяется на каждом запросе, поэтому
  ``cod-doc access init`` включает гейт без рестарта.
- **Хранилища нет, но ``COD_DOC_REQUIRE_AUTH=1``** — 503 на каждый защищённый
  путь: развёрнутый узел без хранилища закрыт, а не открыт.
- **Хранилище есть** — защищённому пути нужен ``Authorization: Bearer cdt_…``
  с любого адреса, включая loopback: за reverse proxy на том же хосте каждый
  запрос пришёл бы с ``127.0.0.1``, и исключение для loopback открыло бы всё.

Отказы — ``{"error": {"code", "message"}}`` (формат §10.9): нет или неверный
токен — 401 ``SYNC-201`` с ``WWW-Authenticate: Bearer``; не участник проекта
``/api/v1/projects/{slug}`` — 403 ``SYNC-202``; частота — 429 ``SYNC-204`` с
``Retry-After``. Каждый отказ пишет ``auth.denied`` в журнал хранилища; блок
по частоте — один раз на окно, иначе перебор раздувал бы журнал со скоростью
перебора.

Роль внутри MCP проверяют сервисы: гейт привязывает владельца токена к
контексту запроса (``services/request_identity``), ``session_factory`` тула
проверяет членство, ``activity_service.emit`` — запись и ``author``. Контекст
доходит до тула, потому что MCP идёт в режиме ``stateless_http``: каждый
запрос обрабатывается внутри своего ASGI-вызова.

**Адрес клиента** для частоты и журнала — ``X-Forwarded-For`` только от
доверенного прокси (loopback или сети из ``COD_DOC_TRUSTED_PROXIES``), иначе
адрес соединения: подделанный заголовок не должен уводить от лимита.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import anyio.to_thread

from cod_doc.services import request_identity

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session, sessionmaker
    from starlette.types import ASGIApp, Receive, Scope, Send

    from cod_doc.services.request_identity import Identity

__all__ = [
    "BearerGate",
    "RateLimiter",
    "is_mcp_path",
    "is_web_protected_path",
]

#: Неудачных попыток аутентификации с адреса за окно — дальше 429.
FAILURES_PER_WINDOW: Final = 10
#: Запросов по одному токену за окно — дальше 429.
REQUESTS_PER_WINDOW: Final = 600
WINDOW_SECONDS: Final = 60.0
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_FORBIDDEN: Final = 403
_HTTP_TOO_MANY: Final = 429
_PROJECT_PATH: Final = re.compile(r"^/api/v1/projects/(?P<slug>[^/]+)(?:/|$)")
_TRUSTED_PROXIES_ENV: Final = "COD_DOC_TRUSTED_PROXIES"
_REQUIRE_AUTH_ENV: Final = "COD_DOC_REQUIRE_AUTH"
_HTTP_UNAVAILABLE: Final = 503


def _auth_required() -> bool:
    """``COD_DOC_REQUIRE_AUTH=1`` — хранилище обязано быть: без него отказ, а не проход.

    Ставится на развёрнутом узле (``deploy/sync-remote``): без флага пропавший
    или не созданный ``access.db`` молча открыл бы ``/api/v1`` всем, кто
    дошёл через прокси.
    """
    return os.environ.get(_REQUIRE_AUTH_ENV, "").strip().lower() in {"1", "true", "yes"}


def is_web_protected_path(path: str) -> bool:
    """Пути веба под гейтом: ``/api/v1`` и ``/api/sync/v1`` (legacy ``/api/*`` — нет)."""
    return any(path == p or path.startswith(p + "/") for p in ("/api/v1", "/api/sync/v1"))


def is_mcp_path(path: str) -> bool:
    """В MCP-приложении под гейтом всё: кроме транспорта там ничего нет."""
    del path
    return True


@dataclass
class RateLimiter:
    """Скользящее окно в памяти процесса: ключ → отметки времени событий."""

    limit: int
    window: float = WINDOW_SECONDS
    _hits: defaultdict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _trim(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        return hits

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Сколько секунд ждать; ``0`` — лимит не исчерпан."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._trim(key, now)
            if len(hits) < self.limit:
                if not hits:
                    del self._hits[key]
                return 0
            return max(1, int(self.window - (now - hits[0])) + 1)

    def hit(self, key: str, now: float | None = None) -> int:
        """Учесть событие; вернуть ``retry_after`` *до* учёта (``0`` — пропустить)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._trim(key, now)
            if len(hits) >= self.limit:
                return max(1, int(self.window - (now - hits[0])) + 1)
            hits.append(now)
            return 0


@dataclass(frozen=True, slots=True)
class _Denial:
    status: int
    code: str
    message: str
    retry_after: int = 0
    token_label: str | None = None
    project: str | None = None


def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = os.environ.get(_TRUSTED_PROXIES_ENV, "")
    nets = [ipaddress.ip_network(part.strip(), strict=False) for part in raw.split(",") if part]
    return tuple(nets)


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    peer = str(client[0]) if client else ""
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    trusted = peer_addr.is_loopback or any(peer_addr in net for net in _trusted_networks())
    if not trusted:
        return peer
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            # Правый адрес дописал ближайший прокси — его и берём.
            forwarded: str = bytes(value).decode("latin-1")
            return forwarded.split(",")[-1].strip() or peer
    return peer


def _bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            raw: str = bytes(value).decode("latin-1")
            scheme, _, token = raw.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


async def _send_denial(send: Send, denial: _Denial) -> None:
    body = json.dumps(
        {"error": {"code": denial.code, "message": denial.message, "details": {}}},
        ensure_ascii=False,
    ).encode()
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    if denial.status == _HTTP_UNAUTHORIZED:
        headers.append((b"www-authenticate", b'Bearer realm="cod-doc"'))
    if denial.retry_after:
        headers.append((b"retry-after", str(denial.retry_after).encode()))
    await send({"type": "http.response.start", "status": denial.status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


_NO_STORE: Final = _Denial(
    _HTTP_UNAVAILABLE,
    "SYNC-201",
    "хранилище доступа не инициализировано (COD_DOC_REQUIRE_AUTH): cod-doc access init",
)


class BearerGate:
    """ASGI-middleware гейта; ``protected(path)`` решает, какие пути закрыты."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        protected: Callable[[str], bool],
        store_url: str | None = None,
        failures: RateLimiter | None = None,
        requests: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self.protected = protected
        self.store_url = store_url
        self.failures = failures or RateLimiter(FAILURES_PER_WINDOW)
        self.requests = requests or RateLimiter(REQUESTS_PER_WINDOW)
        self._logged: dict[str, float] = {}
        self._logged_lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.protected(scope["path"]):
            await self.app(scope, receive, send)
            return
        from cod_doc.services import access_service

        factory = await anyio.to_thread.run_sync(access_service.open_store, self.store_url)
        if factory is None:
            if _auth_required():
                await _send_denial(send, _NO_STORE)
                return
            await self.app(scope, receive, send)
            return
        ip = _client_ip(scope)
        path = scope["path"]
        outcome = await anyio.to_thread.run_sync(self._admit, factory, scope, ip)
        if isinstance(outcome, _Denial):
            await _send_denial(send, outcome)
            return
        with request_identity.bind(outcome):
            denial = await self._enter_project(path, outcome)
            if denial is None:
                await self.app(scope, receive, send)
                return
        # Запись отказа — вне привязки: журнал хранилища пишет сервер, а не
        # владелец токена, и проверка записи (check_write) его бы не пустила.
        await anyio.to_thread.run_sync(self._record, factory, denial, ip, path)
        await _send_denial(send, denial)

    def _admit(self, factory: sessionmaker[Session], scope: Scope, ip: str) -> Identity | _Denial:
        """Аутентификация (в потоке: SQLite синхронный). Отказ уже записан в журнал."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import access_service

        path = scope["path"]
        with transactional(factory) as session:
            blocked = self.failures.retry_after(ip)
            if blocked:
                if self._first_block(ip):
                    access_service.record_denied(session, code="SYNC-204", ip=ip, path=path)
                return _Denial(
                    _HTTP_TOO_MANY, "SYNC-204", "слишком много неудачных попыток", blocked
                )
            token = _bearer(scope)
            identity = access_service.authenticate(session, token) if token else None
            if identity is None:
                self.failures.hit(ip)
                access_service.record_denied(session, code="SYNC-201", ip=ip, path=path)
                message = (
                    "нужен Authorization: Bearer <токен>" if token is None else "токен не принят"
                )
                return _Denial(_HTTP_UNAUTHORIZED, "SYNC-201", message)
            limited = self.requests.hit(identity.actor_uid + ":" + identity.token_label)
            if limited:
                if self._first_block(identity.actor_uid + ":" + identity.token_label):
                    access_service.record_denied(
                        session, code="SYNC-204", ip=ip, path=path, token_label=identity.token_label
                    )
                return _Denial(
                    _HTTP_TOO_MANY,
                    "SYNC-204",
                    "превышена частота запросов",
                    limited,
                    token_label=identity.token_label,
                )
        return identity

    def _first_block(self, key: str) -> bool:
        """Первый отказ по частоте для ключа за окно — его и пишем в журнал."""
        now = time.monotonic()
        with self._logged_lock:
            last = self._logged.get(key)
            if last is not None and now - last < WINDOW_SECONDS:
                return False
            self._logged[key] = now
            return True

    async def _enter_project(self, path: str, identity: Identity) -> _Denial | None:
        match = _PROJECT_PATH.match(path)
        if match is None:
            return None
        slug = match.group("slug")
        uid = await anyio.to_thread.run_sync(_project_uid, slug)
        try:
            request_identity.enter_project(uid, slug)
        except request_identity.AccessDeniedError as exc:
            return _Denial(
                _HTTP_FORBIDDEN,
                "SYNC-202",
                str(exc),
                token_label=identity.token_label,
                project=slug,
            )
        return None

    @staticmethod
    def _record(factory: sessionmaker[Session], denial: _Denial, ip: str, path: str) -> None:
        from cod_doc.infra.db import transactional
        from cod_doc.services import access_service

        with transactional(factory) as session:
            access_service.record_denied(
                session,
                code=denial.code,
                ip=ip,
                path=path,
                token_label=denial.token_label,
                project=denial.project,
            )


def _project_uid(slug: str) -> str | None:
    """``project.uid`` по слагу реестра; нет проекта или uid — ``None`` (→ 403).

    Без кэша: слаг в реестре можно перевесить на другую БД, и закэшированный
    uid пустил бы участника старого проекта в новый под тем же именем.
    """
    from cod_doc.config import Config
    from cod_doc.services import access_service

    entry = Config.load().get_project(slug)
    if entry is None:
        return None
    try:
        return access_service.project_uid_for(entry)
    except access_service.AccessError:
        return None
