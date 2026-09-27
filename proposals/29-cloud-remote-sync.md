# 29 — cod-doc remote: облачное хранение и доступ по модели git

> Категория: 🔵 Архитектура · Риск: высокий · Зависимости: proposal 09 (activity), proposal 22 (hub), proposal 23 (cloud plane), proposal 26 (write-путь через сервисы)

> **Статус: черновик (2026-09-27).** Только проектирование: плана и задач в БД нет.
> Решения вынесены в ADR-016 (команда через реплики и remote) и ADR-017
> (глобальная идентичность и захват операций), оба `proposed`. Целевой контракт —
> capability [remote-sync](../docs/system/capabilities/remote-sync.md), сценарии
> SCN-081…089 — [scenarios/remote-sync](../docs/system/scenarios/remote-sync.md).
> Следующий шаг после приёмки ADR — истории, `plan_create` на scope
> `cloud-remote-2026-10` и разбиение по секциям A–F из §7.

## 1. Контекст

Состояние проекта — документы, задачи, планы, истории, ADR, ревизии — живёт в одной
файловой SQLite `<project>/.cod-doc/state.db`, и файл этот в `.gitignore`
(`.gitignore:13`). Отсюда три боли:

1. **Нет второй машины.** Ноутбук и десктоп видят разные БД; перенести состояние
   можно только копированием файла, и последняя копия молча затирает другую.
2. **Нет команды.** Второй человек или облачный агент не получает к проекту доступа
   вообще, кроме как через туннель к чужому демону.
3. **Нет бэкапа.** `cod-doc backup|restore` описан в
   [capabilities/backup-and-export.md](../docs/system/capabilities/backup-and-export.md),
   но не реализован; потеря диска = потеря всей истории задач.

RFC 23 решал 2 и 3 центральным Postgres: одна истина в облаке, клиенты работают
онлайн. Он не начат и упирается в пачку Postgres-блокеров STO-018…024. Этот RFC
берёт другую модель — **git**: у каждой машины полная локальная реплика, работа
офлайн, обмен через remote командами `clone` / `pull` / `push`. Remote бывает двух
видов с одним протоколом: «глупый» (S3/MinIO-бакет, без сервера) и «умный»
(`cod-doc serve` с токенами и ролями участников проекта).

**Почему не «просто git по state.db».** SQLite — бинарный файл; git не сливает его,
а выбирает одну сторону целиком. Коммитить дамп SQL не лучше: построчный diff дампа
конфликтует на каждом автоинкременте. Нужна синхронизация на уровне сущностей, а
git — источник модели (DAG коммитов, fast-forward push, объекты по хэшу), а не
транспорт.

## 2. Текущее состояние (проверено по коду 2026-09-27)

| Что | Где | Почему мешает синхронизации |
|---|---|---|
| PK — локальный `row_id INTEGER`, FK — тоже `row_id` | все `cod_doc/infra/models/*.py` | один и тот же `row_id` на двух машинах — разные сущности; переносить FK нельзя |
| Человеческие ID выдаются как `max+1` | `task_service.py:183` `_next_task_id`, `adr_service.py:88` `_next_adr_id`, `scenario_service/_internals.py:33`, `story_service/crud.py:157` `next_story_id` | две офлайн-реплики гарантированно выдадут один и тот же `ADO-229` разным задачам |
| `revision.diff` — unified diff, не состояние | `infra/models/revisions.py:42` | по журналу ревизий сущность не восстановить; для сущностей без ревизий (dependency, tag) журнала нет вовсе |
| Глобальные ID уже есть точечно | `revision.revision_id` ULID (`revisions.py:33`), `activity_event.id` UUID, `finding.finding_uid` uuid7 | журнальные таблицы уже переносимы без перенумерации |
| Единая точка write-пути | `activity_service.py:174` `write_revision_and_emit_event`, `:144` `emit_for_write` (ADO-040) | место есть, но оно видит событие, а не полный набор изменённых полей |
| Presentation не пишет в ORM | гейт `tests/services/test_presentation_no_orm_writes.py` (ADO-208) | все записи идут через сервисы → через `Session` → их можно перехватить в одном месте |
| Bulk-запись мимо unit-of-work | `import_service.py:906`, `:919` (`update(DocumentModel)`), `structure_service.py:182`, `repo_index_service.py:206`, `link_service/semantic.py:311` | `before_flush` такие записи не видит; все найденные бьют в derived/local-поля (§3.2), но гарантии нет |
| Резолв БД | `infra/db.py:85` `resolve_db_url`, `:121` `db_url_for_entry`, `:251` `db_for_entry` | реплика остаётся обычной SQLite, резолв менять не нужно |
| Hub | RFC 22 §3.1, `~/.cod-doc/hub.db`, `UNIQUE(project_id, task_id)` с миграции 0027 | единица синхронизации — проект, hub на это не влияет |
| Identity / auth | ARCHITECTURE §12.2 (`actor` — только DDL-спека, «гейт неактивен, ADO-217»), `api/v1/__init__.py` (Bearer — только docstring) | в API и MCP нет аутентификации вообще; `author` — свободная строка (ADR-012) |
| Бэкап / экспорт | только проекции (`doc/plan/adr/scenario export`) | снапшота БД нет |
| Решения о хранении | ADR-005 (PostgreSQL, accepted), ADR-010 (два профиля: embedded SQLite и server/cloud на PostgreSQL, accepted 2026-09-06; работы — секция G `adoption-2026-08`, STO-*) | командный профиль сегодня **определён** как общая PostgreSQL; этот RFC предлагает другой путь к команде и требует нового ADR (§4) |
| VISION | §6 п. 5: цикл выполним удалённым ИИ-клиентом «против облачного узла COD-DOC» | цель та же, узел здесь — реплика с умным remote, а не общая БД |

