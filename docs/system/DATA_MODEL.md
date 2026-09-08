---
type: data-model
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-25
---

# COD-DOC — Data Model

> Нормализованная схема хранения. Markdown — это проекция этой схемы.
> Диалект SQLite указан как базовый; для Postgres — см. комментарии.

## 1. Обзор сущностей

```text
Project ─┬─< Document ─┬─< Section ─┬─< Block
         │             │            └─< Link (out)
         │             └─< Revision
         │
         ├─< Plan ─┬─< Plan.Section ─┬─< Task ─┬─< Task.Revision
         │        │                  │        ├─< Dependency (out)
         │        │                  │        └─< AffectedFile
         │        │                  │
         │        │                  └─< SectionTotals (view)
         │        │
         │        └─< NextBatchCache
         │
         ├─< UserStory ─┬─< StoryLink (to Document / Task / Module)
         │              └─< StoryAcceptance
         │
         ├─< Module ─┬─< ModuleDependency (module→module)
         │           └─< ModuleCode (paths)
         │
         ├─< Tag ──< DocumentTag, TaskTag, StoryTag
         │
         └─< ActivityEvent (всё, что прошло через write-path) ──< AgentRun (run_id)
```

## 2. Ключевые инварианты

- Все ID внешних сущностей (таск, модуль) — человекочитаемые (`AUTH-025`, `M1-auth`). БД хранит ещё и суррогатный `row_id` BIGINT PK.
- `Revision` — иммутабельная история; никогда не апдейтится, только append.
- `Link` — direct reference `(from_doc, to_ref)`; резолв в `to_doc_id` кэшируется, но всегда перепроверяется при чтении.
- `Task.status` — enum из 3 значений; `Plan.status` — вычисляем, не хранится.
- `tasks_done` / `tasks_total` секции — хранятся в `SectionTotals` как материализованное представление с триггером на изменение задач.

## 3. Таблицы

### 3.1 `Project`

```sql
CREATE TABLE project (
  row_id       INTEGER PRIMARY KEY,
  slug         TEXT    NOT NULL UNIQUE,   -- 'restate', 'weather-cli'
  title        TEXT    NOT NULL,
  root_path    TEXT    NOT NULL,
  created      TEXT    NOT NULL,
  updated      TEXT    NOT NULL,
  config_json  TEXT    NOT NULL DEFAULT '{}'
);
```

### 3.2 `Document`

`Document` хранит **только метаданные и frontmatter**. Body не дублируется здесь — он составляется из `section.body` (см. §3.3) через view §4.

```sql
CREATE TABLE document (
  row_id           INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES project(row_id),
  doc_key          TEXT    NOT NULL,             -- 'modules/M1-auth/overview'
  path             TEXT    NOT NULL,             -- relative projection path
  type             TEXT    NOT NULL,             -- 'module-spec','execution-plan',...
  status           TEXT    NOT NULL,             -- frontmatter.md §Status
  source_of_truth  INTEGER NOT NULL DEFAULT 1,   -- boolean
  sensitivity      TEXT    NOT NULL DEFAULT 'internal',  -- public|internal|confidential|restricted
  owner            TEXT,
  title            TEXT    NOT NULL,
  preamble         TEXT    NOT NULL DEFAULT '',  -- текст до первого H2 (короткое описание/intro)
  frontmatter_json TEXT    NOT NULL DEFAULT '{}',
  frontmatter_raw  TEXT,                         -- ADO-010: YAML-блок как в файле; '' = файл был без frontmatter, NULL = документ заведён в БД ЛИБО строка создана до миграции 0025 (ADO-022)
  title_in_body    INTEGER,                      -- ADO-010: был ли в источнике '# H1' (NULL = неизвестно → H1 рендерится; см. ADO-022)
  projection_hash  TEXT,                         -- hash последнего export
  created          TEXT    NOT NULL,
  last_updated     TEXT    NOT NULL,
  last_reviewed    TEXT,
  UNIQUE(project_id, doc_key)
);
CREATE INDEX ix_document_type ON document(type, status);
CREATE INDEX ix_document_sensitivity ON document(sensitivity);
```

