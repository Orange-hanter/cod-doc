---
type: audit-report
scope: oidc-web-login
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-11-03
last_updated: 2026-11-03
related_docs:
  - ../capabilities/remote-sync.md
  - ../ARCHITECTURE.md
  - ../../../deploy/sync-remote/README.md
audience: [contributors, agents]
---

# Audit — OIDC-логин веба через Pocket ID (миграция 0056)

> **Контекст.** Веб-UI не имел никакой авторизации: Bearer-гейт (CLO-036)
> закрывает только `/api/v1`, `/api/sync/v1` и MCP, а Caddy на умном remote
> держал веб loopback-only. Решение: вход в веб через внешний OIDC-провайдер
> [Pocket ID](https://pocket-id.org) (passkey-only, OIDC-certified) — пасскей
> для людей, Bearer-токены для машин остаются без изменений.

## 1. TL;DR

Веб получил первый гейт: `SessionGate` (`cod_doc/api/web_session.py`) требует
кукисессию на HTML, HTMX-фрагментах и websocket, выпускаемую после
authorization code flow (PKCE S256 + state) через Pocket ID. Личность — `sub`
из userinfo, привязка `actor.oidc_sub` — явно командой `cod-doc oidc link`:
регистрации нет (non-goal §6 remote-sync.md сохранён). Фича выключена без
`COD_DOC_OIDC_ISSUER` — локальный веб байт-в-байт как раньше.

## 2. Deliverables

| Что | Где |
|---|---|
| Миграция `actor.oidc_sub` (UNIQUE, nullable) + `oidc_meta` (kv), LOCAL в реестре, эпоху не открывает | `20261103_0056_oidc_web_login.py` |
| `oidc_link` / `oidc_unlink` / `authenticate_by_sub` / `record_login` (`access.login`, `access.oidc_linked/unlinked`) | `cod_doc/services/access_service.py` |
| Флоу: discovery, authorize (PKCE), exchange, userinfo; кукисессия HMAC-SHA256; ключ в `oidc_meta` | `cod_doc/services/oidc_service.py` |
| Маршруты `/auth/oidc/login`, `/auth/oidc/callback`, `/auth/logout`; отказы → страница ошибки + `auth.denied` SYNC-201 + лимитер 429 | `cod_doc/api/web/pages/auth.py` |
| Гейт веба; ws — close 1008; включён без хранилища — 503 SYNC-205 | `cod_doc/api/web_session.py` |
| CLI `cod-doc oidc link/unlink/list` | `cod_doc/cli/cmd_access.py` |
| Конфиг `COD_DOC_OIDC_{ISSUER,CLIENT_ID,CLIENT_SECRET,REDIRECT_URI}`; secret в `SECRET_FIELDS` | `cod_doc/config.py` |
| Шапка: хендл (из `scope["state"]["web_handle"]`) + «Выйти» | `cod_doc/templates/web/base.html` |
| Caddy: веб наружу, legacy `/api/*` остаётся 404; инструкция Pocket ID | `deploy/sync-remote/` |

## 3. Acceptance

- [x] Без OIDC-конфига поведение прежнее — тест `test_gate_off_web_open_api_bearer_only`.
- [x] Полный флоу login→callback→страница→logout — `test_full_login_flow_page_and_header`.
- [x] Неизвестный sub и forged state — 403 + `auth.denied` SYNC-201 — два теста.
- [x] Перебор логинов — 429 с `Retry-After`, 10 в окно — `test_login_attempts_rate_limited`.
- [x] ws без сессии — close 1008; `/api/v1` остаётся за Bearer (401).
- [x] Включённый OIDC без хранилища — 503 SYNC-205 (fail-closed).
- [x] Миграция up/down/up на чистой БД; `oidc_client_secret` не уходит в `GET /api/config` (`test_read_config_never_leaks_secrets`).
- [ ] Ручной E2E с живым Pocket ID на hermes (см. «Next step»).

## 4. Findings

- **F1.** Гейт только аутентифицирует: identity в `request_identity` не
  биндится — иначе `check_write` с пустой ролью сломал бы запись через
  веб-фрагменты. Следствие: любой привязанный пользователь в вебе действует с
  правами «локальной машины». Для single-admin узла — приемлемо; фикс —
  отдельная задача (bind + per-page `enter_project`).
- **F2.** `exchange_code`/`fetch_userinfo` не проверяют подпись id_token —
  токены получены напрямую по TLS от token endpoint, личность берётся из
  userinfo. Провайдер по OIDC-сертификации гарантирует endpoints; при смене
  провайдера на не-certified — вернуться к JWKS-проверке.
- **F3.** Сессия не отзывается сервером (только logout/expiry 12ч). Отключение
  актёра (`actor disable`) и `oidc unlink` закрывают только *новые* входы.
  Для узла команды живущие ≤12ч сессии отключённого актёра — известный разрыв,
  в фикс F1 (binding проверял бы disabled на каждом запросе).
- **F4.** Одновременные первые входы на пустом `oidc_meta` могут столкнуться на
  UNIQUE ключа (get-then-add) — проигравший получает 500 и повторяет запрос;
  для одного-двух людей не стоит обработки.

## 4.1. Раунд ИИ-ревью #282 (2026-11-03, после пуша)

Verdict «💬 есть замечания»: 🔴0 🟠0 🟡10, мерж не блокируется (MoA: security
glm-5.3-flash, correctness/architecture deepseek-v4.1-flash, агрегатор
claude-haiku-5.5, верификатор mimo-v2.6-pro, 453k токенов). Триаж:

- **Починено в ветке** (4 реальных): не-JSON тело провайдера/прокси →
  `OidcError`/страница ошибки вместо 500 (`_json_body` во всех трёх вызовах);
  не-ASCII кука → «нет сессии», не `UnicodeEncodeError` (подкласс ValueError,
  поднят под try); границы сегментов в `is_web_session_path` (`/apiary`,
  `/staticfoo` — под гейтом); гонка `oidc_link` → `AccessError` вместо сырого
  IntegrityError. Плюс `Config.load()` гейта — в `to_thread`, вне event loop.
- **Ложные** (3): `claims["sub"]` KeyError — `fetch_userinfo` уже гарды sub;
  sync-httpx «блокирует воркер» — маршруты `def` → threadpool FastAPI;
  async-тест без маркера — `asyncio_mode = auto` в pyproject.
- **Совпали с F-находками**: неотзываемая сессия ≤12ч (F3), гонка ключа (F4),
  per-request `open_store`/`session_key` (сознательно, engines кэшируются —
  тот же рисунок, что у BearerGate).
- **Взято в деплой-заметку**: за TLS-терминирующим прокси кука может ставиться
  без `Secure` — uvicorn доверяет `X-Forwarded-Proto` только с
  `forwarded-allow-ips` (по умолчанию loopback); при выносе прокси с хоста —
  настроить `--forwarded-allow-ips`. Allowlist `cod_doc.api.auth` в
  web-слое — осознанно (общий RateLimiter); вынос в нейтральный модуль —
  по потребности.

## 5. Next step

## 5. Next step

Ручной E2E на hermes: поднять Pocket ID, зарегистрировать приложение
(`deploy/sync-remote/README.md` §«Веб-UI»), `docker compose up -d --build`,
`cod-doc oidc link`, проверка входа снаружи и 401 на `/api/v1` без Bearer.
После — закрыть F1 (привязка ролей веба) отдельной задачей, если веб станет
многопользовательским.
