"""Вход в веб через OIDC-провайдера (Pocket ID, миграция 0056) и выход.

``/auth/oidc/login`` уводит на провайдера (пасскей), ``callback`` обменяет
код и ставит кукисессию (`services/oidc_service`), ``/auth/logout`` её
сбрасывает. Ход флоу — между запросами в короткоживущих куках (state,
PKCE-verifier, ``next``), серверного state-хранилища нет.

Все отказы логина — один путь: неудача в лимитер по IP (``RateLimiter`` из
``api/auth.py``), ``auth.denied`` с кодом ``SYNC-201`` в журнал хранилища,
страница ошибки. Успех — ``access.login`` раз на церемонию. Отказы записывает
сервер (`request_identity.suspended` внутри ``record_denied``/``_emit``), не
владелец сессии; журнал и логин — в разных транзакциях, вложенное открытие
SQLite «database is locked» не ловит.

Страница ошибки, а не флеш-редирект: под гейтом рендерится каждая страница,
флеш показался бы только после следующего полного входа.

Фича выключена (issuer пуст): login/logout редиректят на ``/`` — поведение
веба как до 0056.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cod_doc.api.auth import FAILURES_PER_WINDOW, RateLimiter, _client_ip
from cod_doc.api.deps import get_config
from cod_doc.api.web.errors import same_site_path
from cod_doc.api.web.templates_env import templates
from cod_doc.services import access_service, oidc_service

router = APIRouter()

#: Неудачных попыток логина с адреса за окно — дальше 429 (как у BearerGate).
_FAILS = RateLimiter(FAILURES_PER_WINDOW)


def _secure(request: Request) -> bool:
    """``Secure`` у кук, когда страница отдана по https (стиль set_flash_cookie)."""
    return request.url.scheme == "https"


def _set_login_cookie(response: Response, request: Request, name: str, value: str) -> None:
    """Кука хода логина: HttpOnly, Lax, короткий TTL — одна попытка."""
    response.set_cookie(
        name,
        value,
        max_age=oidc_service.LOGIN_TTL,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )


def _error_page(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "auth_error.html", {"message": message}, status_code=403
    )


def _deny_login(request: Request, *, ip: str, path: str, message: str) -> HTMLResponse:
    """Единый отказ: лимитер, ``auth.denied`` в журнал, страница ошибки."""
    _FAILS.hit(ip)
    factory = access_service.open_store()
    if factory is not None:
        oidc_service.journal_denied(factory, ip=ip, path=path)
    return _error_page(request, message)


@router.get("/auth/oidc/login", response_class=HTMLResponse)
def oidc_login(request: Request) -> Response:
    """303 на провайдера; state/verifier/next — в короткоживущих куках."""
    cfg = get_config()
    if not oidc_service.oidc_configured(cfg):
        return RedirectResponse(url="/", status_code=303)
    ip = _client_ip(request.scope)
    if (retry := _FAILS.retry_after(ip)) > 0:
        return _too_many(retry)
    try:
        discovery = oidc_service.discover(cfg)
    except oidc_service.OidcError as exc:
        return _error_page(request, f"провайдер входа недоступен: {exc}")
    state = oidc_service.make_state()
    verifier, challenge = oidc_service.pkce_pair()
    try:
        url = oidc_service.authorize_url(cfg, discovery, state=state, challenge=challenge)
    except oidc_service.OidcError as exc:
        return _error_page(request, str(exc))
    redirect = RedirectResponse(url=url, status_code=303)
    _set_login_cookie(redirect, request, oidc_service.STATE_COOKIE, state)
    _set_login_cookie(redirect, request, oidc_service.VERIFIER_COOKIE, verifier)
    _set_login_cookie(redirect, request, oidc_service.NEXT_COOKIE, _next_of(request))
    return redirect


@router.get("/auth/oidc/callback", response_class=HTMLResponse)
def oidc_callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
    """Проверка state → обмен кода → ``sub`` → привязка → кукисессия."""
    cfg = get_config()
    if not oidc_service.oidc_configured(cfg):
        return RedirectResponse(url="/", status_code=303)
    ip = _client_ip(request.scope)
    path = "/auth/oidc/callback"
    if (retry := _FAILS.retry_after(ip)) > 0:
        return _too_many(retry)
    if error:
        return _deny_login(request, ip=ip, path=path, message=f"провайдер вернул отказ: {error}")
    cookie_state = request.cookies.get(oidc_service.STATE_COOKIE, "")
    verifier = request.cookies.get(oidc_service.VERIFIER_COOKIE, "")
    # Кука — клиентский вход: байты, не str. compare_digest на str с
    # не-ASCII (латин-1 декод заголовка) бросил бы TypeError → 500 (ревью #282,
    # раунд 2); на bytes допустим любой ввод.
    state_ok = (
        bool(code)
        and bool(state)
        and bool(cookie_state)
        and hmac.compare_digest(cookie_state.encode("utf-8"), state.encode("utf-8"))
    )
    if not state_ok or not verifier:
        return _deny_login(
            request, ip=ip, path=path, message="вход не выполнен: недействительный state"
        )
    try:
        discovery = oidc_service.discover(cfg)
        access_token = oidc_service.exchange_code(cfg, discovery, code=code, verifier=verifier)
        claims = oidc_service.fetch_userinfo(discovery, access_token)
    except oidc_service.OidcError as exc:
        return _deny_login(request, ip=ip, path=path, message=f"вход не выполнен: {exc}")
    factory = access_service.open_store()
    if factory is None:
        return _deny_login(
            request, ip=ip, path=path, message="хранилища доступа нет: cod-doc access init"
        )
    # fetch_userinfo уже гарантует sub, но контракт коллаборатора дешевле
    # не доверять: пропуск/не-строка — штатный отказ, не KeyError → 500.
    raw_sub = claims.get("sub")
    if not isinstance(raw_sub, str) or not raw_sub:
        return _deny_login(request, ip=ip, path=path, message="вход не выполнен: нет sub")
    sub = raw_sub
    identity = oidc_service.login_with_store(factory, sub=sub, ip=ip)
    if identity is None:
        return _deny_login(
            request,
            ip=ip,
            path=path,
            message="вход не разрешён: аккаунт не привязан (cod-doc oidc link)",
        )
    key = oidc_service.session_key(factory)
    nxt = same_site_path(request, request.cookies.get(oidc_service.NEXT_COOKIE, "")) or "/"
    redirect = RedirectResponse(url=nxt, status_code=303)
    redirect.set_cookie(
        oidc_service.SESSION_COOKIE,
        oidc_service.issue_session_value(key, actor_uid=identity.actor_uid, handle=identity.handle),
        max_age=oidc_service.SESSION_TTL,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )
    for name in (
        oidc_service.STATE_COOKIE,
        oidc_service.VERIFIER_COOKIE,
        oidc_service.NEXT_COOKIE,
    ):
        redirect.delete_cookie(name, path="/")
    return redirect


@router.get("/auth/logout")
@router.post("/auth/logout")
def logout() -> Response:
    """Сброс кукисессии; Single Logout провайдера не нужен."""
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.delete_cookie(oidc_service.SESSION_COOKIE, path="/")
    return redirect


def _next_of(request: Request) -> str:
    """``?next=`` только в пределах сайта; иначе ``/`` (стиль same_site_path)."""
    return same_site_path(request, request.query_params.get("next", "")) or "/"


def _too_many(retry_after: int) -> Response:
    """429 на перебор: HTML-браузеру хватает заголовка и текста."""
    response = Response(
        f"слишком много попыток входа; подожди {retry_after} c",
        status_code=429,
        media_type="text/plain",
    )
    response.headers["retry-after"] = str(retry_after)
    return response
