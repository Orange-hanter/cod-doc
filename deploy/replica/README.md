# Реплика БД cod-doc на arch

Гермес — единственный узел с данными всех проектов (`/root/cod-doc/projects/*/.cod-doc/state.db`
и `home/access.db`). Эта схема держит на arch непрерывную копию и поднимает на
ней cod-doc, если узел лёг.

```text
Гермес                                         arch (pull по ssh, ключ rrsync -ro)
cod-doc ─ пишет ─▶ state.db/access.db          ┌─ каждую минуту: rsync replica/ + конфиги, проекции
                     │ WAL                     │
litestream ─ 1 с ──▶ /root/cod-doc/replica ────┘─▶ mirror/
                     (LTX, окно 48 ч)              │ каждый час: restore → quick_check → .zst
                                                   ▼
                                               snapshots/<время>/   48 часовых + 30 суточных
                                                   │ promote
                                                   ▼
                                               standby/ cod-doc + MCP 127.0.0.1:8801 (тот же коммит, те же токены)
```

| Параметр | Значение |
|---|---|
| Потеря данных при смерти узла (RPO) | до 1 мин — интервал pull; Litestream отстаёт от записи на ~1 с |
| Время подъёма на arch (RTO) | ~1–2 мин: restore + `compose up`; образ standby собирается заранее |
| Точечное восстановление | на узле — любой момент за 48 ч (`litestream restore -timestamp`); на arch — ежечасные копии за 2 суток и суточные за 30 дней |
| Логическая порча (кривая миграция, удаление) | реплика её повторит; лечится снапшотом до порчи: `promote --snapshot <время>` или restore на узле |

**Почему Litestream, а не `cod-doc push/pull`.** Синк RFC 29 возит только authored-данные:
журнал, производные таблицы, restricted-документы и `access.db` в changeset не попадают.
Для аварии нужна побайтовая копия файла, а её даёт WAL-репликация.

**Почему pull с arch, а не push на arch.** arch за NAT роутера: push потребовал бы
проброса порта или туннеля. При pull узел ничего не знает об arch, ключ arch на узле
ограничен `rrsync -ro /root/cod-doc` — только чтение, без shell. Litestream пишет в
локальный каталог и не зависит от сети до arch: обрыв связи не останавливает
репликацию, arch догоняет следующим pull.

## Установка

### Гермес (запускает пользователь — SSH к узлу из сессии Claude заблокирован)

После мержа этот каталог доезжает на узел в `/root/cod-doc/src` при выкатке.

```bash
# 1. Место: реплика ≈ размер всех БД × 2 (снапшот + 48 ч хвоста).
df -h / && du -ch /root/cod-doc/projects/*/.cod-doc/state.db /root/cod-doc/home/*.db | tail -1

# 2. Litestream — отдельный compose-проект
mkdir -p /root/cod-doc/litestream /root/cod-doc/replica
cp /root/cod-doc/src/deploy/replica/hermes/{litestream.yml,docker-compose.yml} /root/cod-doc/litestream/
cd /root/cod-doc/litestream && docker compose up -d && sleep 20 && docker compose logs --tail 40
find /root/cod-doc/replica -maxdepth 4 -type d -name '*.db'    # по каталогу на каждую БД

# 3. Ключ arch: только чтение /root/cod-doc через rrsync
command -v rrsync || { zcat /usr/share/doc/rsync/scripts/rrsync.gz > /usr/local/bin/rrsync; chmod +x /usr/local/bin/rrsync; }
echo 'command="'"$(command -v rrsync)"' -ro /root/cod-doc",restrict <ключ из install.sh>' >> /root/.ssh/authorized_keys
```

### arch

```bash
# из клона репозитория на arch (или scp каталога deploy/replica/arch)
bash deploy/replica/arch/install.sh            # litestream 0.5.17 (sha256 пиннован), скрипт, юниты, ключ
# → печатает строку для authorized_keys Гермеса (шаг 3 выше)
bash deploy/replica/arch/install.sh --enable   # первый pull + snapshot, включает таймеры
cod-doc-replica status
```

Образ standby собирается из **своего** клона arch (`~/Git/cod-doc`, `COD_DOC_REPLICA_REPO`)
на коммите `COD_DOC_TAG` узла и только если он в `origin/main`; исходники с узла на arch не
возятся — взломанный узел не получает через сборку исполнение кода на arch.

Проверка без сети и без Litestream: `bash deploy/replica/arch/selftest.sh`.

Таймеры (`systemctl --user list-timers 'cod-doc-replica*'`):

