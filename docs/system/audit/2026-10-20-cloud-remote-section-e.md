---
type: audit-report
scope: cloud-remote-section-e
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-10-20
last_updated: 2026-10-20
related_docs:
  - ../../../proposals/29-cloud-remote-sync.md
  - ../capabilities/remote-sync.md
  - ../scenarios/remote-sync.md
  - ../ARCHITECTURE.md
  - ../../mcp-integration.md
  - ../../../deploy/sync-remote/README.md
  - 2026-10-19-cloud-remote-section-d.md
audience: [contributors, agents]
---

# Audit — Plan `cloud-remote-2026-10`, Section E closure («Умный remote и доступ»)

> **Контекст.** [RFC 29](../../../proposals/29-cloud-remote-sync.md) §3.8, §10.9,
> §11.2, секция E: доступ к серверу (actor, токены, роли), Bearer-гейт на REST и
> MCP, умный `https://` remote `/api/sync/v1`, проверка `actor`/`author` на push,
> CLI доступа, развёртывание за Caddy. Плюс находка D-F15 аудита
> [секции D](2026-10-19-cloud-remote-section-d.md) — номер миграции (CLO-049).

## 1. TL;DR

Секция закрыта двумя PR. Развёрнутый узел (Гермес) отдаёт MCP и `/api/sync/v1`
по Bearer-токену с ролью в проекте; проверено живыми запросами по HTTPS. Ни
один из семи пунктов не закрыт «на бумаге»: у каждого — тест, который падает без
фикса, и проверка на живом узле там, где она возможна.

## 2. Задачи и PR

| Задача | Что | PR |
|---|---|---|
| CLO-049 | миграция доступа — `0053_sync_access` (0052 занята CLO-045), эпоху не открывает | #246 |
| CLO-034 | `actor`/`api_token`/`project_member`; хранилище доступа сервера | #246 |
| CLO-036 | Bearer-гейт на `/api/v1` и streamable-http MCP, rate limit, `auth.denied` | #246 |
| CLO-039 | `deploy/sync-remote/` (compose + Caddy), развёртывание на Гермесе | #246 |
| CLO-035 | `/api/sync/v1` поверх `FileRemote` сервера, `HttpRemote` | #247 |
| CLO-037 | роли на маршрутах, `actor`/`author` changeset'а на push, SCN-084 | #247 |
| CLO-038 | `remote login`, `member`/`token --remote` | #247 |

## 3. Что построено

- **Хранилище доступа** — отдельная БД сервера (`$COD_DOC_HOME/access.db`) на
  общей схеме миграций; нет файла — гейт выключен, локальные демоны как раньше.
  Журнал — через строку проекта `_access`; ревизий нет (откат вернул бы отозванный
  токен).
- **Гейт** `api/auth.py`: 401 `SYNC-201`, 403 `SYNC-202`, 429 `SYNC-204`
  (10 неудач с адреса и 600 запросов на токен в минуту), 503 `SYNC-205` при
  `COD_DOC_REQUIRE_AUTH=1` без хранилища; loopback от токена не освобождён; XFF —
  только от доверенного прокси.
- **Внутри MCP** (`services/request_identity`): вход только участнику проекта;
  reader — по списку тулов чтения (deny по умолчанию); каждая запись
  (`activity_service.emit`) — writer+ и свой `author` с совпадающим видом.
- **Умный remote**: маршруты переводят HTTP в `RemoteStore` сервера; push
  проверяется разбором changeset'а; снапшот — только на голову-changeset с
  совпадающим заголовком; `meta.json` writer двигает только вперёд;
  администрирование — только токен человека; потолок тела по маршруту на уровне
  ASGI.

## 4. DoD RFC 29 §11.1

| Пункт | Проверка |
|---|---|
| Контракт транспорта | `HttpRemote` проходит общий контракт (`test_remote_contract.py`, включая межпроцессную гонку CAS) и clone SCN-082 против настоящего uvicorn-сервера за гейтом |
| SCN-084 | `tests/api/sync/test_sync_roles.py`: reader на push — 403, ref не создан; чужой `author` — 422, объект не сохранён |
| Четыре поверхности | доступ — CLI (`access`/`member`/`token`/`actor`) и REST; в MCP намеренно нет (§3.9) |
| Живой узел | Гермес: без токена 401, чужой Host 421, веб-UI и legacy `/api/*` снаружи 404, `/api/sync/v1` по токену — 404 пустого remote, чужой проект 403, мусорный объект отвергнут |

## 5. Решения, отступающие от RFC

Записаны в RFC 29 §3.8 «Как сделано» (2026-10-19 и 2026-10-20). Главные:
хранилище доступа — своя БД, а не БД проекта; маршруты сверх §10.9
(`json?prefix=`, `snapshots/{epoch}/{sha}`, `members`, `tokens`); токен через
remote — только себе или своему агенту; агентский токен не администрирует.

## 6. Находки

Автоматическое security- и AI-ревью гонялись на каждом коммите обоих PR; всё
найденное исправлено в тех же PR с тестом:

| Находка | Где | Исправление |
|---|---|---|
| reader мог вызвать тул с побочным эффектом до `emit` (`doc_export`) | MCP | список тулов чтения, deny по умолчанию |
| `author="system:alice"` проходил как свой | MCP, push | вид в префиксе сверяется; без префикса — только человек |
| узел без `access.db` открывал `/api/v1` | гейт | `COD_DOC_REQUIRE_AUTH` → 503 |
| кэш slug→uid пускал в перевешенный слаг | гейт | кэш убран |
| `RateLimiter` рос без предела; пустой лимитер ложен из-за `__len__` | гейт | вымет ключей; `is None` вместо `or` |
| снапшот без проверки, удаление `meta.json` writer'ом, `schema_min` назад | sync | голова-changeset + заголовок; удаление — admin; только вперёд |
| JSON-тела без потолка до проверки роли; стор в event loop | sync | `SyncBodyCap`; `run_in_threadpool` |
| тесты на macOS могли ходить в Keychain | conftest | `COD_DOC_KEYCHAIN=0` после изоляции `COD_DOC_*` |

## 7. Что переходит дальше

- **Рутина `sync`** (секция F) — автосинк по расписанию.
- **Push локальной истории с legacy-авторами** (`mcp`, `cli`, имена моделей) на
  умный remote отвергается `SYNC-203` по построению; для истории одного владельца
  — `file://`/`s3://`.
- **Узел Гермеса — источник истины** для `cod-doc` и `orakul`; локальная
  `state.db` ноутбука после 2026-10-19 расходится, пока он не ходит через MCP
  узла или не синхронизируется через `/api/sync/v1`.
- Блок сайта живёт в Caddyfile чужого репо песочницы; его держит таймер
  `cod-doc-edge-ensure` на узле.

## 8. Итог секции

Секция E закрыта: 7 задач, 2 PR, развёрнуто и проверено на живом узле.