## 3. Предложение

### 3.1 Словарь

| git | cod-doc remote |
|---|---|
| репозиторий | проект (`project.uid`) |
| рабочая копия + `.git` | локальная реплика `state.db` |
| объект | changeset или снапшот, адрес — sha256 содержимого |
| коммит | **changeset**: пачка операций над сущностями + родители |
| ветка `main` | `refs/main` проекта на remote; других веток в v1 нет |
| `git clone` / `pull` / `push` | `cod-doc clone` / `pull` / `push` |
| merge conflict | запись `sync_conflict`, видна в `curator_next` |

### 3.2 Классы данных

Реестр синхронизации (`cod_doc/infra/sync_registry.py`) относит каждую таблицу, а
при необходимости и колонку, ровно к одному классу. Таблица без записи в реестре
роняет тест — новая таблица не может молча выпасть из синхронизации.

| Класс | Что уезжает | Таблицы |
|---|---|---|
| `authored` | операции над полями | project, document, section, doc_node, doc_comment, plan, plan_section, task, dependency, affected_file, task_document, adr (+ supersedes/task/diagram), user_story (+ acceptance/link/section), scenario (+ step/link), module (+ dependency/code), tag (+ document_tag/task_tag/story_tag), external_ref, commit_link, approval (+ task/doc_revision link), routine (определение), structure_waiver, finding с `source != "routine"` |
| `journal` | append-only, без слияния | revision, activity_event кроме `link.*` и `routine.fired` |
| `derived` | ничего; пересобирается после pull | link, link_suggestion, doc_node_suggestion, `db_search_idx*` (FTS5), code_*, structure_* кроме structure_waiver, doc_code_claim, repo_file, repo_symbol, repo_import, finding с `source = "routine"` |
| `local` | никогда | routine_run, agent_run, trace_call, task_metrics, finding_source_run, sidecar `.cod-doc/runs/*.jsonl` (ADR-015); колонки `document.projection_hash`, `document.content_sha256_head` — они описывают локальное рабочее дерево |

Markdown-проекции не синхронизируются вовсе: их везёт git репозитория. После pull
`doc drift` покажет расхождение, если БД ушла вперёд от закоммиченного файла, —
ровно та же семантика, что сегодня после правки через MCP.

Routine-находки — `derived`: каждая реплика пересчитывает их сама, а партиция
автозакрытия (`finding_service.reconcile_partition`) остаётся локальной. Иначе две
реплики закрывали бы находки друг друга по несвежей картине.

### 3.3 Глобальная идентичность

Каждая `authored`-таблица получает колонку `uid TEXT NOT NULL` (uuid7) с уникальным
индексом `(uid)`. `row_id` остаётся локальным PK — ни один запрос и ни один FK внутри
реплики не меняется. В операциях FK сериализуются через uid:
`{"plan_section": "0192…"}` вместо `plan_section_id: 17`; при применении
транслируются обратно в локальный `row_id`.