| Таймер | Период | Что делает |
|---|---|---|
| `cod-doc-replica-pull` | 1 мин | rsync `replica/`, `home/`, `projects/` (без живых `*.db`), `tokens/`, `.env` — права 600/700 |
| `cod-doc-replica-snapshot` | 1 ч | `litestream restore` каждой БД + `quick_check`, манифест (alembic, TXID), ротация, пересборка образа standby при смене `COD_DOC_TAG` на узле |
| `cod-doc-replica-check` | 15 мин | pull старше 15 мин или снапшот старше 150 мин → `notify-send` + `~/.config/cod-doc-replica/alert` |

Ежечасный снапшот — это ещё и проверка восстановления: реплика, из которой не
восстанавливается БД, роняет таймер, а не обнаруживается в день аварии.

## Авария: узел лёг

1. Убедиться, что лежит узел, а не сеть ноутбука: `curl -m5 https://cod-doc-78-17-96-218.nip.io/mcp` с arch.
2. На arch: `cod-doc-replica promote` — стоп таймеров, последний pull, restore в
   `standby/data`, `compose up`. Откат к проверенной копии: `cod-doc-replica status` →
   `cod-doc-replica promote --snapshot 20261121T140000Z`.
3. Харнесс: `ssh -fN -L 8801:127.0.0.1:8801 arch-lan` и URL MCP-сервера `cod-doc` →
   `http://127.0.0.1:8801/mcp` (заголовок `Authorization` прежний — `access.db` приехала с
   репликой). MCP standby слушает только loopback arch: TLS нет, а Bearer по голому HTTP в LAN
   читается кем угодно в сети. Открыть в LAN осознанно — `STANDBY_MCP_BIND=192.168.1.120` в
   `~/cod-doc-replica/standby/.env`. Другим участникам (stas, permyakov, simakov) standby
   недоступен: наружу без TLS он не выставляется.

**Пока работает standby, узел не должен принимать записи.** Если Гермес поднимется сам,
`restart: unless-stopped` вернёт его контейнеры, и клиенты со старым URL начнут писать в
устаревшую копию — две расходящиеся истории без механизма слияния. Первое действие на
вернувшемся узле:

```bash
cd /root/cod-doc && docker compose stop cod-doc cod-doc-mcp
cd /root/cod-doc/litestream && docker compose stop
```

## Возврат на Гермес

```bash
# arch: остановить standby, влить WAL, упаковать БД
cod-doc-replica failback                 # → ~/cod-doc-replica/failback-<время>.tar.gz

# ноутбук: перенести архив
scp arch-lan:cod-doc-replica/failback-<время>.tar.gz /tmp/ && scp /tmp/failback-<время>.tar.gz root@78.17.96.218:/root/cod-doc/

# Гермес: всё остановлено (см. выше); старые БД — в сторону, не удалять
cd /root/cod-doc
ts=$(date -u +%Y%m%dT%H%M%SZ); mkdir -p backups/pre-failback-$ts
find home projects -name '*.db*' -type f | while read -r f; do mkdir -p "backups/pre-failback-$ts/$(dirname "$f")"; mv "$f" "backups/pre-failback-$ts/$f"; done
tar xzf failback-<время>.tar.gz          # пути ./home/*.db, ./projects/<slug>/.cod-doc/state.db
mv replica replica.old-$ts && mkdir replica
cd litestream && docker compose down -v  # -v: метаданные Litestream о старой линии
cd .. && docker compose up -d && docker compose ps -a
cd litestream && docker compose up -d

# arch: зеркало и standby в сторону, таймеры обратно
cod-doc-replica reset-after-failback
```

Затем харнесс — обратно на `https://cod-doc-78-17-96-218.nip.io/mcp`. `replica.old-*` и
`backups/pre-failback-*` удалить руками, когда убедишься, что узел цел.

## Обслуживание

- **Восстановить одну БД на узле на момент T** (порча без падения узла):
  `docker compose -f /root/cod-doc/litestream/docker-compose.yml exec litestream litestream restore -timestamp 2026-11-21T10:00:00Z -o /replica/restore.db file:///replica/projects/<slug>/.cod-doc/state.db`
  — затем остановить cod-doc и подменить файл.
- **Новый проект на узле** подхватывается сам (`watch: true`).
- **Обновить Litestream**: тег в `hermes/docker-compose.yml`, `LS_VERSION`/`LS_SHA256` в `arch/install.sh`.
- **Подменили БД на узле** (restore `.backup`, ручной откат): Litestream удаление и
  подмену файла не отслеживает. Начать реплику заново:
  `cd /root/cod-doc/litestream && docker compose down -v && mv ../replica ../replica.old-$(date +%s) && mkdir ../replica && docker compose up -d`
  — том `meta` уходит, каждая БД начинает новую линию со снапшота; arch подхватит её
  следующим pull (`--delete` уберёт старые LTX из зеркала, старые снапшоты на arch остаются).
