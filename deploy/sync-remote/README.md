# cod-doc для команды: MCP по токену за Caddy

RFC 29 §3.8, CLO-039. Сервер, к которому подключаются чужие Claude Code /
Cursor / CI: один узел, данные в нём, доступ — Bearer-токен с ролью в проекте.

```text
клиент ──https──▶ Caddy :443 ──▶ cod-doc-mcp :8801 (/mcp)      ┐ Bearer-гейт,
                                └▶ cod-doc     :8765 (/api/v1)  ┘ хранилище доступа
                  веб-UI — за OIDC-логином (Pocket ID, пасскей); legacy /api/* — наружу 404
```

| Файл | Что |
|---|---|
| `docker-compose.yml` | `cod-doc` (веб + API), `cod-doc-mcp` (тот же образ, `cod-doc-mcp --transport streamable-http`), `caddy` |
| `Caddyfile` | TLS от Let's Encrypt; `/mcp*`, `/api/v1*`, `/api/sync/v1*` и веб-UI наружу; legacy `/api/*` — 404 |

## Что защищает

- **Гейт** (`cod_doc/api/auth.py`) — 401 без живого токена, в том числе с
  loopback. Пока нет хранилища доступа, гейт выключен — поэтому MCP с
  `--host 0.0.0.0` **без хранилища не стартует**.
- **Роли** — вход только в проекты, где владелец токена участник; `reader` —
  только тулы чтения; запись — `writer`/`admin` и только своим `author`.
  Детали — `docs/mcp-integration.md` §«Удалённый сервер».
- **Fail-closed** — `COD_DOC_REQUIRE_AUTH=1` в compose: если `home/access.db`
  пропал, гейт отвечает 503, а не пропускает всех.
- **Частота** — 10 неудачных попыток с адреса и 600 запросов на токен в минуту,
  затем 429. Отказы — `auth.denied` в журнале хранилища:
  `sqlite3 home/access.db "select ts, payload from activity_event where kind='auth.denied' order by ts desc limit 20"`.