**Человеческие ID** (`ADO-229`, `ADR-016`, `US-008`, `SCN-094`) выдаются так:

1. **Аренда блока.** На pull/push реплика берёт у remote блок номеров на префикс
   (по умолчанию 20). Аренда — объект `leases/<prefix>` на remote, обновляемый тем
   же CAS, что и `refs/main`, поэтому работает и на глупом remote. Сервис выдачи
   (`_next_task_id` и соседи) сначала берёт номер из локального остатка аренды.
2. **Провизорный ID** — когда аренды нет или она исчерпана офлайн:
   `ADO-~k3f2` (суффикс — 4 символа base32 от uid). Провизорный ID рабочий: на него
   можно ссылаться, делать checkout, писать в коммит.
3. **Закрепление** — на push провизорный ID получает номер из новой аренды. Старое
   имя пишется в `entity_alias(project_id, kind, alias, uid)`; `task_get` и CLI
   резолвят алиас с пометкой «переименовано в ADO-241».

Коллизия двух постоянных номеров невозможна по построению: блоки не пересекаются.

### 3.4 Журнал операций

```sql
CREATE TABLE sync_op (
  op_id        TEXT PRIMARY KEY,          -- ULID
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  replica_id   TEXT NOT NULL,             -- replica.uid автора операции
  hlc          TEXT NOT NULL,             -- hybrid logical clock, сортируемая строка
  entity_kind  TEXT NOT NULL,             -- 'task', 'section', …
  entity_uid   TEXT NOT NULL,
  kind         TEXT NOT NULL,             -- 'upsert' | 'delete'
  fields       TEXT NOT NULL,             -- JSON {field: new_value} только изменённые
  base         TEXT NOT NULL,             -- JSON {field: value_before} — для 3-way merge
  author       TEXT NOT NULL,             -- как в revision.author (ADR-012)
  changeset    TEXT                       -- NULL = ещё не упакована (локальный хвост)
);
CREATE INDEX ix_sync_op_pending ON sync_op(project_id) WHERE changeset IS NULL;

CREATE TABLE replica (
  uid        TEXT PRIMARY KEY,            -- uuid7, генерится при init/clone
  project_id INTEGER NOT NULL REFERENCES project(row_id),
  is_self    BOOLEAN NOT NULL DEFAULT 0,
  label      TEXT                         -- 'dakh@macbook', 'hermes-server'
);
```

**Захват** — хук unit-of-work в `infra/` (`event.listen(Session, "before_flush")`):
проходит `session.new/dirty/deleted`, берёт по реестру §3.2 только `authored`-модели
и колонки, из `inspect(obj).attrs[*].history` собирает `fields` и `base`. Правка
каждого сервиса не нужна; вся запись и так идёт через `Session` (гейт ADO-208).

**Обход хука** — bulk `session.execute(update|insert|delete(Model))`. Новый AST-гейт
`tests/infra/test_sync_capture_no_bulk_authored.py` запрещает bulk-запись в
`authored`-таблицы и колонки; найденные в §2 места бьют только в `derived`/`local`
и проходят гейт как есть.

**Время** — HLC (физическое время + счётчик + `replica_id` для тай-брейка). Стенные
часы разных машин расходятся; HLC даёт полный порядок, согласованный с причинностью.

### 3.5 Changeset и remote

Changeset — неизменяемый объект:

```json
{
  "v": 1,
  "project": "<project.uid>",
  "parents": ["<sha256>", "..."],
  "replica": "<replica.uid>",
  "actor": "dakh",
  "schema_head": "0041_sync_identity",
  "hlc_max": "…",
  "ops": [ { "op_id": "…", "entity_kind": "task", "entity_uid": "…", "kind": "upsert",
             "fields": {"status": "done"}, "base": {"status": "in_progress"},
             "author": "claude-opus", "hlc": "…" } ]
}
```

Адрес — `sha256` канонического JSON (сортированные ключи, без пробелов), сжатие
zstd. Первые 4 байта объекта — версия формата: место под будущее шифрование (§5).

Раскладка на remote (одинаковая для S3 и для диска умного сервера):

```
<project_uid>/
  refs/main              # sha256 головы; меняется только CAS
  leases/<PREFIX>        # {"next": 261, "holders": {...}}; меняется только CAS
  objects/ab/cdef….zst   # changeset'ы
  snapshots/<sha>.db.zst # SQLite authored+journal на момент changeset <sha>
  meta.json              # {"schema_min": "0041…", "created": …}
```