> **ADO-022.** У пары `frontmatter_raw` / `title_in_body` два разных источника
> NULL, и различить их по самой строке нельзя: документ, заведённый в БД
> (`doc create`), и документ, импортированный до миграции
> `0025_projection_fidelity`. Для первого NULL — правда, для второго — потеря
> данных: рендер пере-сериализует frontmatter из `frontmatter_json` и дописывает
> `# H1`, которого в источнике не было. Поэтому `doc export` отказывается
> переписывать такой файл, а лечится это `cod-doc doc backfill-projection`
> (MCP: `doc_backfill_projection`) — восстановлением ровно этих двух колонок с
> диска, без отката метаданных, которые меняли в БД.

### 3.3 `Section`

Секции документа — **канонический носитель body**. Тонкие правки, локальные ревизии, embeddings, ссылки — всё привязано к секции, не к документу.

```sql
CREATE TABLE section (
  row_id       INTEGER PRIMARY KEY,
  document_id  INTEGER NOT NULL REFERENCES document(row_id),
  anchor       TEXT    NOT NULL,  -- 'data-model'
  heading      TEXT    NOT NULL,
  level        INTEGER NOT NULL,  -- 1..6
  position     INTEGER NOT NULL,  -- порядок внутри документа
  body         TEXT    NOT NULL,  -- canonical body этой секции
  content_hash TEXT    NOT NULL,  -- sha256(body) — для invalidate embedding и detect drift
  UNIQUE(document_id, anchor)
);
CREATE INDEX ix_section_position ON section(document_id, position);
```

> **Решение DOC-HI-8:** `Document.body` упразднён; `Section.body` — единственный источник. Полное body документа собирается через view `document_body` (§4.4).

### 3.4 `Link`

Исходящие ссылки, выделенные из body секции.

```sql
CREATE TABLE link (
  row_id           INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES project(row_id),
  from_section_id  INTEGER NOT NULL REFERENCES section(row_id),
  raw              TEXT    NOT NULL,  -- как написано: '[[M1 AUTH v2]]' или '../M1 AUTH v2.md'
  kind             TEXT    NOT NULL,  -- 'wiki','markdown','url','task','story'
  to_doc_key       TEXT,
  to_task_id       TEXT,
  to_story_id      TEXT,
  resolved         INTEGER NOT NULL DEFAULT 0,
  last_checked     TEXT,
  broken_reason    TEXT
);
CREATE INDEX ix_link_target_doc  ON link(to_doc_key);
CREATE INDEX ix_link_target_task ON link(to_task_id);
CREATE INDEX ix_link_broken      ON link(resolved) WHERE resolved = 0;
```

### 3.5 `Revision`

Универсальная иммутабельная история.

```sql
CREATE TABLE revision (
  row_id       INTEGER PRIMARY KEY,
  revision_id  TEXT    NOT NULL UNIQUE,    -- ULID, 26 символов: '01HQX5Z9F0K8R...'
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  entity_kind  TEXT    NOT NULL,   -- см. enum ниже
  entity_id    INTEGER NOT NULL,   -- row_id соответствующей сущности (полиморфно, без FK)
  parent_revision_id TEXT,         -- предыдущая revision той же сущности; NULL если первая
  author       TEXT    NOT NULL,   -- 'agent:task-steward','human:dakh','mcp:claude'
  at           TEXT    NOT NULL,   -- ISO-8601; должен соответствовать timestamp в revision_id
  diff         TEXT    NOT NULL,   -- unified diff либо JSON-patch
  reason       TEXT,
  commit_sha   TEXT,               -- если привязано к git-коммиту
  run_id       TEXT                -- телеметрия оркестратора; де-факто всегда NULL, см. §3.13.1
);
CREATE INDEX ix_revision_entity ON revision(entity_kind, entity_id, at);
CREATE INDEX ix_revision_parent ON revision(parent_revision_id);
```

