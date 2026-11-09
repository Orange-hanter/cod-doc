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
  **Закрыто 2026-11-09** (`cod_doc/api/web_access.py`): гейт читает роли
  на каждый запрос и решает по пути и методу — проект только участнику,
  запись по роли, страницы узла только `admin` всех проектов. Без bind:
  путь выбран тот же, что у Bearer-гейта для `/api/v1/projects/{slug}`.
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

## 4.2. Раунд 2 ИИ-ревью на `6df0507` (2026-11-03, synchronize)

Verdict «🔴 нужны правки»: 🟠1 (блокер по правилу) 🟡6 🔵2 — −8 закрытых из
раунда 1 (все четыре фикса подтвердились), +6 новых. Триаж:

- **Починено (4):**
  - 🟠 `Caddyfile` — веб наружу без OIDC по умолчанию. Гейт cod-doc
    включён лишь при `COD_DOC_OIDC_*`, а прокси открывал веб безусловно.
    Фикс: дефолт закрытый — весь веб отдаёт 404, пока в Caddy не придёт
    `COD_DOC_WEB_PUBLIC=1` (expression-матчер `{env.…}`; passthrough в
    `docker-compose.yml`, шаг в README «Включить на узле»).
  - `next` с `&` в query — значение теперь urlencode-параметр
    (`urlencode({"next": …})`), иначе `&` обрезал адрес возврата.
  - `compare_digest` на str с латин-1 не-ASCII → TypeError/500; сравнение
    переведено на байты.
  - JSON-массив в теле отказа token endpoint → `AttributeError`;
    разбор честно проверяет dict.
- **Возражение (без кода)**: «после IntegrityError сессия в failed-состоянии»
  — не так: `AccessError` выходит через `transactional`, который на
  исключении делает rollback; failed-сессия наружу не живёт. Верификатор сам
  понизил до minor.
- **Остальное** (🔵2): повтор раунда 1 — allowlist (осознанно) и
  per-request `open_store` (кэш engines, как у BearerGate).

## 4.3. Раунд 3 ИИ-ревью на `77d4654` (2026-11-04, synchronize)

Verdict «🔴 нужны правки»: 🟠2 🟡11 🔵3 (−6 закрытых раунда 2, +14 новых —
наfix-фиксы раунда 2 добавили новый код в дифф). Триаж:

- **Починено (3):**
  - 🟠 **PKCE был сломан по-настоящему**: `exchange_code` принимал
    `verifier`, но не передавал `code_verifier` в token endpoint — живой
    провайдер отверг бы обмен. Тесты не видели: обмен целиком мокался.
    Фикс + тест, проверяющий тело POST.
  - 🟠 `Caddyfile`: `COD_DOC_WEB_PUBLIC=1` открывал веб даже без
    настроенного OIDC. Теперь expression требует **обоих**: флаг и
    непустой `COD_DOC_OIDC_ISSUER` (env проброшен в caddy-сервис compose).
  - 🟡 `oidc list` читал `ActorModel` напрямую в CLI — правило §5.10;
    чтение переведено на `access_service.list_oidc_links`.
- **Возражения (без кода):**
  - «failed-сессия после IntegrityError» / «грязный oidc_sub остаётся в
    сессии» — один и тот же ответ: `AccessError` выходит через
    `transactional`, который на исключении делает rollback; наружу
    сессия с грязным состоянием не живёт.
  - «Logout по GET без защиты» — принудительный logout чужой картинкой
    портит удобство, не безопасность: сессия просто снимается; Single
    Logout у провайдера не нужен, повторный вход бесплатный.
  - «Discovery без кэша на каждом шаге логина» — путь синхронный в
    threadpool, частота — один логин на 12ч на человека; кэш — по
    потребности при многопользовательском узле.
  - «ARCHITECTURE: любой привязанный видит все страницы» — это F1,
    документированное осознанное ограничение v1 (ARCHITECTURE §9 это
    прямо пишет), не дефект этого PR.
  - «actor disable не закрывает сессию сразу» — F3, ≤12ч TTL, в фикс F1.
  - «open_store/discovery per-request» — известный рисунок BearerGate
    (engines кэшируются), повтор раунда 1.

## 4.4. Раунд 4 ИИ-ревью на `18bb534` (2026-11-04): зачистка остатка

Вердикт «💬 есть замечания» — блокеров нет (🔴0 🟠0 🟡12 🔵4, −11 из раунда 3).
Из остатка починено ещё шесть (коммит раунда 5):