**Транспорт** — один интерфейс, три реализации:

```python
class RemoteStore(Protocol):
    def get_ref(self, project_uid: str, name: str) -> RefValue | None: ...
    def cas_ref(self, project_uid: str, name: str,
                expected: RefValue | None, new: bytes) -> bool: ...
    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]: ...
    def put_object(self, project_uid: str, sha: str, data: bytes) -> None: ...
    def get_object(self, project_uid: str, sha: str) -> bytes: ...
    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None: ...
```

| Схема URL | Реализация | CAS | Доступ |
|---|---|---|---|
| `file:///path` | `FileRemote` | `os.link` + rename в каталоге ref | права ФС; для тестов и NAS |
| `s3://bucket/prefix` | `S3Remote` (boto3) | conditional PUT `If-Match: <ETag>` / `If-None-Match: *` (есть в S3 и MinIO) | S3-креды, всё или ничего |
| `https://host` | `HttpRemote` → `/api/sync/v1/projects/{uid}/…` на `cod-doc serve` | на сервере, транзакцией | Bearer-токен + роль (§3.8) |

Для локальной обвязки подходит уже поднятый MinIO на Hermes (`localhost:9000` через
туннель); отдельной инфраструктуры RFC не требует.

### 3.6 Протокол

```mermaid
sequenceDiagram
    participant L as Локальная реплика
    participant R as Remote
    Note over L: push
    L->>L: упаковать sync_op WHERE changeset IS NULL → changeset C (parents = локальная голова)
    L->>R: has_objects([C…]) → put_object(отсутствующие)
    L->>R: cas_ref(refs/main, expected = известная голова, new = C)
    alt CAS прошёл
        R-->>L: ok — fast-forward
    else remote ушёл вперёд
        R-->>L: rejected
        L->>L: pull, затем push заново
    end
    Note over L: pull
    L->>R: get_ref(refs/main) → H
    L->>R: get_object(…) по родителям от H до общего предка
    L->>L: применить чужие ops, слить с локальным хвостом (§3.7)
    L->>L: merge-changeset M (parents = [локальная голова, H]); пересобрать derived
```

- **push** — только fast-forward. Отказ CAS = «remote ушёл вперёд», клиент сам
  делает pull и повторяет (`cod-doc push` делает это до 3 раз, дальше отдаёт ошибку).
- **pull** = fetch + merge. Merge-changeset несёт только операции, разрешившие
  расхождение; если локального хвоста нет — fast-forward без merge-changeset.
- **clone** — последний снапшот + changeset'ы после него. Снапшот пишет тот, кто
  делает push, если с прошлого снапшота накопилось больше N операций (по умолчанию
  5000), — иначе clone проекта со 100k операций replay'ил бы их все.
- **Схема.** Реплика не применяет changeset с `schema_head` новее своей головы
  миграций: `pull` отвечает «remote на схеме 0043, у вас 0041 — `cod-doc update`».
  Push с более старой схемы, чем `meta.schema_min`, отвергается тем же сообщением.
- **Пересборка derived** после pull: `link_sync` по затронутым секциям, FTS по
  затронутым документам, routine-находки — при следующем прогоне рутины.

### 3.7 Слияние

Слияние детерминировано: две реплики, применившие одно множество changeset'ов в
любом порядке, приходят к одному состоянию. Проверка — `cod-doc sync verify`:
sha256 от канонической выгрузки `authored`-таблиц по `uid`, сравнение с дайджестом
в последнем changeset'е.

| Что | Правило | Конфликт |
|---|---|---|
| скалярное поле (title, priority, order, plan_section) | LWW по HLC **на уровне поля**: правки разных полей одной задачи не конфликтуют | нет |
| текст (section.body, task.description, task.acceptance, task_document.body, adr.decision, user_story.narrative) | 3-way merge построчно от `base`; непересекающиеся ханки сливаются | пересекающиеся ханки → `sync_conflict`, в БД остаётся «наше», «их» лежит в конфликте |
| `task.status` | если изменили обе стороны — побеждает больший HLC, но только при допустимом переходе из значения другой стороны по `task_status_machine.ALLOWED_TRANSITIONS` | недопустимая пара или двойной офлайн-`task_checkout` → `sync_conflict` |
| множества (dependency, *_tag, story_link, adr_task, affected_file) | add-wins: добавление одной стороны и удаление другой → элемент остаётся | нет |
| delete против edit | delete выигрывает (tombstone по uid) | `sync_conflict` с последним состоянием «их» — восстановимо |
| человеческий ID | не сливается: коллизий нет по §3.3 | — |
| journal (revision, activity_event) | объединение по глобальному ID | нет |

