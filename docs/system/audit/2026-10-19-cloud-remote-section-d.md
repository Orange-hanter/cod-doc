---
type: audit-report
scope: cloud-remote-section-d
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-10-19
last_updated: 2026-10-19
related_docs:
  - ../../../proposals/29-cloud-remote-sync.md
  - ../capabilities/remote-sync.md
  - ../scenarios/remote-sync.md
  - ../DATA_MODEL.md
  - ../../mcp-integration.md
  - 2026-10-17-cloud-remote-section-c.md
audience: [contributors, agents]
---

# Audit — Plan `cloud-remote-2026-10`, Section D closure («S3, clone, аренды, эпохи»)

> **Контекст.** [RFC 29](../../../proposals/29-cloud-remote-sync.md) §11.2,
> секция D: второй транспорт (`s3://`), `cod-doc clone` со снапшота, аренда
> человеческих номеров и замка checkout через remote, эпохи схемы с
> `export-pending`, гарантия «restricted не покидает реплику». В ту же волну
> закрыты переходящие находки аудита
> [секции C](2026-10-17-cloud-remote-section-c.md): F11 (CLO-044), F4 (CLO-045),
> смежная часть F8 (CLO-046), и две находки внутри волны (CLO-047, CLO-048).
> Умный remote, токены и роли — секция E.

## 1. TL;DR