- **Наружу** — только 443 (и 80 для выпуска сертификата). Legacy `/api/*`
  (включая `POST /settings`) гейта не имеют и через Caddy отдают 404. Веб-UI
  — за OIDC-логином (см. ниже) и только при явном `COD_DOC_WEB_PUBLIC=1`:
  без переменной Caddy отвечает на веб 404, потому что SessionGate cod-doc
  включён лишь тогда, когда заданы `COD_DOC_OIDC_*` (ревью #282, раунд 2).

## Первый запуск

```bash
# 1. Код и каталог
git archive origin/main | tar x -C src          # или clone; COD_DOC_SRC=./src
mkdir -p home projects
cat > .env <<'EOF'
COD_DOC_PUBLIC_HOST=cod-doc.example.dev         # A-запись на этот хост (или <имя>-<ip>.nip.io)
COD_DOC_SRC=./src
EOF

# 2. Веб и миграции; проекты — в home/config.yaml и projects/<slug>
docker compose up -d --build cod-doc

# 3. Включить гейт и выдать первый доступ — ДО запуска MCP
docker compose exec cod-doc cod-doc access init
docker compose exec cod-doc cod-doc member add alice -p <slug> --role admin
docker compose exec cod-doc cod-doc token create alice --label macbook   # токен — один раз

# 4. MCP и Caddy
docker compose up -d cod-doc-mcp caddy

# 5. Файрвол: наружу только 80/443 (ssh — по вкусу)
ufw allow 80/tcp && ufw allow 443/tcp
```

Проверка с другой машины:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://cod-doc.example.dev/mcp   # 401
claude mcp add --transport http cod-doc https://cod-doc.example.dev/mcp -s user \
  --header "Authorization: Bearer cdt_…"
claude mcp list                                   # cod-doc ✓ connected
```

## Каждый новый участник

```bash
docker compose exec cod-doc cod-doc member add bob -p <slug> --role writer
docker compose exec cod-doc cod-doc actor agent bob claude-code      # если пишет агент
docker compose exec cod-doc cod-doc token create bob --label laptop
```

Токен передаётся участнику по защищённому каналу; на сервере остаётся только
`sha256`. Потерянный токен — `token revoke <uid>` и новый `token create`.

## Обновление

```bash
git archive origin/main | tar x -C src   # свежий код
docker compose up -d --build             # entrypoint катит миграции проектов и хранилища
```

`cod-doc access migrate` в entrypoint догоняет схему хранилища и ничего не
создаёт: гейт включает только явный `access init`.

## Рутины

Рутины (`approval_stale_default`, `adr_health_daily`, …) на узле тикает systemd,
а не демон ноутбука: `routines/` — скрипт такта и его юниты.

```bash
install -m 0755 routines/cod-doc-routines-tick /usr/local/bin/
install -m 0644 routines/cod-doc-routines.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now cod-doc-routines.timer
journalctl -u cod-doc-routines -p err   # отказы — с текстом причины
```

Скрипт ждёт compose в `/root/cod-doc`; иначе поправьте `cd` в его начале.

## Свой Caddy уже есть

Если 443 занят другим Caddy на этом хосте — сервис `caddy` из compose не
поднимайте. Подключите `cod-doc-mcp` и `cod-doc` к сети того Caddy
(`networks: {edge: {external: true, name: <сеть>}}`), допишите в его Caddyfile
блок из `Caddyfile` этого каталога и выставьте `COD_DOC_TRUSTED_PROXIES` на
подсеть этой сети.

## Веб-UI: вход через Pocket ID (пасскей)

Миграция 0056. Caddy отдаёт наружу веб-UI (`/`, `/p/*`, `/settings`, …); вход —
authorization code flow через внешний OIDC-провайдер [Pocket ID](https://pocket-id.org)
(passkey-only, self-hosted). Машины остаются на Bearer-токенах: провайдер
пасскеев программным клиентам не подходит.

### Один раз: приложение в Pocket ID

1. Поднять Pocket ID (у себя, не в этом compose) и завести пользователя —
   пасскей он добавит сам при первом входе.
2. Administration → Applications → Add: получить **Client ID** и **Client Secret**;
   Callback URL — `https://<COD_DOC_PUBLIC_HOST>/auth/oidc/callback`;
   scopes — `openid profile email`.

### Включить на узле

```bash
cat >> .env <<'EOF'
COD_DOC_OIDC_ISSUER=https://<хост pocket-id>           # без /well-known, просто origin
COD_DOC_OIDC_CLIENT_ID=<client id>
COD_DOC_OIDC_CLIENT_SECRET=<client secret>
COD_DOC_OIDC_REDIRECT_URI=https://cod-doc.example.dev/auth/oidc/callback
COD_DOC_WEB_PUBLIC=1                                   # открыть веб через Caddy; нет — веб 404
EOF

docker compose up -d --build cod-doc caddy
docker compose exec cod-doc cod-doc access init       # если ещё нет
```

Caddy открывает веб только при **обеих** условиях: `COD_DOC_WEB_PUBLIC=1`
и непустой `COD_DOC_OIDC_ISSUER` — без логина наружу не откроется
(ревью #282, раунд 3).

### Кого пускать: явная привязка, регистрации нет

```bash
# sub — «Subject» из Pocket ID: Users → пользователь → поле Subject ID
docker compose exec cod-doc cod-doc oidc link alice <sub>
docker compose exec cod-doc cod-doc oidc list          # кто уже привязан
docker compose exec cod-doc cod-doc oidc unlink alice  # вход через провайдера закрыт
```

Вход без привязки — отказ (`SYNC-201` в журнале хранилища). Отключение актёра
(`actor disable`) тоже закрывает веб-вход сразу. Сессия — подписанная кука на
12 часов; `cod-doc access init` обязателен: без хранилища гейт отвечает 503.

## Чего здесь нет

- **Автосинка.** Узел — умный remote (`/api/sync/v1`): реплика подключается
  `cod-doc remote add origin https://<host> -p <slug>`, `cod-doc remote login
  origin -p <slug>`, дальше `pull`/`push` руками; рутина `sync` — секция F.
  Push на умный remote принимает только операции владельца токена (`SYNC-203`).

Удалённо администрировать можно с машины admin'а проекта:
`cod-doc member add bob -p <slug> --role writer --remote origin`,
`cod-doc token create <вы или ваш агент> --label … -p <slug> --remote origin`.