Конфликт — строка таблицы, а не маркеры в тексте:

```sql
CREATE TABLE sync_conflict (
  uid          TEXT PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  entity_kind  TEXT NOT NULL,
  entity_uid   TEXT NOT NULL,
  field        TEXT,                 -- NULL для delete/edit
  ours         TEXT, theirs TEXT, base TEXT,
  theirs_op    TEXT NOT NULL,        -- op_id чужой операции
  status       TEXT NOT NULL,        -- 'open' | 'resolved'
  resolved_by  TEXT, resolved_at DATETIME
);
```

Разрешение (`sync_resolve --take ours|theirs|<текст>`) — обычная запись через
сервис: она порождает новую операцию, ревизию и activity event (ADO-040) и уезжает
при следующем push. `curator_next` показывает открытые конфликты одним пунктом
очереди с готовой командой — ниже битых ссылок, выше неразобранного Инбокса.

### 3.8 Доступ (умный remote)

Активирует спеку ARCHITECTURE §12.2, но с поправкой: actor — сущность **сервера**,
а не проекта, и членство вынесено отдельно.

```sql
CREATE TABLE actor (
  uid        TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,          -- 'human' | 'agent'
  handle     TEXT NOT NULL UNIQUE,   -- 'dakh', 'dakh/claude-code'
  owner_uid  TEXT REFERENCES actor(uid),  -- агент принадлежит человеку
  created    DATETIME NOT NULL,
  disabled   BOOLEAN NOT NULL DEFAULT 0
);
CREATE TABLE api_token (
  uid        TEXT PRIMARY KEY,
  actor_uid  TEXT NOT NULL REFERENCES actor(uid),
  label      TEXT NOT NULL,          -- 'macbook', 'ci', 'cursor-cloud'
  token_hash TEXT NOT NULL UNIQUE,   -- sha256; сам токен показывается один раз
  created    DATETIME NOT NULL, last_used DATETIME, revoked_at DATETIME
);
CREATE TABLE project_member (
  project_uid TEXT NOT NULL,
  actor_uid   TEXT NOT NULL REFERENCES actor(uid),
  role        TEXT NOT NULL,          -- 'reader' | 'writer' | 'admin'
  PRIMARY KEY (project_uid, actor_uid)
);
```

| Роль | fetch / clone | push | аренда ID | участники, токены, `meta.schema_min` |
|---|---|---|---|---|
| reader | да | нет | нет | нет |
| writer | да | да | да | нет |
| admin | да | да | да | да |

Агентский actor наследует не больше роли своего владельца и может быть сужен до
reader.

- **Проверка на push.** Сервер отвергает changeset, в котором `actor` не совпадает с
  владельцем токена, а `author` операции не равен ни handle actor'а, ни handle
  его агентов. Провенанс перестаёт быть свободной строкой именно там, где пишут
  несколько людей; локально ADR-012 работает как раньше.
- **Токены** хранятся у клиента в `~/.cod-doc/credentials` (0600) или в Keychain
  (`cod-doc remote login` выбирает Keychain на macOS). Сравнение на сервере — по
  sha256, constant-time.
- **Тот же Bearer-гейт** закрывает `/api/v1` (обещанный в `api/v1/__init__.py`) и
  streamable-http MCP. Это CAP-020/022 из RFC 23, сделанные один раз.
- **Глупый remote** не знает участников: доступ равен S3-кредам, `author` не
  проверяется. Годится для одного владельца на нескольких машинах или для группы,
  где все доверяют всем; `cod-doc remote add s3://…` пишет об этом предупреждение.

### 3.9 Поверхности

Правило четырёх равных поверхностей соблюдается; гейты паритета
(`_surface_parity.py`) расширяются на `sync_service`.