Секция D закрыта по коду: 6/6 задач (CLO-028…033) и пять задач-продолжений
(CLO-044…048) в `main` десятью PR (#229, #232, #234…#241). CI на всех десяти
коммитах слияния зелёный (`gh run list --branch main`). Одна миграция — `0052_sync_conflict_opened_hlc`
(CLO-045). Новые поверхности: `cod-doc clone`, `cod-doc sync export-pending`,
`task checkout --offline` / `task_checkout(offline=…)`, поля `sync status`
(`leases`, `clone_url`, `restricted_rows`, `epoch_local`/`epoch_remote`).
Новый метод транспорта — `RemoteStore.list_prefix` (CLO-048).

Ревью безопасности нашли в волне девять дефектов, все закрыты в своих PR с
тестом, падавшим до правки (§6.1). Одно 🟠 AI-ревью #234 не исправлено и
переходит дальше (§6.1, §7 D-F1). Property-тест сходимости CLO-027 нашёл три
расхождения, все исправлены в CLO-047 (§6.2).

Что не сделано и переходит дальше: §3.6 п. 3 (отставшая реплика со снапшота
эпохи), остаточный риск алиасов в clone, clone качает всю историю,
`export-pending` без обратного импорта, `~/.cod-doc/credentials` не читается,
границы CLO-046 (§7).

## 2. Задачи и PR

| Задача | Что | PR → коммит в `main` |
|---|---|---|
| CLO-028 | `infra/remote/s3.py`: `S3Remote` на boto3, CAS через `If-None-Match: *` / `If-Match`, снятие аренды — условное надгробие; джоба CI `S3 remote (MinIO)` | #229 → `a59bcda` |
| CLO-030 | `sync_service/leases.py`: блоки номеров через `leases/<PREFIX>`, провизорный `PREFIX-~xxxx` (`SYNC-107`), закрепление на push, `entity_alias reason=pinned`, уступка двойника арендованного номера | #232 → `7aa81c6` |
| CLO-044 | `status_merge.replay`: статус и замок — проигрывание цепочки всех записей поля (F11) | #235 → `2fb5f13` |
| CLO-045 | маркер разрешения `entity_kind = sync_resolution` в changeset'е v1; миграция `0052_sync_conflict_opened_hlc` (F4) | #235 → `2fb5f13` |
| CLO-046 | `sync_withdraw.py`: отзыв уехавшего документа при повышении до restricted, понижение — первый upsert всего документа (F8) | #234 → `00bd69a` |
| CLO-031 | `sync_service/checkout_lease.py`: аренда `leases/checkout/<task_uid>`, `SYNC-108`/`SYNC-109`, `--offline`, разрешение `double_checkout` (F1) | #236 → `74d3ba4` |
| CLO-047 | буквы секций плана, `dependency.note` у тройного ребра, тело при понижении против правки — находки property-теста | #237 → `5c324ca` |
| CLO-048 | осиротевшая аренда checkout: перехват по TTL при checkout, `list_prefix` + `sweep` в `release_stale`, тело аренды — недоверенный ввод | #238 → `9f98a40` |
| CLO-029 | `sync_service/snapshot.py`, `clone.py`: снапшот `CDS1` каждые 5000 операций и на первом push, `cod-doc clone` = снапшот + хвост | #239 → `0d89cad` |
| CLO-033 | `sync_service/gate.py`: пересверка хвоста по текущим меткам при упаковке, растяжка `RestrictedLeakError` на changeset'е и снапшоте, `restricted_rows` | #240 → `9b5a472` |
| CLO-032 | `infra/sync_epochs.py`, `sync_service/epochs.py`: реестр эпох с гейтом по схеме, `SYNC-105`/`SYNC-106`, снапшот эпохи, `export-pending` | #241 → `34b4574` |

## 3. Что построено

| Область | Модуль | Как устроено |
|---|---|---|
| S3-транспорт | `infra/remote/s3.py` | раскладка §3.5 под `s3://бакет/префикс`; ключи только из сегментов, прошедших `check_name`/`check_sha`; создание — `PutObject` + `If-None-Match: *`, замена — `If-Match: <ETag>`; 412/409/404 — проигранная гонка. Ошибки: сеть/5xx — `SYNC-101`, 401/403 и нет кредов — `SYNC-201`, 429/SlowDown — `SYNC-204`, NoSuchBucket — `SYNC-110`. Потолки чтения — как у `FileRemote` |
| аренда номеров | `leases.py` | `{"next", "block", "holders"}` под CAS `get_json`/`cas_json`; блок 20, добор при остатке < 5; без remote генераторы работают как раньше. Провизорный ID реплику не покидает: первый шаг push переписывает строку, `human_id_refs`, `scope_id` событий и хвост на месте |
| статус — цепочка | `status_merge.replay` | записи `task.status`/`checked_out_*` из хвоста не обрезаются; значение — проигрывание по `(hlc, op_id)`; последовательная запись применяется как есть, конкурентная — по `ALLOWED_TRANSITIONS`; отказ — одна подпись конфликта на всех репликах; конфликт закрывается сам (`sync:chain`) |
| маркер разрешения | `markers.py`, `conflicts.py` | операция `upsert` с `entity_kind = sync_resolution`, `fields` = подпись; получатель закрывает конфликты подписи с `opened_hlc` < штампа маркера; `opened_hlc` — старший штамп записей конфликта |
| отзыв restricted | `sync_withdraw.py` | повышение уехавшего документа пишет одну операцию (`upsert` ровно с `sensitivity`); получатель затирает тело (`WITHDRAW_BLANK`), строки остаются; понижение — `reship_ops`; свои неотправленные правки получателя — в local-событие `sync.withdraw_quarantined` |
| аренда checkout | `checkout_lease.py`, `checkout_service.py` | `{"task_id", "agent", "replica", "acquired_at", "ttl_minutes"}`; аренда — до первой записи сессии, снятие — хуком после коммита/отката; занято — `SYNC-108` (подкласс `CheckoutConflictError`, поверхности отдают прежний 409), нет сети — `SYNC-109`; `--offline` — провизорная отметка `sync_ref("@checkout", task_uid)`, `reconcile_quietly` после push/pull |
| осиротевшая аренда | `checkout_lease.py`, `RemoteStore.list_prefix` | истекла = `acquired_at + ttl_minutes + 2 мин` раньше локальных часов; перехват при checkout по etag (на S3 — `If-Match`), из двух перехватчиков проходит один; `release_stale` после коммита проходит `leases/checkout/` и снимает истёкшие |
| снапшот и clone | `snapshot.py`, `clone.py` | `snapshots/<epoch>/<head>.db.zst` = `CDS1` + zstd-поток строк канонического JSON §10.4 (заголовок, `row`, `alias`, `end` с числом строк и sha256); указатель — `meta.json["snapshot"]` под CAS. Clone: `refs/main` → снапшот → загрузка одной транзакцией под `suspended` → журнал и индекс из changeset'ов remote → хвост обычным pull → сверка дайджеста → регистрация (`config.yaml`, `remotes.add origin`, событие `sync.cloned`) |
| restricted не уезжает | `gate.py` | три рубежа: захват (CLO-006); упаковка — `shippable_tail` пересматривает хвост по текущим меткам (одна функция для push и `export-pending`); растяжка — `check_ops`/`check_row` ищет uid restricted-документа и его тела на любой глубине `fields`/`base`, находка — `SYNC-001`, remote не трогается |
| эпохи | `infra/sync_epochs.py`, `epochs.py`, `update_service.py` | `MIGRATIONS` — застывший реестр «открывает ли ревизия эпоху» (`0050` — да, `0051`/`0052` — нет); гейт сверяет решение с фактом по схеме. `update` перед открывающей миграцией при незапушенных операциях — `SYNC-106`; push новой эпохи пишет снапшот и поднимает `epoch`/`schema_min` (`sync.epoch_opened`); push/pull отставшей реплики — `SYNC-105`; `meta.json` — недоверенный ввод |

**Поверхности.**

| Поверхность | Что |
|---|---|
| CLI `cod-doc clone URL [DIR] [--project SLUG]` | URL — `<URL remote>/<project_uid>`; готовую строку печатает `sync status` (`clone_url`); `--json` |
| CLI `cod-doc sync export-pending -p P [--out F]` | конверт `{v, kind: "pending", slug, exported_at, ops, changeset}`; файл новый, 0600, не перезаписывается; без `--out` — stdout |
| CLI `task checkout --offline`, MCP `task_checkout(offline=…)` | ответ несёт `lease`: `leased` / `offline` / `None` |
| CLI `task show`, MCP `task_get` | `renamed_from` / `renamed_to` / `note` для закреплённого провизорного ID |
| CLI `doc update`, MCP `doc_update` | `warnings`: «история документа остаётся на remote» при повышении уехавшего |
| MCP `sync_resolve` | ответ несёт `marker` |
| `sync status` (CLI, MCP) | `leases`, `clone_url`, `restricted_rows`, `epoch_local` (по схеме БД, раньше константа), `epoch_remote` (из `meta.json`, раньше заглушка) |
| `RemoteStore` | новый метод `list_prefix(project_uid, prefix)`; остальное — существующие `get_json`/`cas_json`/`delete_json`, `put_snapshot`/`get_snapshot` |

## 4. DoD RFC 29 §11.1

Пункты задачи: 1 — тест, падавший до правки; 2 — таблица в реестре, гейт
CLO-001; 3 — запись через `Session`/`record`, гейт CLO-008; 4 — миграция
симметрична, smoke, тест сохранности; 5 — новый тул/команда на обеих
поверхностях, строка в `docs/mcp-integration.md`, `regen.sh`, паритет; 6 —
коды ошибок из §10.10; 7 — CI зелёный на PR; 8 — `task_complete` +
`task_release`.

| Задача | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| CLO-028 | ✅ `RemoteConfigError: SYNC-110 … транспорт s3:// ещё не реализован`; `ModuleNotFoundError: cod_doc.infra.remote.s3`; первая версия — `test_json_delete_requires_current_etag[s3]` `assert True is False` | — | — | — | — (транспорт) | ✅ `SYNC-101/110/201/204` | ✅ | ⚠️ |
| CLO-030 | ✅ `ImportError: cannot import name 'leases'`; двойник — `IntegrityError: UNIQUE … task.project_id, task.task_id` | ✅ `sync_lease`, `entity_alias` — local | ✅ закрепление под `suspended` переписывает на месте, провизорный ID не уезжает | — | ✅ `renamed_*` в `task_get` и `task show` | ✅ `SYNC-107` | ✅ | ⚠️ |
| CLO-044 | ✅ 4 из 6 порядков — `assert {'blocked', 'done'} == {'done'}`; `AttributeError: … 'CHAIN_COLUMNS'` | — | ✅ | — | — | — | ✅ | ⚠️ |
| CLO-045 | ✅ `assert {'a': {('task…', 'status')}} == {'a': set(), …}`; `SYNC-102: … 'sync_resolution', которая не синхронизируется` | ✅ зарезервированный `entity_kind` вне реестра таблиц | ✅ маркер — операция журнала в транзакции `resolve_conflict` | ✅ `0052` — nullable `ADD COLUMN`, upgrade/downgrade | ✅ `marker` в ответе `sync_resolve` | ✅ `SYNC-102`/`SYNC-104` | ✅ | ⚠️ |
| CLO-046 | ✅ `AttributeError: 'MetadataUpdate' … 'warnings'`; `assert 3 == 0`; security — `assert 'internal' == 'restricted'` | ✅ решение 20 реестра, `is_withdraw` | ✅ отзыв пишется в транзакции повышения | — | ✅ `warnings` в `doc update` и `doc_update` | ✅ `SYNC-102`, `DM-005` | ✅ | ⚠️ |
| CLO-031 | ✅ `DID NOT RAISE CheckoutLeaseHeldError`; `MergeRequiredError: … double_checkout … (CLO-031)`; `TypeError: … unexpected keyword argument 'offline'` | — | ✅ событие в транзакции снятия, сеть после коммита (ADO-040) | — | ✅ `--offline` / `offline=`, `_cod-doc`; `reconcile*` — `internal_only` паритета | ✅ `SYNC-108/109/110` | ✅ | ⚠️ |
| CLO-047 | ✅ `assert ['M', 'L', 'K', 'N'] == ['K', 'L', 'M', 'N']`; `{'a': 'b-1'} != {'a': 'c-1'}`; конфликт `text` на A | — | ✅ | — | — | — | ✅ | ⚠️ |
| CLO-048 | ✅ `CheckoutLeaseHeldError: SYNC-108` после TTL; `AttributeError: 'FileRemote' … 'list_prefix'`; security — `OverflowError: date value out of range` | — | ✅ событие `task.checkout_lease_taken_over` | — | — (поведение существующих команд) | ✅ `SYNC-108` для аренды не по форме | ✅ | ⚠️ |
| CLO-029 | ✅ `KeyError: 'snapshot'`; `KeyError: 'clone_url'`; security — 3 × `DID NOT RAISE ObjectMissingError` | ✅ снапшот несёт только `SHIPPED_COLUMNS` без придержанных | ✅ загрузка через `apply.create_row`/`journal.apply_row` под `suspended`, событие `sync.cloned` | — | ⚠️ CLI `clone`; MCP — `surface_debt` (§7) | ✅ `SYNC-102` | ✅ | ⚠️ |
| CLO-033 | ✅ `assert ['01a153c7-…рет два', …] == []`; `DID NOT RAISE RestrictedLeakError` ×2; `AttributeError: … 'restricted_rows'` | — | ✅ вычистка хвоста — внутри упаковки | — | ✅ `restricted_rows` в CLI и MCP `sync_status` | ✅ `SYNC-001` | ✅ | ⚠️ |
| CLO-032 | ✅ SCN-095 `assert not True` (миграция накатилась); `AttributeError: … 'epoch_opened'`; `No such command 'export-pending'` | — | ✅ событие `sync.epoch_opened` в транзакции push | — (миграций нет; гейт реестра эпох) | ⚠️ `export-pending` только CLI: БД не меняет, обоснование — в докстринге гейта паритета `sync_service` (§5 D23) | ✅ `SYNC-102/104/105/106` | ✅ | ⚠️ |

Пункт 7 — CI на коммитах слияния `a59bcda`, `7aa81c6`, `2fb5f13`,
`00bd69a`, `74d3ba4`, `5c324ca`, `9f98a40`, `0d89cad`, `9b5a472`, `34b4574`
зелёный. Джоба `S3 remote (MinIO)` входит в
обязательный агрегат `Test py3.14` с CLO-028: контракт `RemoteStore`, аренды,
перехват и clone прогоняются против MinIO на каждом PR. Пункт 8 аудит не
сверял: живую БД он не читает. Проверить — `cod-doc task show CLO-028` …
`CLO-033`, `CLO-044` … `CLO-048`.

**Сценарии секции.** SCN-082 →
`tests/services/sync/test_snapshot_clone.py::test_scn_082_clone_restores_remote_state_from_snapshot_and_tail`,
`tests/infra/remote/test_clone_contract.py::test_scn_082_clone_through_the_transport`
(`file` и `s3`), `tests/cli/test_cmd_sync.py::test_scn_082_clone_on_a_clean_machine_restores_the_remote_project`;
SCN-086 → `tests/services/sync/test_id_leases.py::test_scn_086_exhausted_offline_lease_gives_provisional_id_pinned_on_push`;
SCN-094 → `tests/services/sync/test_checkout_lease.py::test_scn_094_second_checkout_is_refused_before_any_work`;
SCN-095 → `tests/services/test_update_epochs.py::test_scn_095_update_with_unpushed_ops_before_an_epoch_stops_with_sync_106`;
SCN-096 → `tests/services/sync/test_restricted_never_leaves.py::test_scn_096_restricted_document_never_reaches_any_changeset_or_snapshot`.
Все остаются `draft`. Текст SCN-082 расходится с реализацией (§7 D-F12).

## 5. Решения, отступающие от RFC

Нумерация сквозная по секции, источник — таблицы отступлений в телах PR.
Решения CLO-044/045 записаны в аудите секции C как C36/C37 и здесь не
повторяются.

| # | RFC | Сделано | Почему | PR |
|---|---|---|---|---|
| D1 | §3.5: удаление ключа | снятие аренды на S3 — conditional PUT пустого надгробия; `get_json` видит его как отсутствие ключа | MinIO RELEASE.2025-09-07 принимает `DeleteObject` с `If-Match` и игнорирует условие; HEAD + DELETE — TOCTOU (§6.1) | #229 |
| D2 | §3.3 п. 2: суффикс провизорного ID — base32 от uid | у генератора суффикс случайный; у строки, уступившей двойнику, — от uid | генератор выдаёт ID до flush, uid ещё нет; уникальность проверяется локально | #232 |
| D3 | §3.3 п. 3: закрепление — обычная операция | переписывание на месте (строка, ссылки, хвост) под `suspended` | провизорный ID не уезжает, другим репликам не с чем сталкиваться | #232 |
| D4 | §3.3: остаток аренды — один диапазон | блок, примыкающий к остатку, его продлевает; иначе остаток < `LOW_WATER` сгорает | одна строка `sync_lease` на префикс (§10.1) | #232 |
| D5 | §10.6: `lease_number(session, project_id, prefix)` | + `kind`: номер, занятый явным ID, пропускается | явные ID внутри арендованного блока | #232 |
| D6 | §10.11 | новое событие `sync.id_yielded`; алиас `pinned` — только на закрепившей реплике | уступка номера в §10.11 не описана; провизорный ID видела только она | #232 |
| D7 | §3.2: отзыв — не описан до CLO-046 | строки тела у получателя остаются, тело затирается (`WITHDRAW_BLANK`); придержанные колонки ссылок физически не чистятся | пересозданная при понижении секция оторвала бы ревизии у получателей; дайджест уже берёт значение приёмника (F8) | #234 |
| D8 | — | чужая правка строки, которую держит local только отозванный документ, поглощается, а не отвергается | отказ навсегда останавливал бы pull владельца | #234 |
| D9 | — | карантин своих правок получателя — local-событие `sync.withdraw_quarantined`, не новый вид `sync_conflict` | новый вид требует миграции CHECK, а `sync_conflict` параллельно правили CLO-044/045 | #234 |
| D10 | §10.6: `checkout_lease(sf, …)` / `release_lease(sf, …)` | аренда встроена в `checkout_service.checkout(session, …)`; сеть — до первой записи, снятие — хуком после коммита/отката | вызывающие (MCP, CLI, web, `agent_pick`, `complete`) передают сессию | #236 |
| D11 | §3.6: двойной захват ловится при pull | провизорная отметка, чей момент не совпал с `checked_out_at`, снимается без сети | после CLO-044 в БД у всех замок цепочки | #236 |
| D12 | §3.7: `--take ours` = «наше» | у `double_checkout` `ours` — замок цепочки, одинаковый на всех репликах; держатель аренды выводится из журнала | следствие C36 | #236 |
| D13 | §10.11 `task.checkout_lease_released` | событие — в транзакции снятия замка, сетевое снятие — после коммита | событие обязано быть в транзакции мутации (ADO-040) | #236 |
| D14 | `remotes`: какой remote | `origin`, без него — первый по имени | у checkout нет `--remote` | #236 |
| D15 | §3.6: TTL снимает замок | **Заменяет** строку #236 «checkout истёкшую чужую аренду не перехватывает»: перехват при checkout, если задача свободна локально, по etag; чистка `release_stale` листингом. Продления аренды нет | осиротевшую аренду иначе не снимал никто: `release_stale` видит только свою БД | #238 |
| D16 | §10.10 | аренда не по форме (`acquired_at` впереди часов больше допуска, срок вне `datetime`) — занята, `SYNC-108` с подсказкой `release --force`; не перехватывается как истёкшая | «будущее» может значить спешащие часы держателя; вечным замком она тоже не становится | #238 |
| D17 | §3.5: снапшот — «SQLite authored+journal» | поток строк `CDS1` (суффикс `.db.zst` сохранён); журнал и индекс changeset'ов клон собирает сам из объектов remote | файл SQLite с remote — исполняемая схема (триггеры, представления); дайджест журнал и индекс не покрывает (§6.1) | #239 |
| D18 | §3.6: снапшот раз в N операций | ещё и первый push в remote без снапшота | строки до журнала есть только в снапшоте | #239 |
| D19 | §10.6 `clone(url, *, slug, root, author) -> str`; §10.8 `clone URL` | `clone(url, *, root, slug, author, store) -> CloneResult`; URL = `<URL remote>/<project_uid>`, слаг — с remote | в `RemoteStore` нет листинга проектов; результат нужен для `--json` | #239 |
| D20 | §10.1 `meta.json`; §10.11 | + ключ `snapshot: {epoch, sha, ops}`; + событие `sync.cloned` | указатель двигается CAS; write-path оставляет след (ADO-040) | #239 |
| D21 | §3.2: restricted — гейт захвата | + пересверка хвоста при упаковке и растяжка; операции restricted-документа **удаляются** из журнала, а не придерживаются | оставленные операции навсегда держали бы `pending_ops > 0` и блокировали `update` (`SYNC-106`) и смену эпохи (`SYNC-105`) | #240 |
| D22 | §10.10 `SYNC-105` — эпоха сменилась при незапушенных операциях | ещё и pull отставшей реплики без хвоста; незнакомое, но правильно записанное имя эпохи — «эпоха новее», не `SYNC-102` | acceptance CLO-032; эпоху открыл релиз, которого у реплики нет | #241 |
| D23 | §3.6 п. 1: `update` требует push; §10.7 | проверка в фазе B новым кодом, а не до свапа; непрочитанная БД — тоже `SYNC-106`; `export-pending` только в CLI, обратного `import-pending` нет | какие миграции откроют эпоху, знает только новый код; fail closed; запись файла по пути клиента на машине общего демона — не дело тула | #241 |
| D24 | §3.6 п. 3: отставшие реплики стартуют со снапшота эпохи | не реализовано | changeset'ы старой эпохи, запушенные до открытия новой, отставшая реплика после `update` применяет как обычно (§7 D-F3) | #241 |

## 6. Находки

### 6.1 Безопасность

Каждая закрыта в своём PR с тестом, падавшим до правки. Decode и ReDoS
секции B сюда не входят.

| PR (коммит) | Находка | Решение |
|---|---|---|
| #229 (`44e8f48`) | MinIO RELEASE.2025-09-07 игнорирует `If-Match` на `DeleteObject`: удаление аренды по устаревшему etag проходило — живой контракт `test_json_delete_requires_current_etag[s3]` `assert True is False` | снятие — conditional PUT пустого надгробия; `cas_json(expected=None)` пишет поверх него с `If-Match` его ETag (D1) |
| #229 (`3d718c0`) | `bitnamilegacy/minio:2025.7.23` на PUT с `If-Match` по отсутствующему ключу **создаёт** объект: CAS «заменить существующее» на таком сервере не CAS — `test_cas_rejects_expected_when_ref_absent[s3]` | образ CI сменён на сервер, который отвечает 412; контрактный тест остаётся и ловит такой сервер у пользователя |
| #229 (`44e8f48`) | текст ошибок S3 | сообщение сервера и строка исключения botocore в ошибку не попадают — только операция, `s3://бакет/ключ`, код и HTTP-статус; креды в URL отвергаются, URL в ошибке не повторяется |
| #234 (`f289026`) | **несанкционированное понижение (HIGH)**: чужое понижение снимало restricted с документа | понижение принимается, только если документ restricted по **чужому** отзыву, пришло от реплики, чей отзыв в силе (`restricted_by`), и имеет форму первого upsert'а; иначе поглощается без записи |
| #234 (`f289026`) | **разрушение по чужой команде (MEDIUM)**: отзыв затирал неотправленные правки получателя бесследно | правки и тексты до затирания — в local-событие `sync.withdraw_quarantined`; отзыв документа, которого журнал получателя не знает, — `SYNC-102` |
| #234 (`f289026`) | **пробел в чистке хвоста (MEDIUM)**: один проход по HLC пропускал правку секции, стоящую в хвосте раньше удаления с `document_id` | тело документа — неподвижная точка по живым строкам и всем операциям хвоста |
| #240 (`08cf562`) | документ, ни разу не уезжавший и повышенный до restricted после захвата, но до push, отзыва не пишет — его хвост (создание, секции, тексты) уходил первым push'ем (известная граница #234) | `gate.shippable_tail` пересматривает хвост по текущим меткам перед упаковкой push и `export-pending`; растяжка `check_ops`/`check_row` останавливает changeset или снапшот с uid restricted-строки (`SYNC-001`) |
| #238 (`45b2679`) | тело аренды checkout — недоверенный ввод: `acquired_at` из будущего держал замок год, огромный `ttl_minutes` — вечно, `OverflowError` в сроке ронял checkout, сбой `sweep` на одном ключе обрывал чистку; тело целиком попадало в текст ошибки | `ttl_minutes` 1…10080, строки 1…256 символов; аренда из будущего (больше допуска 2 мин) или со сроком вне `datetime` — «не по форме», занята, снимается `release --force`; `sweep` пропускает только упавший ключ; тело в тексты ошибок и логов не попадает |
| #239 (`32b2fa3`) | снапшот вёз журнал `sync_op`, индекс `changeset` и алиасы, и клон принимал их на веру: дайджест головы их не покрывает. Подложенная операция, запись индекса (pull пропустил бы настоящий changeset) или алиас (будущие операции ушли бы в другую строку) проходили — `DID NOT RAISE ObjectMissingError` ×3 | снапшот несёт только строки и алиасы, `op`/`changeset` — `SYNC-102`; журнал и индекс клон собирает из changeset'ов remote по адресам (`decode` сверяет sha, `check_clock`); алиас — только `merged`, выживший — загруженная строка той же таблицы, проигравший не жив и есть в проверенном журнале; дайджест загруженного сверяется до коммита |
| #241 (`4b5fd98`) | `update_service` писал URL hub-БД в лог и отчёт — с паролем; БД, которую проверка эпохи не прочитала, мигрировалась бы | в лог — `make_url(…).render_as_string(hide_password=True)` и только имя класса исключения, в отчёт — слаги; тест проверяет отсутствие пароля в логе и отчёте. Непрочитанная БД — `SYNC-106` (fail closed), флага «катить без проверки» нет |
| #235 (`54196cb`) | маркер разрешения с remote — новый недоверенный ввод | форма (upsert без `base`, ровно четыре поля, uid — UUID, вид из `sync_conflict.kind`, поле уезжает, таблица под захватом) и гейт реестра (restricted-строка, придержанное поле, строка чужого проекта hub-БД — `SYNC-102`) |

**Цепочка поставки CI-образа MinIO.** Сборки MinIO community больше не
публикуются: `minio/minio` снят с Docker Hub, quay.io закрыт, `dl.min.io`
отвечает 410. Джоба `S3 remote (MinIO)` поднимает
`pgsty/minio:RELEASE.2026-08-04T00-00-00Z` — сборку того же кода сторонним
сопровождающим (github.com/pgsty/minio), запиненную digest'ом
`sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372`.
Digest защищает от подмены тега, но доверие переносится на сопровождающего;
образ получает только `minioadmin`-креды джобы и слушает `127.0.0.1:9000`,
секретов репозитория у него нет. Обновление — вручную, с повторным прогоном
контракта (сервер, создающий объект на `If-Match`, уронит
`test_cas_rejects_expected_when_ref_absent[s3]`). Dependabot digest этого
`docker run` не видит.

**Не исправлено.** 🟠 AI-ревью #234 (подтверждено верификатором):
`apply.py` кладёт карту применённых отзывов в `session.info`
(`_APPLIED_WITHDRAWALS`) и снимает её только на успешном пути
`apply_changeset`. Если changeset упал и вызывающий переиспользует ту же сессию,
устаревшая запись открывает путь понижения через `applied.get` раньше проверки
`restricted_by`. Pull сейчас открывает новую транзакцию на changeset и при сбое
выходит, так что путь требует переиспользования сессии; правка — `pop` в
`finally` (§7 D-F1). Остальные замечания AI-ревью волны — 🟡/🔵
(10/13/2/21/20/8/11+2/20+1/1/11 по PR), блокирующих нет.

### 6.2 Расхождения, найденные property-тестом CLO-027

Прогон `CLO027_SEEDS=500` до CLO-047 падал на семи сидах; все три причины
исправлены в #237 с регрессионным тестом. Генератор в волне расширен
CLO-044 (снято ограничение «два писателя статуса на раунд») и CLO-046 (общий
документ: A повышает/понижает его, A и ещё одна реплика правят тело).

| # | Сиды | Что | Причина | Фикс | Регрессия |
|---|---|---|---|---|---|
| 1 | 35 | дайджест: буквы секций плана K/M переставлены | `keys._claim` искал заявку строки на букву только в `sync_op`; операции применяемого changeset'а попадают туда после всех вставок, и секция, вставленная раньше в том же changeset'е, считалась заведённой до журнала и проигрывала всем. Воспроизводится и с выключенным повышением — не CLO-046 | заявки берутся и из `ctx.batch`, как у цепочки статуса | `test_natural_keys.py::test_letters_of_rows_inserted_earlier_in_the_same_changeset_count` |
| 2 | 358 | дайджест A ≠ B, C: `dependency.note` b-201 против c-201 | три реплики завели одно ребро (uid детерминирован); своя операция создания оставалась в хвосте после того, как её переписала более поздняя чужая вставка, и третью вставку сравнивали только с ней | если история новее своей операции создания, решает штамп истории | `test_convergence.py::test_older_twin_insert_does_not_override_a_newer_one_from_history` |
| 3 | 93, 193, 201, 489, 490 | множество подписей конфликтов зависит от перестановки: `text` на `section.body` | понижение шлёт тело операцией создания (`reship_ops`, `base` пуст); у приёмника «чужая вставка против своей правки» шла LWW, у понижавшего «своя вставка против чужой правки» — через diff3 без общего предка | LWW в обе стороны; проигравшая вставка пишет значение последней записи истории, иначе у приёмника оставалось затёртое отзывом пустое тело | `test_convergence.py::test_reship_after_lowering_against_an_edit_merges_the_same_in_any_order` |

После #237 `CLO027_SEEDS=500` прошёл без падений (8 мин 11 с при `-n 6`).
CLO-045 прогнан на 500 сидах и на коммите CLO-044, и на голове ветки.

## 7. Что переходит дальше

Строки F1–F11 аудита секции C, закрытые в волне: F1 (CLO-031), F2 (CLO-030 —
уступка номера `leases.yield_number` вместо `MergeRequiredError(CLO-030)`), F4
(CLO-045), F8 (CLO-046), F11 (CLO-044).

| # | Находка | Куда |
|---|---|---|
| D-F1 | 🟠 AI-ревью #234: `_APPLIED_WITHDRAWALS` в `session.info` переживает откат changeset'а (§6.1) | правка `pop` в `finally` `apply_changeset` с тестом на переиспользование сессии |
| D-F2 | Остаточный риск алиасов в clone: алиас `merged` дайджестом не покрыт, проверяется правдоподобие (выживший загружен, проигравший мёртв и есть в журнале). Тот, кто пишет в remote, может связать мёртвый uid из истории с другой живой строкой той же таблицы — будущие операции по проигравшему uid уйдут в неё | подписи changeset'ов/снапшотов (вне v1) или вывод алиасов из проигрывания журнала при clone |
| D-F3 | §3.6 п. 3 «отставшие реплики стартуют со снапшота эпохи» не реализован (D24) | при первой эпохе, у которой старые changeset'ы не ложатся на новую схему |
| D-F4 | Clone скачивает все changeset'ы истории (по объекту на changeset, без проигрывания) — иначе журнал проверить нечем; потолок `_local.MAX_HISTORY` = 100 000 | подписи истории или снапшот журнала, проверяемый дайджестом |
| D-F5 | `export-pending` только в CLI и без обратного `import-pending`: операции переносятся руками | осознанно (D23); пересмотреть по первому реальному переносу |
| D-F6 | История remote, переписанная без локальной головы, — по-прежнему `MergeRequiredError(task="CLO-032")` (`pull.py:_REWRITTEN`); выход — `export-pending` и новый clone, подсказка в сообщении указывает на закрытую задачу | текст ошибки → `export-pending` + `clone` |
| D-F7 | CLO-046: получатель, правивший отозванный документ локально, после понижения расходится ревизиями (его ревизии local, после понижения вошли бы только в его дайджест); генератор такого не порождает | отдельная задача по ревизиям отозванного документа |
| D-F8 | CLO-046: встречные повышение и понижение с разных реплик расходятся по построению — каждая держит свой отзыв | правило старшинства отзыва; до тех пор — ручное разрешение |
| D-F9 | CLO-046: `doc import` (frontmatter) поднимает метку с тем же отзывом, но предупреждение только в логе захвата | при следующей правке `doc import` |
| D-F10 | CLO-033: удаление строки, которой уже нет, со ссылкой на документ по `doc_key` (строкой, не uid) по текущей метке не пересматривается; растяжка ловит только uid | при следующей правке `gate` |
| D-F11 | `clone` в MCP — в `surface_debt` гейта паритета, хотя отсутствие намеренное (§10.7: MCP-демон общий, регистрация проекта — дело человека) | перенести в `internal_only`/исключение с тем же обоснованием, чтобы ratchet не считал его долгом |
| D-F12 | SCN-082 шаг 1 — `cod-doc clone s3://bucket/prefix --project demo ./demo`; реальный URL — `<URL remote>/<project_uid>` (D19). Сценарии — проекция БД, markdown не правится | `scenario_set_steps` на SCN-082 (ниже) |
| D-F13 | `~/.cod-doc/credentials` (§10.12) не читается: `open_store` зовёт `open_remote` без кредов, S3 работает только через стандартную цепочку AWS (env, `~/.aws`) | CLO-038 (`remote login`, credentials 0600) |
| D-F14 | Аренда checkout и цепочка могут разойтись офлайн: B взяла задачу онлайн позже, чем A офлайн — цепочка держит A (меньший штамп), аренда у B. Показывается конфликтом `double_checkout`, разрешение переписывает аренду. Продления аренды нет: работа дольше TTL с неуехавшей операцией допускает перехват и тот же конфликт | осознанно; продление — по запросу |
| D-F15 | RFC §11.2 CLO-034 называет миграцию `0052_sync_access`, а `0052` занята `0052_sync_conflict_opened_hlc` (CLO-045) | CLO-034 — следующий свободный номер, RFC поправить |
| D-F16 | Инвариант §5 capability «changeset прошлой эпохи не применяется к реплике новой эпохи» сильнее реализации: старые changeset'ы, запушенные до открытия эпохи, применяются после `update` (D24) | вместе с D-F3 |
| D-F17 | F5 (nullable FK без каскада в хвосте), F6 (`partial`-снимок `delete_edit` — строки до журнала: clone их грузит строкой, операции создания не появляется), F7, F9, F10 секции C | без изменений |

**Нужная правка в БД (SCN-082).** `scenario_set_steps(project="cod-doc",
scenario_id="SCN-082", steps=[…])`, шаги:

1. `Выполнить cod-doc clone <URL remote>/<project_uid> --project demo ./demo (готовую строку печатает cod-doc sync status -p demo на исходной машине, поле clone_url)`
2. `Выполнить cod-doc sync verify -p demo`

Предусловие («На s3://-remote лежит проект со снапшотом…») верно и после
CLO-029.

## 8. Итог секции

Секция D — 6/6 по коду, плюс пять продолжений. Remote теперь — `file://` или
`s3://`; проект заводится на чистой машине `cod-doc clone` со снапшота с
проверкой истории по адресам; номера и замок checkout арендуются через CAS
remote, офлайн-работа получает провизорные ID и провизорный замок; смена
эпохи схемы не теряет незапушенные операции; restricted-документ не попадает
ни в changeset, ни в снапшот, а повышенный после отправки — отзывается у
получателей. Следующий шаг — секция E: умный remote (`/api/sync/v1`,
`HttpRemote`), токены, роли и проверка `author` на push (CLO-034…039).