- **Кэш гейта** — `(factory, ключ)` резолвится один раз в модульный
  `_GATE_CACHE` по `store_url`; до включения фичи проверка остаётся на каждый
  запрос (`access init` включает гейт без рестарта), горячий путь за диски
  не платит. F3 закрыт содержательно: **`session_actor_alive`** — гейт
  сверяет актёра на каждый запрос, `actor disable`/`oidc unlink` закрывают
  веб-вход сразу, не дожидаясь TTL 12ч.
- **TTL-кэш discovery** (10 мин по issuer): login+callback одного входа —
  один снимок документа, сеть на второй раз не ходит.
- **Гонка ключа сессии**: `IntegrityError` на INSERT → rollback → перечитывание
  ключа победителя (PK решает; тест честный — stale-чтение + реальный конфликт).
- **`claims.get("sub")`** в маршруте — ремень поверх подтяжек `fetch_userinfo`
  (missing/не-строка → штатный отказ, не KeyError).
- Тесты: подпись над мусорным payload (`garbage`/`null`/`[3`) — None;
  кэш discovery (второй вызов из кэша, другой issuer — свой снимок);
  disable актёра убивает живую сессию в гейте.

Не чинится кодом — контракт v1: гейт не биндит `request_identity`
(«любой привязанный видит все проекты») — см. F1; bind с пустой ролью сломал
бы `check_write` веб-фрагментов, отдельная задача про роли per-page.
Loopback-гварда за прокси — legacy `/api/*` наружу отдаёт 404 на уровне
Caddy, поверхность недостижима.

## 4.5. Раунд 5 ИИ-ревью на `b0c98b1` (2026-11-04): fail-closed до конца

Вердикт «🔴 нужны правки»: 🟠1 🟡12 🔵4 — −10 раунда 4 (кэш гейта, отзыв
сессии, гонка ключа, discovery-кэш подтверждены закрытыми), +16 новых.
Триаж:

- **Починено (4):**
  - 🟠 Caddy проверял только issuer — при `WEB_PUBLIC=1` + issuer без
    `client_id/secret` прокси открыт, а гейт cod-doc выключен (fail-open).
    Теперь expression требует **всех трёх** OIDC-переменных + флага; compose
    пробрасывает тройку в caddy-сервис.
  - Отмена входа у провайдера (`error=access_denied`) не считается
    перебором: страница ошибки без лимитера и `auth.denied`.
  - `access_token` не-строка (число/объект от кривого провайдера) —
    `OidcError`, не строка-маска в заголовке.
  - Сбой ревизии актёра в гейте (хранилище мигнуло) — fail-closed на
    логин, не 500.
- **Возражения (без кода):** кэш гейта не сбрасывается при ротации ключа —
  ротация ручная, рестарт приемлем (задокументировано); `record_login` в
  одной транзакции с аутентификацией — конвенция ADO-040 (событие и мутация
  атомарны), «декопуляция» ослабила бы журнал; старая сессия переживает
  перепривязку sub — сессия принадлежит актёру, не sub, актёр тот же;
  Config.load на каждый запрос до включения фичи — цена «access init без
  рестарта», mtime-кэш уже внутри; logout по GET — повтор раунда 4.

## 4.6. Раунд 6 ИИ-ревью на `867a41b` (2026-11-04): сходимость

Вердикт «💬 есть замечания» — **блокеров нет** (🔴0 🟠0 🟡11 🔵6; −14 раунда 5,
оба фикса — Caddy-тройка и лимитер — подтверждены закрытыми). Остаток —
переформулировки позиций, уже зафиксированных выше (кэш при ротации ключа —
§4.5; `record_login` в одной транзакции — конвенция ADO-040; сессия
переживает перепривязку sub — сессия актёра; logout GET; allowlist
`cod_doc.api.auth`; `Config.load` до включения фичи), плюс две записи
деплой-уровня: `Secure`-кука за прокси зависит от схемы на uvicorn (см.
§4.1 — `forwarded-allow-ips`) и catch-all Caddy проксирует непокрытые пути
на cod-doc при включённом вебе — это и есть дизайн (весь веб за SessionGate;
legacy `/api/*` перед ним отдаёт 404). Однострочный 🔵-нит (слабый ассерт
статики) усилен до `== 200`.

Дальнейшие раунды гоняют хвост переформулировок (стабильность 26–53%) —
фиксы исчерпаны, PR передан на человеческое ревью.

## 5. Next step

## 5. Next step

Ручной E2E на hermes: поднять Pocket ID, зарегистрировать приложение
(`deploy/sync-remote/README.md` §«Веб-UI»), `docker compose up -d --build`,
`cod-doc oidc link`, проверка входа снаружи и 401 на `/api/v1` без Bearer.
После — закрыть F1 (привязка ролей веба) отдельной задачей, если веб станет
многопользовательским.