| CLI | MCP (standard / full) | Что делает |
|---|---|---|
| `cod-doc remote add <name> <url>` / `list` / `remove` | — | запись в `~/.cod-doc/config.yaml` у проекта: `remotes: {origin: s3://…}` |
| `cod-doc remote login <name>` | — | сохранить токен |
| `cod-doc clone <url> [--project <slug>] [<dir>]` | — | снапшот + хвост, регистрация в реестре |
| `cod-doc fetch` / `pull` / `push` `-p <slug>` | `sync_pull`, `sync_push` | §3.6 |
| `cod-doc sync status -p <slug>` | `sync_status` | ahead/behind, неупакованные операции, открытые конфликты, аренды |
| `cod-doc sync conflicts` / `resolve <uid> --take …` | `sync_conflicts`, `sync_resolve` | §3.7 |
| `cod-doc sync verify` | `sync_status(verify=True)` | дайджест реплики против remote |
| `cod-doc sync log [-n 20]` | — | changeset'ы: автор, реплика, число операций |
| `cod-doc member add/list/remove`, `cod-doc token create/revoke` | — | только умный remote, роль admin |

Профиль `agent` (6 curator-тулов) не расширяется: куратор узнаёт о конфликтах и
отставании от remote через `curator_next`. Администрирование доступа в MCP не
выставляется намеренно: выдача токена агентом, которому этот токен и нужен, —
эскалация привилегий. Для MCP-тулов из этой таблицы потребуется обновить счётчики
профилей и `PROSE_COUNTERS`.

**Автосинк** — рутина `sync` в существующем движке routines (`pull`, затем `push`
по расписанию проекта, по умолчанию раз в 10 минут, при отсутствии сети — тихо до
следующего прогона).

## 4. Миграция / обратная совместимость

- **Одна ревизия alembic** `0041_sync_identity`: `ADD COLUMN uid` во все
  `authored`-таблицы, бэкфилл uuid7, затем уникальный индекс; новые таблицы
  `replica`, `sync_op`, `changeset`, `sync_ref`, `sync_conflict`, `entity_alias`.
  Таблицы доступа (`actor`, `api_token`, `project_member`) — отдельной ревизией
  секции E, они нужны только серверу.
- **Никакого `batch_alter_table` на `document`**: на SQLite он пересоздаёт таблицу, и
  CASCADE уносит все секции и ссылки (см. CLAUDE.md). Только `ALTER TABLE … ADD
  COLUMN` + `CREATE UNIQUE INDEX`. Тест по образцу
  `test_migration_0035_preserves_data.py` наливает данные на 0040 и гонит upgrade.
- **Проект без remote** работает как сегодня; `sync_op` пишется всегда, чтобы
  первый `remote add` + `push` не терял историю между миграцией и подключением.
  Первый push выгружает снапшот и становится корнем DAG.
- **Hub-режим**: единица синхронизации — проект; в одной `hub.db` часть проектов
  может иметь remote, часть нет.
- **Postgres не нужен.** Блокеры STO-018…024 остаются блокерами RFC 23, но не этого
  RFC: и реплика, и сервер работают на SQLite.
- **Связь с ADR-010.** ADR-010 оставляет PostgreSQL профилем server/cloud и
  требует его исполнимости (CI-job `test-postgres`). Этот RFC профиль не
  отменяет, но снимает с него роль единственного пути к командной работе:
  команда = реплики на SQLite + remote. Решение зафиксировано в ADR-016
  (`proposed`), его следствия для схемы — в ADR-017; supersede ли ADR-016
  ADR-010 целиком или уточняет его (как ADR-010 уточнил ADR-005) — решает
  владелец при приёмке, до начала секции A.
- **Связь с RFC 23.** Его premise «SoT = Postgres в облаке» заменяется на «сервер —
  это такая же реплика с умным remote». Сервер может материализовать проект в
  локальную SQLite и отдавать её web UI и облачным агентам через remote MCP; секции
  B–D RFC 23 (`agent_apply`, токены, проекция) садятся на auth из §3.8. Сам RFC 23
  не отменяется, но его секция A (Postgres SoT) требует пересмотра.
- **Принцип «Не берём: multi-tenant»** из `proposals/README.md` не нарушается:
  команда — это участники одного проекта на одном сервере владельца. Организаций,
  тенантов, регистрации и биллинга нет.

## 5. Риски и что не делаем

**Риски**

