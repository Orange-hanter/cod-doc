# cod-doc для команды: MCP по токену за Caddy

RFC 29 §3.8, CLO-039. Сервер, к которому подключаются чужие Claude Code /
Cursor / CI: один узел, данные в нём, доступ — Bearer-токен с ролью в проекте.

```text
клиент ──https──▶ Caddy :443 ──▶ cod-doc-mcp :8801 (/mcp)      ┐ Bearer-гейт,
                                └▶ cod-doc     :8765 (/api/v1)  ┘ хранилище доступа
                  веб-UI, legacy /api/* — только 127.0.0.1:8765, наружу 404
```

| Файл | Что |
|---|---|
| `docker-compose.yml` | `cod-doc` (веб, loopback), `cod-doc-mcp` (тот же образ, `cod-doc-mcp --transport streamable-http`), `caddy` |
| `Caddyfile` | TLS от Let's Encrypt; наружу только `/mcp*` и `/api/v1/*` |

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
- **Наружу** — только 443 (и 80 для выпуска сертификата). Веб-UI, legacy
  `/api/*` и `POST /settings` гейта не имеют и через Caddy недоступны.

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

## Свой Caddy уже есть

Если 443 занят другим Caddy на этом хосте — сервис `caddy` из compose не
поднимайте. Подключите `cod-doc-mcp` и `cod-doc` к сети того Caddy
(`networks: {edge: {external: true, name: <сеть>}}`), допишите в его Caddyfile
блок из `Caddyfile` этого каталога и выставьте `COD_DOC_TRUSTED_PROXIES` на
подсеть этой сети.

## Чего здесь нет

- **Синхронизации с ноутбуком.** Пока нет `/api/sync/v1` (CLO-035), данные
  узла и локальной `state.db` расходятся: один из них должен быть источником
  истины, второй — читать через MCP узла.
- **Удалённого администрирования.** `member`/`token` — только на сервере; через
  https-remote — вместе с CLO-035.
