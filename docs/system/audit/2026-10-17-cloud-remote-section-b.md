---
type: audit-report
scope: cloud-remote-section-b
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-10-17
last_updated: 2026-10-17
related_docs:
  - ../../../proposals/29-cloud-remote-sync.md
  - ../capabilities/remote-sync.md
  - ../scenarios/remote-sync.md
  - ../../mcp-integration.md
  - 2026-10-15-cloud-remote-section-a.md
audience: [contributors, agents]
---

# Audit — Plan `cloud-remote-2026-10`, Section B closure («Changeset, file-remote, push/pull»)

> **Контекст.** [RFC 29](../../../proposals/29-cloud-remote-sync.md) §11.2,
> секция B: журнал `sync_op` из секции A упаковывается в changeset, changeset
> уезжает на remote, другая реплика забирает его и применяет fast-forward'ом.
> Слияние и конфликты — секция C, S3 и умный remote — секции D/E.

## 1. TL;DR

Секция B закрыта по коду: 7/7 задач (CLO-010…016) в `main` тремя PR
(#219, #220, #221), CI на всех трёх коммитах слияния зелёный. Появилось первое видимое пользователю поведение:
`cod-doc remote add file://…`, `push`, `fetch`, `pull`, `sync status|log|verify`
и MCP-тулы `sync_status`/`sync_pull`/`sync_push`. Две реплики на одном
`file://` remote сходятся к одному дайджесту (§8, проверено руками
2026-10-17).

Ограничения, которые видны сразу: pull умеет только fast-forward, remote —
только `file://`, второй реплики без эмуляции clone не завести (§7, F1–F2).
Новых миграций в секции нет. Рантайм сервисов получит команды после
`cod-doc update`.

## 2. Задачи и PR

| Задача | Что | PR → коммит в `main` |
|---|---|---|
| CLO-010 | формат changeset v1: `cod_doc/services/sync_service/changeset.py`, коды §10.10 в `sync_service/errors.py` | #219 → `861da20` |
| CLO-011 | протокол `RemoteStore` (`cod_doc/infra/remote/base.py`) и `FileRemote` (`cod_doc/infra/remote/file.py`) | #219 → `861da20` |
| CLO-012 | `sync_service.push`: упаковка хвоста, CAS, повтор через pull | #220 → `e6ef3bb` |
| CLO-013 | `sync_service.fetch`/`pull` с fast-forward, применение `apply.py` | #220 → `e6ef3bb` |
| CLO-014 | дайджест состояния и `verify`: `sync_service/digest.py`, `sync_capture.shipped_state` | #221 → `e01c7da` |
| CLO-015 | CLI `cod_doc/cli/cmd_sync.py`: `remote`, `fetch/pull/push`, `sync status/log/verify` | #221 → `e01c7da` |
| CLO-016 | MCP `cod_doc/mcp/tools/sync_tools.py`, паритет-спек `sync_service` | #221 → `e01c7da` |

## 3. Что построено

- **Changeset v1** (§10.4). `Changeset`/`Op` — frozen dataclass'ы, обязательные
  поля проверяются при построении. Каноническая форма: UTF-8 без
  экранирования, ключи по алфавиту на всех уровнях, разделители `(",", ":")`,
  время `YYYY-MM-DDTHH:MM:SS.ffffffZ`, операции по `(hlc, op_id)`. Адрес —
  sha256 канонических байтов. На проводе — `b"CDC1"` + zstd из stdlib
  `compression.zstd` (Python 3.14), новой зависимости нет. Golden-тест
  держит sha `87d8b8ca…3f82`, байты в нём написаны руками по спеке.
- **`decode` — недоверенный вход.** Любой сбой даёт `SYNC-102`: чужая версия,
  битый или дописанный zstd-кадр, sha ≠ адресу, структура не по §10.4,
  неканонические байты. Распаковка ограничена 64 МиБ, вложенность — 64,
  глубина считается до `json.loads` одним линейным проходом.
- **`RemoteStore` + `FileRemote`** (§10.5). CAS ref'ов и JSON — под
  `fcntl.flock` на файле-замке; запись через временный файл, `fsync` →
  `rename` → `fsync` каталога; etag — sha256 байтов. Объекты и снапшоты
  неизменяемы, повторный put — no-op. Каталог remote недоверенный: спуск
  openat-стилем (`O_DIRECTORY | O_NOFOLLOW` с `dir_fd` на каждом сегменте),
  чтение `O_NOFOLLOW | O_NONBLOCK` + `fstat`, потолки ref 128 Б / JSON 1 МиБ /
  объект 128 МиБ / снапшот 1 ГиБ. Контрактный тест параметризован фабрикой URL —
  S3 и Http добавят по строке.
- **`push`.** Три фазы: упаковка хвоста `sync_op` в один changeset (своя
  транзакция) → обмен без транзакции (`get_ref`, `has_objects`/`put_object`,
  `cas_ref`) → фиксация только после CAS (`sync_op.changeset`, голова `@local`,
  `sync_ref`, событие `sync.pushed`). Если голова remote ≠ локальной, CAS не
  делается вовсе. Отказ CAS → pull → упаковка заново, до 3 повторов, дальше
  `SYNC-103`; pull, не сдвинувший голову, даёт `SYNC-103` сразу. Сбой между CAS
  и фиксацией лечится следующим pull: свои операции он узнаёт и только
  фиксирует. Время в полях нормализуется при упаковке по типу колонки
  (`_values.to_wire`).
- **`fetch`/`pull`.** Сначала сеть: объекты от `refs/main` до первого
  применённого changeset'а, с потолками `MAX_FETCH_CHANGESETS`/`MAX_PARENTS` и
  проверкой цикла. Затем до первой записи: схема (`SYNC-104`, не применяется
  ни один changeset pull'а), штампы (`apply.check_clock`), расхождение. Каждый
  changeset — своя транзакция под `sync_capture.suspended`: поиск строки по uid
  (у тег-таблиц — по `deterministic_uid`), FK uid → `row_id`, `position` новой
  строки по классу места, пересборка derived (ссылки best-effort в SAVEPOINT,
  FTS), чужие операции — в `sync_op` с `changeset = sha`, `observe` часов,
  событие `sync.pulled`. Первая запись транзакции — CAS головы реплики.
- **Дайджест** (§3.7). sha256 потока строк `[таблица, uid, поля]` в порядке
  `(entity_kind, uid)`; поля — ровно то, что уехало бы операцией
  (`shipped_state`), время через тот же `to_wire`. `row_id`, `position`,
  restricted и journal не входят. Поток считается без материализации: `ORDER
  BY uid` в SQL (на PostgreSQL `COLLATE "C"`). `push` пишет дайджест в
  changeset; `verify` сравнивает дайджест реплики с дайджестом головы remote.
- **Поверхности.** CLI — таблица ниже; MCP — `sync_status(project, remote,
  verify)`, `sync_pull(project, remote, author, dry_run)`,
  `sync_push(project, remote, author)` на профилях `standard`/`full`. Ошибки —
  текст с префиксом `SYNC-NNN` (`ClickException` на CLI, `ValueError` на MCP).
  Паритет стережёт `tests/services/test_sync_mutation_surface_parity.py`.

| Команда | Опции |
|---|---|
| `remote add NAME URL -p P` | `--kind auto\|file\|s3\|https`, `--json` |
| `remote list` / `remote remove NAME` | `-p P`, `--json` |
| `fetch` / `pull` / `push` | `-p P`, `--remote` (по умолчанию `origin`), `--author`, `--json`; `pull --dry-run`, `push --no-retry` |
| `sync status` | `-p P`, `--remote`, `--verify`, `--json` |
| `sync log` | `-p P`, `-n 20`, `--json` |
| `sync verify` | `-p P`, `--remote`, `--json`; при расхождении exit 1 |

Не выставлены, сервисов ещё нет: `remote login` и `member`/`token` (CLO-038),
`clone` (CLO-029), `sync conflicts`/`resolve` (CLO-026), `sync export-pending`
(CLO-032), `task checkout --offline` (CLO-031).

## 4. DoD RFC 29 §11.1

Пункты задачи: 1 — тест, падавший до правки; 2 — таблица в реестре, гейт
CLO-001; 3 — запись через `Session`/`record`, гейт CLO-008; 4 — миграция
симметрична, smoke, тест сохранности; 5 — новый тул/команда на обеих
поверхностях, строка в `docs/mcp-integration.md`, `regen.sh`, паритет; 6 —
коды ошибок из §10.10; 7 — CI зелёный на PR; 8 — `task_complete` +
`task_release`.

| Задача | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| CLO-010 | ✅ golden падал с `ensure_ascii=True`; чужой sha — `DID NOT RAISE`; глубина — `RecursionError`; квадратичная регулярка — таймаут 30 с | — | — | — | — | ✅ `SYNC-102`, транспортные коды в `infra/remote/errors.py` (B1) | ✅ | ⚠️ |
| CLO-011 | ✅ гонка без `flock`: 4 победителя в раунде 0 (3 из 3 прогонов); ссылки и потолки — 10 из 14 на первой версии | — | — | — | — | ✅ `SYNC-101/110` | ✅ | ⚠️ |
| CLO-012 | ✅ SCN-083 без проверки головы — `DID NOT RAISE PushRejectedError`; `to_wire` — неканоническое время; сеть в транзакции — `database is locked` | ✅ служебные таблицы — local с секции A | ✅ запись через `Session` | — | ✅ через CLO-015/016 | ✅ `SYNC-103` | ✅ | ⚠️ |
| CLO-013 | ✅ SCN-085 — `DID NOT RAISE SchemaMismatchError`; без `suspended` — `assert 4 == 0`; 8 из 11 в `test_apply_untrusted.py` до security-правок | — | ✅ под `suspended`, чужие операции — история (B14) | — | ✅ через CLO-015/016 | ✅ `SYNC-102/104`; `MergeRequiredError` без кода (B13) | ✅ | ⚠️ |
| CLO-014 | ✅ дайджест по всем колонкам ронял 4 теста (`'c949ff…' == '71b93c…'`); restricted — `'8ab873…' == '63fc6d…'` | — | — | — | ✅ `sync status --verify`, `sync verify`, `sync_status(verify=True)` | — | ✅ | ⚠️ |
| CLO-015 | ✅ `No such command 'remote'` | — | — | — | ✅ `_cod-doc` перегенерирован | ✅ `SYNC-110` на неизвестной схеме | ✅ | ⚠️ |
| CLO-016 | ✅ `KeyError: 'sync_pull'`; паритет — `{'pull': ['mcp'], 'push': ['mcp']}`; CLO-009 — `мутации без случая захвата` | — | ✅ случаи `push`/`pull` в `test_sync_capture_write_paths.py` | — | ✅ строка `sync.*` в `docs/mcp-integration.md`, счётчики `regen.sh` | ✅ | ✅ | ⚠️ |

Пункт 4 к секции не относится: миграций нет. Пункт 7 — CI на коммитах
слияния `861da20`, `e6ef3bb` и `e01c7da` зелёный (`gh run list --branch main`). Пункт 8 этот аудит не
сверял: живую БД он не читает. Проверить — `cod-doc task show CLO-010` …
`CLO-016` (статус `done`, `completed_commit`, нет замка).

**Сценарии секции.** SCN-083 → `tests/services/sync/test_push.py::test_scn_083_push_that_missed_a_foreign_push_is_rejected`;
SCN-085 → `tests/services/sync/test_pull.py::test_scn_085_changeset_with_newer_schema_is_refused_without_partial_apply`.
Оба остаются `draft`, как и прочие сценарии `remote-sync`.

## 5. Решения, отступающие от RFC

| # | RFC | Сделано | Почему | PR |
|---|---|---|---|---|
| B1 | исключения в `sync_service/errors.py` | транспортные — в `infra/remote/errors.py`, реэкспорт | их бросает infra, а infra не импортирует services | #219 |
| B2 | `SYNC-108` в таблице | отложен до аренды checkout | основа `CheckoutConflictError` — в `checkout_service`, импорт сервиса без потребителя | #219 |
| B3 | CAS file — `os.link` + rename | `flock` + rename | `link` не умеет атомарно сравнить и заменить существующий ключ | #219 |
| B4 | §10.5 без `get_snapshot` | добавлен | clone читает тело снапшота; REST §10.9 его отдаёт | #219 |
| B5 | — | `hlc_max` обязан равняться max hlc операций | производное поле; расхождение — ошибка упаковщика | #219 |
| B6 | — | `parents` не сортируются, повторы запрещены | первый родитель merge-changeset'а значим (§3.6) | #219 |
| B7 | — | непригодный объект — `SYNC-102`, нарушение раскладки file-remote — `SYNC-101` | новых кодов нет (DoD п. 6) | #219 |
| B8 | — | `s3://`, `https://` пока `SYNC-110` | до CLO-028 / CLO-035 | #219 |
| B9 | локальная голова не названа | `sync_ref` под псевдо-remote `@local` | имя remote проходит `[A-Za-z0-9_]…` и совпасть не может; голова — одна строка | #220 |
| B10 | `digest` обязателен | нули до CLO-014 | закрыто в #221: push пишет дайджест | #220 |
| B11 | `epoch` — голова эпохи | константа `0050_sync_rank` | эпохи и `meta.json` — CLO-032 | #220 |
| B12 | merge-changeset при локальном хвосте (§3.6) | неупакованный хвост встаёт поверх новой головы | хвост ещё не changeset — переписывать нечего; merge-changeset — секция C | #220 |
| B13 | — | `MergeRequiredError(NotImplementedError)` без кода | граница реализации до секции C; новый код по DoD п. 6 — сначала в RFC | #220 |
| B14 | — | чужие операции пишутся в `sync_op` с `changeset = sha` | история для LWW по полю (CLO-018); push их не упакует | #220 |
| B15 | — | `MAX_DRIFT_MS` = 5 мин в `infra/hlc.py` | в RFC значения нет; штамп дальше отравил бы часы и LWW | #220 |
| B16 | §3.6 «пересборка derived» | ссылки пересобираются best-effort в SAVEPOINT | derived не откатывает применённый changeset (как `doc_service._sync_section_links_safe`) | #220 |
| B17 | push принимает конфиг remote | `store=` для тестов, иначе `remotes[name].url` из конфига, нет записи — `SYNC-110` | `remote add` — CLO-015 | #220 |
| B18 | §3.7: ревизии входят в дайджест | journal (`revision`, `activity_event`) не входит; исключение `parent_revision_id` заготовлено в `EXCLUDED_COLUMNS` | ревизии не синхронизируются до CLO-024; иначе дайджесты расходились бы после любого fast-forward | #221 |
| B19 | §10.12: поле `ProjectEntry.remotes` | ключ `remotes` в extra записи проекта, `Config.project_remotes`/`set_project_remote`/`remove_project_remote` | поле с дефолтом `{}` дописало бы `remotes: {}` к каждому проекту при любом `save` | #221 |
| B20 | `SyncStatus` без результата сверки | поле `verify: VerifyResult \| None` | `sync_status(verify=True)` — MCP-эквивалент `sync verify` | #221 |
| B21 | `status` не оговаривает сеть | при настроенном remote — fetch, `sync_ref` сдвигается | `behind` без скачивания не посчитать: тела changeset'ов локально не хранятся | #221 |
| B22 | — | `open_conflicts=0`, `epoch_remote=None`, `leases={}`; `drift_hint` при `behind > 0` | источников нет до секций C/D | #221 |
| B23 | §10.8: exit 0/1 по ошибкам | `sync verify` при расхождении выходит с 1 | для скриптов, как `diff --exit-code` | #221 |
| B24 | §10.8 `push` без `--no-retry` | добавлен | `max_retries=0` из SCN-083 | #221 |

## 6. Находки безопасности по раундам

Каждая закрыта отдельным коммитом с тестом, падавшим до правки.

| Раунд | Находка | Тяжесть | Решение |
|---|---|---|---|
| B1 (#219) | распаковка zstd без потолка — бомба раздувается целиком | — | `max_length`, `MAX_CANONICAL_BYTES` = 64 МиБ |
| B1 (#219) | вложенный JSON роняет разбор `RecursionError`; регулярка подсчёта глубины квадратична (вход `'"' + '\"' * 100000` — таймаут 30 с) | — | `MAX_JSON_DEPTH` = 64, глубина — один линейный проход до `json.loads`; ловятся `ValueError`/`TypeError`/`KeyError`/`OverflowError`/`RecursionError` |
| B1 (#219) | каталог remote доверялся: симлинк ниже корня, FIFO, каталог на месте файла, файл без потолка | — | спуск `O_DIRECTORY \| O_NOFOLLOW` с `dir_fd`, чтение `O_NOFOLLOW \| O_NONBLOCK` + `fstat`, потолки по роду объекта, читается не больше потолка + 1 байт |
| B2 (#220) | строка по uid и конец FK искались во всей hub-БД | HIGH | поиск только в проекте pull'а (по `project_id` или цепочке родителей); итоговая строка сверяется ещё раз, `project_id` на чужой проект не перевешивается |
| B2 (#220) | поле вне `SHIPPED_COLUMNS` молча пропускалось | MEDIUM | local, `uid`, незнакомое поле, таблица вне захвата → `SYNC-102`; local-строку (restricted и всё под ним, придержанные колонки) нельзя создать, переписать или удалить — тот же `shipped_fields`, что у захвата |
| B2 (#220) | штамп из будущего отравлял часы; реплика операции не сверялась с репликой changeset'а; remote мог выдать себя за эту машину | MEDIUM | `apply.check_clock` до записи: реплика операции = реплике changeset'а, не своя реплика, не дальше `MAX_DRIFT_MS` впереди; `HybridClock.observe` сам обрезает чужой штамп до `wall + MAX_DRIFT_MS` |
| B2 (#220) | каскад `session.delete` сносил строку, которую реестр держит local | MEDIUM | обход поддерева по `apply.CASCADE_PATHS` до удаления: `gated` — отказ, `owned` уходят с родителем; тест сверяет пути со схемой |
| B2 (#220, AI-ревью) | changeset признавался «своим» по одному совпавшему `op_id` — подделка чужим объектом | major | «свой» = своя реплика + каждая операция совпала с журналом целиком, иначе `SYNC-102`; свои тоже проходят схему и `check_clock` |
| B2 (#220, AI-ревью) | два конкурентных pull применяли один changeset дважды | major | CAS головы — первая запись транзакции changeset'а; проигравший пропускает применённый sha |
| B2 (#220, AI-ревью) | мелкие: `json.loads` без проверки формы, `fromisoformat` без обёртки, неизвестная схема БД, `dry_run` без головы, ветвление `parents` без потолка, ранний выход без проверки достижимости | — | `SYNC-001`/`SYNC-102`/`SchemaMismatchError`, `MAX_FETCH_CHANGESETS`, `MAX_PARENTS`, «remote позади» — только если его голова — предок локальной |
| B3 (#221) | гонка вставки `sync_ref`: два pull одной реплики оба вставляли ref, проигравший получал `IntegrityError` (флак `test_concurrent_pulls_apply_each_changeset_once`, 4 из 5) | — | `_upsert_ref` — один `INSERT … ON CONFLICT DO UPDATE` на SQLite и PostgreSQL, `ensure_local_head` — `DO NOTHING`, `_ref_row` читает из БД; после правки 20/20 |

AI-ревью: на #219 — 0 замечаний (specialist correctness упал с ошибкой
вызова); на #220 — 14 замечаний первого прогона, исправления коммитом
`f877ed9`, во втором прогоне 3 жёлтых; на #221 — 3 замечания первого
прогона закрыты или опровергнуты, во втором осталось одно жёлтое (F7).
Находка F2 аудита секции A (ревью #210–#213 не отработало на лимите Ollama)
этим аудитом не закрыта: дифф секции A повторно не ревьюился.

## 7. Что переходит в следующие секции

| # | Находка | Куда |
|---|---|---|
| F1 | pull умеет только fast-forward. Хвост и чужие операции на одной сущности или удаление под чужим FK дают `MergeRequiredError` до первой записи; это `NotImplementedError` без кода `SYNC-NNN`, на MCP приходит `ValueError` без кода | секция C (CLO-018…024); код — вписать в RFC §10.10 |
| F2 | Реплики, заведённые `project add` по отдельности, на pull падают `IntegrityError`: коллизия натуральных ключей у дефолтных рутин (`routine.project_id, name`) и дерева разделов (`doc_node.project_id, node_key`). Откатывается changeset целиком, ранее применённые остаются. Вторую реплику сегодня можно завести только эмуляцией clone (§8) | CLO-029 (clone), CLO-023 (склейка ключей) |
| F3 | Journal (`revision`, `activity_event`) не синхронизируется и не входит в дайджест (B18) | CLO-024 |
| F4 | Remote — только `file://`; `s3://` и `https://` принимаются `remote add` с предупреждением и дают `SYNC-110` | CLO-028 (S3), CLO-035 (умный remote) |
| F5 | `MAX_DRIFT_MS` = 5 мин выбран без значения в RFC (B15) | вписать в RFC §3.5/§10 вместе с секцией C |
| F6 | Временные каталоги разработки секции остались в `/tmp`: `/tmp/clo012-*`, `/tmp/clo006`, `/tmp/clo-b-audit` (проверка §8); `rm` отклонён классификатором прав. `/tmp/claude-smoke` к 2026-10-17 уже нет | удалить вручную |
| F7 | `sync_tools.py`: `session_factory(project)` вызывается вне `_as_value_error()`; ошибки резолва проекта уже `ValueError`, но контракт держится на этом, а не на обёртке (AI-ревью #221, жёлтое) | при следующей правке `sync_tools.py` |
| F8 | `epoch` — константа `0050_sync_rank` (B11), `open_conflicts`/`leases` в `sync status` — заглушки (B22) | CLO-032, секции C/D |

## 8. Как попробовать сейчас

Две «машины» — два `COD_DOC_HOME` со своим `config.yaml` и своей БД, общий
каталог как remote. Живой `~/.cod-doc` не трогается. Прогнано 2026-10-17 на
ветке от `e01c7da` (`.venv/bin/cod-doc`).

1. Реплика A и remote:
   ```bash
   B=/tmp/cod-doc-sync-try; mkdir -p $B/remote $B/a/repo $B/b/repo
   A() { COD_DOC_HOME=$B/a cod-doc "$@"; }
   M() { COD_DOC_HOME=$B/b cod-doc "$@"; }
   A project add $B/a/repo --name demo
   A remote add origin file://$B/remote -p demo
   A doc create -p demo --key guide/hello --type guide --status draft \
     --title Hello --author human:dakh
   A sync status -p demo          # не отправлено: 17 операций
   A push -p demo --author human:dakh
   A sync log -p demo
   A sync verify -p demo          # дайджест совпал, exit 0
   ```
2. Реплика B. `clone` нет (F2), поэтому B — свежий проект с uid проекта A,
   пустым журналом и без дефолтных рутин и дерева разделов (они приедут с A).
   Так её заводит и `tests/cli/test_cmd_sync.py::_as_clone_of`:
   ```bash
   UID_A=$(sqlite3 $B/a/repo/.cod-doc/state.db "select uid from project where slug='demo'")
   M project add $B/b/repo --name demo
   sqlite3 $B/b/repo/.cod-doc/state.db "DELETE FROM sync_op; DELETE FROM routine;
     UPDATE doc_node SET parent_id=NULL; DELETE FROM doc_node;
     UPDATE project SET uid='$UID_A' WHERE slug='demo';"
   M remote add origin file://$B/remote -p demo
   M fetch -p demo                # новых changeset'ов 1
   M sync status -p demo --verify # behind 1, дайджест НЕ совпал, подсказка doc drift
   M pull -p demo --dry-run
   M pull -p demo                 # применено changeset'ов 1, операций 17
   M doc list -p demo             # guide/hello
   M sync verify -p demo          # дайджест совпал, exit 0
   ```

Тот же сценарий доступен по MCP на профиле `standard` (`:8801` после
`cod-doc update`): `sync_status(project, verify=True)`, `sync_push(project)`,
`sync_pull(project, dry_run=True)`.

## 9. Итог секции

Секция B — 7/7 по коду в `main`. Следующий шаг — секция C: таблица
`sync_conflict` (CLO-017), LWW по полю (CLO-018), 3-way merge текстов
(CLO-019), склейка натуральных ключей (CLO-023), чужие ревизии (CLO-024).
До CLO-029 вторая реплика заводится только эмуляцией clone из §8.