| Риск | Смягчение |
|---|---|
| Хук захвата пропускает запись → реплики молча расходятся | AST-гейт bulk-записи (§3.4); `sync verify` в рутине `sync` пишет находку при несовпадении дайджеста |
| Новая таблица забыта в реестре | тест «каждая модель классифицирована» (§3.2) |
| Качество 3-way merge для markdown-текстов | построчный merge + конфликт вместо маркеров; единица слияния — секция, а не документ, поэтому правки разных секций одного документа не пересекаются |
| Рост `sync_op` и объектов | упакованные операции старше последнего снапшота удаляются локально (`sync gc`); на remote объекты старше двух снапшотов — по флагу |
| Расхождение схем между машинами | `schema_head` в changeset + отказ применять новее; доставка по-прежнему `cod-doc update` |
| Утечка токена | отзыв (`token revoke`), `last_used`, токен на устройство, а не на человека |
| Сдвиг часов | HLC, а не стенное время |

**Не делаем в v1**

- Ветки, PR-flow и ревью изменений в БД — один `refs/main` на проект.
- Realtime-совместное редактирование и посимвольные CRDT.
- Multi-tenant, SaaS, регистрацию, организации.
- Синхронизацию `derived`-таблиц и markdown-проекций.
- E2E-шифрование объектов (формат оставляет под него версию, §3.5).
- Partial clone (по плану, по разделу дерева).
- P2P между репликами без remote.
- Подписи changeset'ов (провенанс на умном remote даёт токен).
- Postgres как бэкенд реплики.
- Гранулярные права ниже проекта (на документ / раздел / sensitivity) — это §12.3,
  отдельный RFC.

## 6. Решения

| Развилка | Решение | Почему |
|---|---|---|
| Центральная БД или распределённые реплики | реплики | офлайн-работа и бэкап «бесплатно»; не упирается в Postgres-блокеры |
| Синк состоянием или операциями | операции с `base` | нужен 3-way merge текста и LWW на уровне поля; снапшоты — только ускорение clone |
| Захват в сервисах или в `Session` | `before_flush` | одно место на все сервисы; гейт ADO-208 уже гарантирует, что запись идёт через `Session` |
| Человеческие ID | аренда блоков + провизорный ID | постоянные номера не сталкиваются никогда; офлайн не блокирует работу |
| Один протокол или два | один, три транспорта | S3 закрывает «я и мои машины» без сервера, сервер — команду; тесты гоняются на `file://` |
| Конфликт текста | строка `sync_conflict`, в БД «наше» | маркеры `<<<<` в секции сломали бы проекцию и поиск |

## 7. Оценка

Около 35 задач, 6–8 недель; секции плана `cloud-remote-2026-10` (префикс `CLO`):

| Секция | Содержание | Задач |
|---|---|---|
| A. Идентичность и журнал | миграция `uid`, реестр классов, `sync_op` + хук `before_flush`, HLC, гейты «модель классифицирована» и «нет bulk в authored» | 7 |
| B. Changeset и file-remote | формат и адресация, `RemoteStore`, `FileRemote`, fetch/push/fast-forward, `sync status/log` | 6 |
| C. Слияние и конфликты | LWW по полю, 3-way merge текста, статус-машина, add-wins, tombstones, `sync_conflict`, `sync verify`, пункт в `curator_next` | 7 |
| D. S3 и clone | `S3Remote` с conditional PUT, снапшоты, `clone`, аренда ID и провизорные ID, `entity_alias` | 5 |
| E. Умный remote и доступ | `/api/sync/v1`, `actor`/`api_token`/`project_member`, Bearer-гейт на sync + `/api/v1` + MCP-http, проверка `author`, `member`/`token` CLI | 6 |
| F. Поверхности и документация | MCP-тулы + счётчики профилей, рутина `sync`, zsh-дополнение, ARCHITECTURE §8/§12, capability-док, скилл | 4 |

A → B → C строго последовательны; D и E параллельны после B; F идёт по ходу.

## 8. Критерий успеха

1. Две реплики клонированы с одного S3-remote; обе офлайн правят **одну и ту же**
   задачу (одна — `title`, другая — `status`) и одну секцию (пересекающиеся строки);
   каждая создаёт по новой задаче.
2. Обе делают `pull` + `push`. Итог: правки разных полей слились без конфликта;
   секция дала ровно один `sync_conflict`, видимый в `curator_next`; новые задачи
   получили непересекающиеся постоянные номера, провизорные имена резолвятся через
   алиас.
3. После `sync resolve` и ещё одного цикла `sync verify` на обеих репликах
   показывает один дайджест.
4. На умном remote токен с ролью reader получает 403 на push; changeset с чужим
   `author` отвергается.