**`entity_kind` ∈** `'document' | 'section' | 'task' | 'plan' | 'story' | 'link' | 'module'`. Сервис, инициирующий ревизию, отвечает за корректность `entity_kind + entity_id`.

**Полиморфный `entity_id` без FK.** Намеренно: append-only история должна переживать удаление целевой сущности (audit-инвариант). Каскад от родительской таблицы НЕ затрагивает revision; «сиротские» ревизии — норма и читаются по `entity_kind + entity_id` за время жизни проекта.

**`revision_id` = ULID** ([Crockford-base32, 128 бит, lexicographically sortable](https://github.com/ulid/spec)).
Первые 48 бит — timestamp с миллисекундной точностью; оставшиеся 80 — random.

Зачем ULID, а не AUTOINCREMENT INTEGER:
- сортируется по времени без отдельного `at`-индекса;
- генерируется на клиенте без round-trip в БД (важно для propose-flow и offline-сессий);
- безопасно мерджится из нескольких реплик (нет конфликта sequence-counter).

`parent_revision_id` обеспечивает optimistic concurrency control: write-path в transaction'е делает `WHERE revision_id = (SELECT MAX(revision_id) FROM revision WHERE entity ...)`, и если кто-то ещё успел записать раньше — конфликт.

### 3.6 `Plan` и `Plan.Section`

```sql
CREATE TABLE plan (
  row_id          INTEGER PRIMARY KEY,
  project_id      INTEGER NOT NULL REFERENCES project(row_id),
  scope           TEXT    NOT NULL UNIQUE,  -- 'M1-auth-module','infra-cors'
  principle       TEXT,                     -- 'test-first','fix-first'
  module_id       TEXT,                     -- 'M1-auth'
  parent_doc_id   INTEGER REFERENCES document(row_id),
  completed_log_id INTEGER REFERENCES document(row_id),
  created         TEXT NOT NULL,
  last_updated    TEXT NOT NULL
);

CREATE TABLE plan_section (
  row_id      INTEGER PRIMARY KEY,
  plan_id     INTEGER NOT NULL REFERENCES plan(row_id),
  letter      TEXT    NOT NULL,   -- 'A','B','C',...
  title       TEXT    NOT NULL,
  slug        TEXT    NOT NULL,   -- 'A-Test-Coverage'
  position    INTEGER NOT NULL,
  doc_id      INTEGER REFERENCES document(row_id),  -- section file (split format)
  UNIQUE(plan_id, letter)
);
```

### 3.7 `Task`

```sql
CREATE TABLE task (
  row_id        INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(row_id),
  task_id       TEXT    NOT NULL UNIQUE,      -- 'AUTH-025'
  plan_id       INTEGER NOT NULL REFERENCES plan(row_id),
  section_id    INTEGER NOT NULL REFERENCES plan_section(row_id),
  title         TEXT    NOT NULL,
  status        TEXT    NOT NULL,   -- pending|in-progress|done
  type          TEXT    NOT NULL,   -- feature|test|bug|refactor|...
  priority      TEXT    NOT NULL,   -- critical|high|medium|low
  description   TEXT,
  acceptance    TEXT,
  created       TEXT NOT NULL,
  last_updated  TEXT NOT NULL,
  completed_at  TEXT,
  completed_commit TEXT
);
CREATE INDEX ix_task_status ON task(status, priority);
CREATE INDEX ix_task_plan ON task(plan_id, section_id);
```

### 3.8 `Dependency`

```sql
CREATE TABLE dependency (
  row_id       INTEGER PRIMARY KEY,
  from_task_id INTEGER NOT NULL REFERENCES task(row_id),
  to_task_id   INTEGER NOT NULL REFERENCES task(row_id),
  kind         TEXT    NOT NULL DEFAULT 'blocks',   -- blocks|relates|duplicates
  note         TEXT,
  UNIQUE(from_task_id, to_task_id, kind)
);
-- Cycle detection: в сервисе, на каждый insert.
```

### 3.9 `AffectedFile`

```sql
CREATE TABLE affected_file (
  row_id    INTEGER PRIMARY KEY,
  task_id   INTEGER NOT NULL REFERENCES task(row_id),
  path      TEXT    NOT NULL,
  kind      TEXT    NOT NULL DEFAULT 'source',   -- source|test|migration|config
  UNIQUE(task_id, path)
);
CREATE INDEX ix_affected_path ON affected_file(path);
```

Используется для N:1 / N:M diff-based sync (как в Restate task-plan.md §4.6).

### 3.10 `UserStory`

```sql
CREATE TABLE user_story (
  row_id       INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  story_id     TEXT    NOT NULL UNIQUE,    -- 'US-014'
  persona      TEXT    NOT NULL,           -- 'Agency Owner','Platform Admin'
  narrative    TEXT    NOT NULL,           -- 'As X, I want Y, so Z'
  status       TEXT    NOT NULL,           -- draft|accepted|delivered|deferred
  priority     TEXT    NOT NULL,
  created      TEXT NOT NULL,
  last_updated TEXT NOT NULL
);

CREATE TABLE story_acceptance (
  row_id    INTEGER PRIMARY KEY,
  story_id  INTEGER NOT NULL REFERENCES user_story(row_id),
  position  INTEGER NOT NULL,
  criterion TEXT    NOT NULL,
  met       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE story_link (
  row_id       INTEGER PRIMARY KEY,
  story_id     INTEGER NOT NULL REFERENCES user_story(row_id),
  to_kind      TEXT    NOT NULL,       -- 'task'|'document'|'module'
  to_ref       TEXT    NOT NULL,       -- 'AUTH-025' | 'modules/M1-auth' | 'M1-auth'
  relation     TEXT    NOT NULL        -- 'implemented_by'|'specified_in'|'owned_by'
);
```

### 3.11 `Module`

```sql
CREATE TABLE module (
  row_id       INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  module_id    TEXT    NOT NULL UNIQUE,    -- 'M1-auth'
  name         TEXT    NOT NULL,
  status       TEXT    NOT NULL,
  spec_doc_id  INTEGER REFERENCES document(row_id),
  plan_id      INTEGER REFERENCES plan(row_id)
);

CREATE TABLE module_dependency (
  row_id      INTEGER PRIMARY KEY,
  from_module INTEGER NOT NULL REFERENCES module(row_id),
  to_module   INTEGER NOT NULL REFERENCES module(row_id),
  reason      TEXT,
  UNIQUE(from_module, to_module)
);

CREATE TABLE module_code (
  row_id     INTEGER PRIMARY KEY,
  module_id  INTEGER NOT NULL REFERENCES module(row_id),
  kind       TEXT    NOT NULL,   -- 'backend'|'tests'|'migrations'|'admin_panel'
  path       TEXT    NOT NULL
);
```

### 3.12 `Tag`

```sql
CREATE TABLE tag (
  row_id     INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(row_id),
  name       TEXT    NOT NULL,
  UNIQUE(project_id, name)
);
CREATE TABLE document_tag (document_id INTEGER, tag_id INTEGER, PRIMARY KEY(document_id, tag_id));
CREATE TABLE task_tag     (task_id     INTEGER, tag_id INTEGER, PRIMARY KEY(task_id, tag_id));
CREATE TABLE story_tag    (story_id    INTEGER, tag_id INTEGER, PRIMARY KEY(story_id, tag_id));
```

### 3.13 `ActivityEvent` — журнал write-операций

Единый audit-таймлайн проекта: одна строка на каждую мутацию, пишется
в той же транзакции, что и сама мутация (ADO-040), через
`activity_service.write_revision_and_emit_event` / `emit_for_write`.

```sql
CREATE TABLE activity_event (
  id         TEXT    PRIMARY KEY,         -- UUIDv7, time-sortable
  project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  ts         TEXT    NOT NULL,
  actor_kind TEXT    NOT NULL,            -- см. ActorKind ниже
  actor_id   TEXT,                        -- строка-автор: 'human:dakh', 'agent:claude-opus-5'
  run_id     TEXT,                        -- телеметрия оркестратора, см. §3.13.1
  kind       TEXT    NOT NULL,            -- 'task.status_changed', 'doc.updated', …
  scope_kind TEXT,                        -- 'task' | 'doc' | 'story' | 'approval' | 'run'
  scope_id   TEXT,
  payload    TEXT    NOT NULL DEFAULT '{}',
  summary    TEXT
);
```

**`actor_kind`** — enum `domain.entities.ActorKind`:
`human | agent | orchestrator | routine | system | cli | api`.
Выводится из строки-автора **только** через
`domain.entities.actor_kind_for_author()` — единственную точку вывода
(ADR-012). Канонический формат `actor_id` / `author` — `<kind>:<id>`
(`human:dakh`, `agent:claude-opus-5`, `routine:doc_drift_daily`);
оркестраторный прогон исторически пишется через дефис
(`orchestrator-run-<name>`), резолвер понимает оба написания.
`cli` и `api` — поверхностные акторы: проставляются явно на своих
call-site'ах и из строки не выводятся.

> **Строки до 2026-09-06 неточны.** До ADR-012 эвристика была
> продублирована в одиннадцати местах в трёх вариантах, поэтому в истории
> есть события `orchestrator-run-*` с `actor_kind='human'` и события
> `agent:*` с `actor_kind='human'`. Бэкфилла нет намеренно: журнал
> наблюдений не переписывается задним числом.

#### 3.13.1 `AgentRun` и колонка `run_id` — телеметрия, не контракт

`agent_run` — одна строка на вызов `Orchestrator.run_task` встроенного
раннера. Колонка `run_id` есть в семи таблицах (`revision`,
`activity_event`, `approval`, `agent_run`, `routine_run`, `finding`,
плюс исторически `audit_log`) и заполняется **только** внутри
`run_scope` / `start_orchestrator_run`.

**Мутации через MCP, CLI и REST run-скоуп не открывают, поэтому у них
`run_id IS NULL`.** Замер на живой БД cod-doc 2026-09-06: `revision`
2166/2166 NULL, `activity_event` 1114/1114 NULL, `agent_run` — одна
строка от 2026-06-06.

Это зафиксированное решение (ADR-012), а не незакрытый долг: правило
«run-id на всех мутациях» снято из `AGENTS.md` §5.4, MCP-тулы
`run_list` / `run_revert` / `activity_for_run` удалены. Колонки
оставлены nullable — удалять их значило бы переписать append-only
`revision` в каждой существующей БД ради нулевой выгоды. Не пиши код,
который рассчитывает на непустой `run_id`.

> **`audit_log` удалена** (миграция `0029_drop_audit_log`, 2026-09-06).
> Таблица была объявлена журналом write-операций в ARCHITECTURE §9 и в
> этом параграфе, но за всю историю проекта не получила ни одного
> writer'а — 0 строк. Потребность закрывает `activity_event` выше.

### 3.14 `Embedding`

Векторные представления секций для semantic-search (см. [capabilities/context-retrieval.md §5](capabilities/context-retrieval.md)).

```sql
CREATE TABLE embedding (
  row_id        INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(row_id),
  section_id    INTEGER NOT NULL REFERENCES section(row_id) ON DELETE CASCADE,
  model         TEXT    NOT NULL,         -- 'openai:text-embedding-3-small','bge-small-en-v1.5'
  dim           INTEGER NOT NULL,         -- 1536 / 384 / ...
  vector        BLOB    NOT NULL,         -- packed float32; pgvector использует своё
  content_hash  TEXT    NOT NULL,         -- = section.content_hash на момент генерации
  generated_at  TEXT    NOT NULL,
  UNIQUE(section_id, model)
);
CREATE INDEX ix_embedding_section ON embedding(section_id);
CREATE INDEX ix_embedding_stale ON embedding(content_hash);  -- быстрый join к section для invalidate
```

Lifecycle:

- При commit revision на section → `EmbeddingService.enqueue(section_id)`.
- Воркер вычисляет вектор, пишет / апдейтит row.
- При смене `section.content_hash` запись считается stale и регенерируется.
- При `DELETE section` — каскадно удаляется (CASCADE).

Чанкование: одна секция — один embedding row; если body > N токенов (default 1024) — секция считается «too large», поднимается warning `EMB-001` в audit, рекомендуется split.

Pg-профиль использует `pgvector` тип `vector(<dim>)` вместо `BLOB` и индекс `ivfflat`/`hnsw`.

### 3.15 `Proposal`

Pending-edit, ожидающий approve/reject (см. [capabilities/doc-evolution.md §5](capabilities/doc-evolution.md)).

```sql
CREATE TABLE proposal (
  row_id         INTEGER PRIMARY KEY,
  proposal_id    TEXT    NOT NULL UNIQUE,    -- ULID
  project_id     INTEGER NOT NULL REFERENCES project(row_id),
  target_kind    TEXT    NOT NULL,           -- 'document'|'section'|'task'|'story'
  target_id      INTEGER NOT NULL,           -- row_id целевой сущности
  author         TEXT    NOT NULL,           -- agent:... / mcp:... / human:...
  patch          TEXT    NOT NULL,           -- unified diff | json-patch
  reason         TEXT,
  status         TEXT    NOT NULL,           -- pending|approved|rejected|withdrawn
  created        TEXT    NOT NULL,
  decided_at     TEXT,
  decided_by     TEXT,
  resulting_revision_id TEXT                 -- ULID при status=approved
);
CREATE INDEX ix_proposal_pending ON proposal(project_id, status) WHERE status='pending';
CREATE INDEX ix_proposal_target ON proposal(target_kind, target_id);
```

Lifecycle:

- `propose_edit` → row с `status=pending`, ULID `proposal_id`.
- `approve` → применяет patch через соответствующий сервис, пишет revision, проставляет `resulting_revision_id`, `status=approved`.
- `reject` → `status=rejected`, без revision.
- `withdraw` (автором или по таймауту) → `status=withdrawn`.

Auto-approve для агентов с `auto_approve: true` ([agents-and-skills.md §1.1](capabilities/agents-and-skills.md)) — пропускает создание proposal-row, идёт прямо в revision.

## 4. Вычисляемые представления

### 4.1 `section_totals`

```sql
CREATE VIEW section_totals AS
SELECT
  s.row_id           AS section_id,
  COUNT(t.row_id)    AS tasks_total,
  SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS tasks_done,
  SUM(CASE WHEN t.status='in-progress' THEN 1 ELSE 0 END) AS tasks_in_progress
FROM plan_section s
LEFT JOIN task t ON t.section_id = s.row_id
GROUP BY s.row_id;
```

### 4.2 `plan_totals`

Аналогично — агрегат по плану. Используется при генерации Progress Overview.

### 4.3a `document_body`

```sql
CREATE VIEW document_body AS
SELECT
  document_id,
  preamble
    || CASE WHEN preamble <> '' AND sec <> '' THEN E'\n\n' ELSE '' END
    || sec AS body
FROM (
  SELECT
    d.row_id AS document_id,
    d.preamble AS preamble,
    COALESCE((
      SELECT string_agg(
        repeat('#', s.level) || ' ' || s.heading || E'\n\n' || s.body,
        E'\n\n'
        ORDER BY s.position
      )
      FROM section s
      WHERE s.document_id = d.row_id
    ), '') AS sec
  FROM document d
) AS t;
```

> Реализовано в `cod_doc/infra/migrations/versions/20260908_0030_document_body_pushdown.py`: SQLite-вариант использует `group_concat(... , char(10) || char(10))` поверх упорядоченного подзапроса (`SELECT ... WHERE document_id = d.row_id ORDER BY position`); Postgres — `string_agg(... , E'\n\n' ORDER BY position)`. Оба варианта возвращают идентичный текст.

> **ADO-010 (находка F7).** До миграции 0025 view склеивал `preamble` с первым заголовком без разделителя — `preamble` хранится без хвостового перевода строки, поэтому на выходе получалось `> …заранее.## 1. Зачем`. Это была порча контента, а не форматирование: любой `doc export` ломал документ. Разделитель `\n\n` вставляется только когда обе части непусты; агрегат секций вычисляется один раз во внутреннем `SELECT`, чтобы условие могло его проверить, не повторяя `string_agg`.

> **Производительность (миграция 0030).** Форма 0025 собирала секции в производной таблице с `GROUP BY document_id` и джойнила её к `document`. SQLite не проталкивает внешний `WHERE document_id = ?` внутрь такой группировки: он материализует агрегат по **всей** таблице `section` и лишь потом берёт одну строку, поэтому чтение одного документа стоило O(все секции проекта), а обход всех документов — O(документы × секции). На корпусе cod-doc (150 документов, 1200 секций) 150 одиночных чтений занимали 250–450 мс; на этом стояла страница `GET /p/{slug}`, которая гоняет `detect_project_drift` по всем документам. Коррелированный подзапрос даёт `SEARCH section USING INDEX ix_section_position (document_id=?)` — те же 150 чтений занимают 6 мс, чтение всех строк разом не пострадало (8.4 → 5.1 мс). Текст на выходе побайтово тот же — это обязательное условие, от него считается `document.projection_hash`.

### 4.3 `ready_tasks`

```sql
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status='pending'
  AND NOT EXISTS (
    SELECT 1 FROM dependency d
    JOIN task dep ON dep.row_id = d.to_task_id
    WHERE d.from_task_id = t.row_id
      AND dep.status <> 'done'
  );
```

## 5. Миграции и seed

- Миграции — Alembic (`cod_doc/infra/migrations/`), нумерация `0001_*`, `0002_*`.
- Seed добавляет только системные теги и enum-валидации.
- Для импорта Restate — отдельный one-shot скрипт (см. [migration/from-restate.md](migration/from-restate.md)).
- **JSON `NOT NULL`-колонки** (`project.config_json`, `document.frontmatter_json`) — `server_default '{}'`. Безопасны для raw INSERT и bulk-импорта.
- **Таймстемп-колонки** (`created`, `last_updated`, `at`) — `NOT NULL` без `server_default`. Заполняются на стороне приложения (`_utcnow` в ORM); raw SQL должен передавать значения явно. Это компромисс: единый источник истины — Python-часовой пояс, без рассинхрона с серверным `current_timestamp` между диалектами.

## 6. Именование и id-формат

| Сущность | Human ID | Правило |
|----------|----------|---------|
| Module   | `M<N>-<slug>` | `M1-auth`, `M10-agencies` |
| Task     | `<PREFIX>-<NNN>` | `AUTH-025`; ранжирование по секциям как в Restate |
| Story    | `US-<NNN>` | globally unique |
| Plan     | `<MODULE-ID>-<kebab>` | `M1-auth-module` |
| Document | `<path-without-ext>` | `modules/M1-auth/overview` |

Все правила валидируются сервисами — см. [standards/task-plan.md](standards/task-plan.md).

## 7. Целостность

Триггеры и сервисные правила:

1. **Insert Dependency** → run cycle-check, ошибка если появится цикл (BD-уровень: `CHECK from_task_id <> to_task_id` отсекает self-loops; полный cycle-check — в сервисе).
2. **Update Task.status → done** → ensure все `depends_on` уже `done`, иначе error.
3. **Delete Document** запрещён, пока на него есть живые `Link` (force-flag только в сервисе с audit).
4. **Update Document.body** → триггерит пересчёт ссылок и (асинхронно) эмбеддингов.
5. **Insert ModuleDependency** — на уровне БД `CHECK from_module <> to_module`; cycle-check — в сервисе.

## 8. Почему не NoSQL / plain markdown

- NoSQL не даёт ACID-транзакций на «создал задачу → пересчитал секцию → записал revision → обновил ссылки».
- Plain markdown = Restate сегодня = уже знаем, что деградирует.
- Реляционка даёт чистый recursive CTE для графа зависимостей и критического пути без внешнего графового движка.
