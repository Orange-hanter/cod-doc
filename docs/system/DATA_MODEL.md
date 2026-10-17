---
type: module-spec
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-10-15
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
- `Task.status` — 7 канонических бакетов (`backlog`, `todo`, `in_progress`,
  `in_review`, `blocked`, `done`, `cancelled`) плюс легаси-алиасы
  (`pending`≡`todo`, `in-progress`≡`in_progress`); нормализация и переходы —
  `cod_doc/services/task_status_machine.py`, бэкфилл в канон — миграция 0037.
  `Plan.status` — вычисляем, не хранится.
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

**`entity_kind` ∈** `domain.entities.EntityKind`: `'document' | 'section' | 'task' | 'task_doc' | 'plan' | 'plan_section' | 'story' | 'story_section' | 'doc_node' | 'link' | 'module' | 'adr' | 'scenario'`. У каждой таблицы со своей нумерацией `row_id` — свой kind: пара `(entity_kind, entity_id)` — единственный адрес ревизии, и секция плана `row_id=1` под `'plan'` села бы в историю плана `row_id=1` (`'plan_section'` — ADO-201, RFC 26 §3.1). Сервис, инициирующий ревизию, отвечает за корректность `entity_kind + entity_id`.

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
  scope           TEXT    NOT NULL,         -- 'M1-auth-module','infra-cors'
  principle       TEXT,                     -- 'test-first','fix-first'
  module_id       TEXT,                     -- 'M1-auth'
  parent_doc_id   INTEGER REFERENCES document(row_id),
  completed_log_id INTEGER REFERENCES document(row_id),
  created         TEXT NOT NULL,
  last_updated    TEXT NOT NULL,
  UNIQUE(project_id, scope)                 -- STO-018, миграция 0047: в проекте, не во всей БД
);

CREATE TABLE plan_section (
  row_id      INTEGER PRIMARY KEY,
  plan_id     INTEGER NOT NULL REFERENCES plan(row_id),
  letter      TEXT    NOT NULL,   -- 'A','B','C',...
  title       TEXT    NOT NULL,
  slug        TEXT    NOT NULL,   -- 'A-Test-Coverage'; validate_section_slug
  position    INTEGER NOT NULL,   -- плотный порядок 0..n-1 внутри плана (RFC 26 §3.1)
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
  status        TEXT    NOT NULL,   -- backlog|todo|in_progress|in_review|blocked|done|cancelled (+ легаси pending|in-progress)
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
  note         TEXT,              -- мотивация ребра; write-путь требует её (ADO-202)
  UNIQUE(from_task_id, to_task_id, kind)
);
-- Cycle detection: в сервисе, на каждый insert.
```

Write-путь рёбер — `task_service.add_dependency` / `remove_dependency`
(ADO-202, RFC 26 §3.2): `note` обязателен и хранится обрезанным; повтор с тем
же `note` — no-op без ревизии, с другим — правка `note`. Ребро, замыкающее
цикл, отвергается `DependencyCycleError` до записи. Колонка осталась nullable:
легаси-рёбра без мотивации не заполняются задним числом, их предъявляет
рутина `graph_health` (ADO-205), пока зависимая задача не закрыта.

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
-- ADO-143: реестр продуктовых модулей. Аналог plan_section для планов.
-- `key` — слаг [a-z0-9-]: он уходит в путь роута анализа секции (один сегмент
-- URL) и в id/hx-target htmx-фрагмента, который читает querySelector.
CREATE TABLE story_section (
  row_id     INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  key        TEXT    NOT NULL,             -- 'module-1', 'annex'
  title      TEXT    NOT NULL,             -- «Управление запасами»
  position   INTEGER NOT NULL,
  UNIQUE(project_id, key)
);

CREATE INDEX ix_story_section_project ON story_section(project_id, position);

CREATE TABLE user_story (
  row_id       INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  story_id     TEXT    NOT NULL,           -- 'US-014'
  persona      TEXT    NOT NULL,           -- 'Agency Owner','Platform Admin'
  narrative    TEXT    NOT NULL,           -- 'As X, I want Y, so Z'
  status       TEXT    NOT NULL,           -- draft|accepted|delivered|deferred
  priority     TEXT    NOT NULL,
  created      TEXT NOT NULL,
  last_updated TEXT NOT NULL,
  -- SET NULL, не CASCADE: удаление секции осиротит историю, но не удалит её.
  section_id   INTEGER REFERENCES story_section(row_id) ON DELETE SET NULL,
  UNIQUE(project_id, story_id)             -- STO-018, миграция 0047
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
  module_id    TEXT    NOT NULL,           -- 'M1-auth'
  name         TEXT    NOT NULL,
  status       TEXT    NOT NULL,
  spec_doc_id  INTEGER REFERENCES document(row_id),
  plan_id      INTEGER REFERENCES plan(row_id),
  UNIQUE(project_id, module_id)            -- STO-024, миграция 0047
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

### 3.16 `Scenario`, `ScenarioStep`, `ScenarioLink`

Сценарии тестирования — **авторская половина** [RFC 24 §9](../../proposals/24-structure-contracts-scenarios.md).
Хранят намерение: что должно быть верно. Не хранят доказательства: вердикты
покрытия (`covered | partial | missing | unverifiable`) выводит producer в
ai-reviewer, и они лягут в отдельную append-only `scenario_assessment`
(STR-002) с join по `scenario.row_id`. Эти три таблицы занимают слот
«нормализованного scenario index», отложенный в RFC 24 §12.

```sql
CREATE TABLE scenario (
  row_id           INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  scenario_id      TEXT    NOT NULL,           -- 'SCN-001'
  title            TEXT    NOT NULL,
  kind             TEXT    NOT NULL,           -- RFC 24 §9: happy_path|error_path|
                                               --   boundary_value|invariant|integration
  group_key        TEXT    NOT NULL,           -- имя файла проекции
  document_id      INTEGER REFERENCES document(row_id) ON DELETE SET NULL,
  doc_key          TEXT,                       -- RFC 24 §8 текстовый якорь
  section_anchor   TEXT,
  doc_content_hash TEXT,                       -- section.content_hash на момент авторства
  module_id        INTEGER REFERENCES module(row_id) ON DELETE SET NULL,  -- зарезервировано
  subject_ref      TEXT,                       -- RFC 24 §8; заполнит link-suggest (STR-002)
  status           TEXT    NOT NULL,           -- claim-статус §8: draft|confirmed|retired
  provenance       TEXT    NOT NULL,           -- manual|agent|import
  preconditions    TEXT    NOT NULL,
  expected         TEXT    NOT NULL,
  notes            TEXT,
  position         INTEGER NOT NULL,           -- порядок внутри group_key
  author           TEXT    NOT NULL,
  created          TEXT    NOT NULL,
  last_updated     TEXT    NOT NULL,
  UNIQUE(project_id, scenario_id)
);
CREATE INDEX ix_scenario_project_group ON scenario(project_id, group_key, position);
CREATE INDEX ix_scenario_document_id   ON scenario(document_id);
CREATE INDEX ix_scenario_project_kind  ON scenario(project_id, kind);

CREATE TABLE scenario_step (
  row_id          INTEGER PRIMARY KEY,
  scenario_row_id INTEGER NOT NULL REFERENCES scenario(row_id) ON DELETE CASCADE,
  position        INTEGER NOT NULL,
  text            TEXT    NOT NULL,
  UNIQUE(scenario_row_id, position)
);

CREATE TABLE scenario_link (
  row_id          INTEGER PRIMARY KEY,
  scenario_row_id INTEGER NOT NULL REFERENCES scenario(row_id) ON DELETE CASCADE,
  to_kind         TEXT    NOT NULL,            -- task|story|document|criterion
  to_ref          TEXT    NOT NULL,            -- id задачи / стори / doc_key / 'US-013#2'
  relation        TEXT    NOT NULL,            -- verifies|specified_in|exercised_by
  UNIQUE(scenario_row_id, to_kind, to_ref, relation)
);
```

Замечания, которые не видны из DDL:

- **Якорь — capability-документ, не модуль.** Таблица `module` в живых
  проектах пуста, поэтому `module_id` остаётся nullable и никогда не
  обязателен; группировка идёт по `group_key`, выведенному из `doc_key`.
- **Отсутствующий документ — не ошибка.** Сценарий можно написать до импорта
  capability-документа: `document_id` останется NULL, а совещательная проверка
  сообщит о висящем якоре.
- **`retired` — расширение RFC 24 §8** (`draft`/`confirmed`): id сценариев не
  переиспользуются, снятию нужен собственный статус.
- **Проекция.** `scenario_service.export_group` собирает `Document`
  (`type = scenario-set`) + `Section` на сценарий и отдаёт их
  `projection_service` → `docs/system/scenarios/<group_key>.md`. Ручная правка
  файла ловится теми же гвардами, что и у любой другой проекции.
- **Шаги и связи ревизируются под родительским `entity_kind = 'scenario'`** —
  как критерии под стори.

### 3.17 Таблицы, появившиеся после апреля 2026

> **Сноска ADO-217 (2026-09-26).** Разделы §3 описывают ядро схемы на апрель.
> Подсистемы, добавленные позже, живут в своих моделях
> [`cod_doc/infra/models/`](../../cod_doc/infra/models/) и миграциях — здесь
> только карта, DDL — в миграции:

| Подсистема | Таблицы | Миграция |
|---|---|---|
| Документы задач | `task_document` | `0011_task_documents` |
| Approvals | `approval`, `approval_task_link`, `approval_doc_revision_link` | `0013_approvals` |
| Routines, checkout | `routine`, `routine_run` | `0014_task_checkout_and_routine` |
| ADR | `adr`, `adr_supersedes`, `adr_task`, `adr_diagram` | `0018_adr_tables` |
| Findings | `finding`, `finding_source_run` (+ `miss_streak`, 0036) | `0028_findings` |
| Structure platform | `code_structure_snapshot`, `code_entity`, `code_edge`, `code_boundary`, `code_contract`, `doc_code_claim`, `structure_*` | `0031_structure_platform` |
| Дерево документации | `doc_node`, `doc_node_suggestion`, `document.node_id` | `0035_doc_nodes` |
| Репликация (RFC 29, секция A) | `uid` в authored-таблицах, `rank`/`node_rank`, `replica`, `sync_op`, `changeset`, `sync_ref`, `sync_lease`, `entity_alias` — см. §3.18 | `0049_sync_identity`, `0050_sync_rank` |
| Конфликты слияния (RFC 29, секция C) | `sync_conflict` (local) — см. §3.18 | `0051_sync_conflict` |

Таблицы `agent_definition` и `actor` из capability-доков в схеме **нет** — это
целевые спеки (ARCHITECTURE §12.2, agents-and-skills §1.1).

### 3.18 Репликация: `uid`, `rank` и служебные таблицы sync

> Миграции `0049_sync_identity` и `0050_sync_rank` (2026-10-15, план
> `cloud-remote-2026-10`, секция A; RFC
> [29](../../proposals/29-cloud-remote-sync.md) §3.2–§3.4, §10.1). Класс каждой
> таблицы и колонки (authored / journal / derived / local) задаёт реестр
> [`cod_doc/infra/sync_registry.py`](../../cod_doc/infra/sync_registry.py);
> полноту сверяет `tests/infra/test_sync_registry_complete.py`.

#### 3.18.1 Колонка `uid` — глобальная идентичность строки

```sql
-- во всех 39 authored-таблицах с row_id-PK
-- (sync_registry.UID_TABLES = authored − COMPOSITE_PK_TABLES):
ALTER TABLE <t> ADD COLUMN uid TEXT;          -- бэкфилл, затем:
CREATE UNIQUE INDEX uq_<t>_uid ON <t>(uid);
```

- **Где есть.** Каждая authored-таблица (`project`, `document`, `section`,
  `task`, `plan`, `plan_section`, `doc_node`, `adr*`, `scenario*`,
  `user_story`, `story_*`, `open_question*`, `finding`, `approval*`, `module*`,
  `tag`, `routine`, `external_ref`, `task_document`, `structure_waiver`, рёбра
  ниже). **Нет** у тег-таблиц с составным PK — `document_tag`, `task_tag`,
  `story_tag`: их uid вычисляется при захвате той же формулой, что у рёбер.
  Нет и у journal (`revision`, `activity_event`), derived и local-таблиц.
- **Значение.** Обычная строка — случайный `uuid7`. Ребро (`dependency`,
  `affected_file`, `commit_link`, `adr_relation`, `adr_supersedes`,
  `adr_task`, `module_dependency`, `story_link`, `scenario_link`,
  `open_question_link`) — `uuid5(EDGE_UID_NAMESPACE, json([таблица, *ключ]))`,
  где FK ключа заменены на uid концов (`sync_registry.edge_uid`). uid ребра
  детерминирован относительно uid концов, но не между двумя независимо
  мигрированными БД: концы у них получили разные uuid7.
- **NOT NULL — инвариант кода, не ограничение.** `ADD COLUMN NOT NULL` без
  `DEFAULT` на SQLite невозможен, а пересоздавать `document` нельзя (CASCADE
  унёс бы секции и ссылки). uid новой строке назначает хук захвата в
  `before_flush`; строка, вставленная мимо `Session`, осталась бы без uid —
  такие записи запрещает AST-гейт `tests/infra/test_sync_capture_no_bulk_authored.py`.
- **`row_id` остаётся локальным.** Ссылки между таблицами по-прежнему по
  `row_id`; uid нужен там, где строка покидает БД: в `sync_op.fields` FK
  записан uid конца.

#### 3.18.2 `rank` / `node_rank` — синхронизируемый порядок

```sql
-- 0050_sync_rank, 11 мест порядка (RFC 29 §3.2, Q-009):
ALTER TABLE <t> ADD COLUMN rank TEXT;         -- 10 таблиц
ALTER TABLE document ADD COLUMN node_rank TEXT;  -- порядок документа в разделе дерева
```

| Таблица | Колонка | Родитель | Тай-брейк бэкфилла |
|---|---|---|---|
| `section` | `rank` | `document_id` | `position, row_id` |
| `plan_section` | `rank` | `plan_id` | `position, row_id` |
| `doc_node` | `rank` | `project_id` | `position, node_key, row_id` |
| `scenario` | `rank` | `project_id, group_key` | `position, scenario_id, row_id` |
| `scenario_step` | `rank` | `scenario_row_id` | `position, row_id` |
| `story_section` | `rank` | `project_id` | `position, key, row_id` |
| `story_acceptance` | `rank` | `story_id` | `position, row_id` |
| `adr_diagram` | `rank` | `adr_id` | `position, row_id` |
| `adr_topic` | `rank` | `project_id` | `position, row_id` |
| `open_question_option` | `rank` | `question_row_id` | `position, row_id` |
| `document` | `node_rank` | `node_id` (только `node_id IS NOT NULL`) | `node_position NULLS LAST, doc_key, row_id` |

- **Порядок показа — `(rank, uid, row_id)`** (`cod_doc/infra/ordering.py`).
  Ранг — дробная строка base-36 без заглавных букв (`cod_doc/infra/rank.py`):
  сортировка совпадает в SQLite `BINARY`, Python и PostgreSQL. Вставка между
  соседями пишет один ранг, остальные строки не трогаются.
- **`position` стал local.** Он не уезжает и после слияния реплик может
  повториться. У `section`, `plan_section`, `scenario_step`, `adr_topic` это
  индекс, пересчитываемый из ранга (`ordering.renumber`). У `story_acceptance`
  (`<story_id>#<position>` в ссылках сценариев), `open_question_option` и
  `adr_diagram` это адрес — append-счётчик, не пересчитывается. У `doc_node` и
  `story_section` — advisory-число из CLI/MCP. SQL-представление
  `document_body` (§4.3a) по-прежнему сортирует по `position`: его держат
  согласованным с рангом.
- **Снятые UNIQUE.** `uq_scenario_step_position`,
  `uq_story_acceptance_position`, `uq_adr_diagram_position`,
  `uq_open_question_option_position` заменены обычными индексами
  `ix_<t>_position` на тех же колонках: две строки после слияния законно делят
  позицию до пересчёта.

#### 3.18.3 Служебные таблицы синхронизации

Шесть таблиц — класс `local`: это состояние реплики, а не проекта, они не
уезжают. После секции A пишутся только `replica` и `sync_op`; остальные ждут
секций B–C.

```sql
CREATE TABLE replica (
  uid         TEXT PRIMARY KEY,               -- uuid7
  project_id  INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  is_self     BOOLEAN NOT NULL DEFAULT 0,     -- своя реплика проекта
  label       TEXT,                           -- 'dakh@macbook'
  created     DATETIME NOT NULL
);
CREATE UNIQUE INDEX uq_replica_self ON replica(project_id) WHERE is_self = 1;
-- своя реплика заводится при init_project или первым коммитом с операцией
-- (cod_doc/infra/hlc.py::self_replica)

CREATE TABLE sync_op (                        -- журнал операций над authored-данными
  op_id        TEXT PRIMARY KEY,              -- ULID
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  replica_uid  TEXT NOT NULL,
  hlc          TEXT NOT NULL,                 -- '<ms:013d>.<counter:04d>.<replica_uid>'
  entity_kind  TEXT NOT NULL,                 -- имя таблицы из реестра
  entity_uid   TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('upsert','delete')),
  fields       TEXT NOT NULL,                 -- JSON {колонка: новое}; только SHIPPED_COLUMNS, FK как uid конца
  base         TEXT NOT NULL,                 -- JSON {колонка: прежнее}
  author       TEXT NOT NULL,                 -- '<kind>:<id>' (ADR-012); нет автора — 'system:unknown'
  changeset    TEXT                           -- sha256 или NULL = неупакована
);
CREATE INDEX ix_sync_op_pending ON sync_op(project_id) WHERE changeset IS NULL;
CREATE INDEX ix_sync_op_entity  ON sync_op(entity_kind, entity_uid);

CREATE TABLE changeset (                      -- локальный индекс известных changeset'ов
  sha          TEXT PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  parents      TEXT NOT NULL,                 -- JSON-массив sha
  replica_uid  TEXT NOT NULL,
  actor        TEXT NOT NULL,
  epoch        TEXT NOT NULL,                 -- голова миграций эпохи
  hlc_max      TEXT NOT NULL,
  digest       TEXT NOT NULL,                 -- дайджест после применения
  applied_at   DATETIME                       -- NULL = известен, не применён
);

CREATE TABLE sync_ref (                       -- что реплика знает о remote
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  remote       TEXT NOT NULL,                 -- 'origin'
  name         TEXT NOT NULL,                 -- 'main'
  sha          TEXT,                          -- последняя виденная голова
  etag         TEXT,                          -- для CAS
  fetched_at   DATETIME,
  PRIMARY KEY (project_id, remote, name)
);

CREATE TABLE sync_lease (                     -- арендованный остаток человекочитаемых номеров
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  prefix       TEXT NOT NULL,                 -- 'ADO', 'ADR', 'US', 'SCN', 'Q'
  next_number  INTEGER NOT NULL,
  end_number   INTEGER NOT NULL,              -- включительно
  leased_at    DATETIME NOT NULL,
  PRIMARY KEY (project_id, prefix)
);

CREATE TABLE entity_alias (                   -- провизорный ID или склеенный uid → живой uid
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  entity_kind  TEXT NOT NULL,
  alias        TEXT NOT NULL,
  uid          TEXT NOT NULL,
  reason       TEXT NOT NULL CHECK (reason IN ('pinned','merged')),
  created      DATETIME NOT NULL,
  PRIMARY KEY (project_id, entity_kind, alias)
);

-- 0051_sync_conflict (CLO-017): то, что слияние не свело само; local
CREATE TABLE sync_conflict (
  uid          TEXT PRIMARY KEY,              -- uuid7: порядок по uid = порядок открытия
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  entity_kind  TEXT NOT NULL,
  entity_uid   TEXT NOT NULL,
  field        TEXT,                          -- NULL у delete_edit и склейки ключей
  kind         TEXT NOT NULL CHECK (kind IN ('field','text','status','delete_edit',
                                             'key_merge','double_checkout')),
  ours         TEXT, theirs TEXT, base TEXT,  -- канонический JSON значения (§10.4)
  theirs_op    TEXT NOT NULL,                 -- op_id чужой операции
  status       TEXT NOT NULL CHECK (status IN ('open','resolved')),
  resolved_by  TEXT, resolved_at DATETIME
);
CREATE INDEX ix_sync_conflict_status ON sync_conflict(project_id, status);
```

- **Кто пишет `sync_op`.** Хук `cod_doc/infra/sync_capture.py`, который
  `infra/db.py::make_session_factory` вешает на каждую фабрику сессий; bulk-
  запись, которую хук не видит, обязана звать `sync_capture.record` (так
  пишет `finding_service.ingest_findings`). Операции копятся до
  `before_commit` корневой транзакции, откат SAVEPOINT выбрасывает свои.
  Пишется всегда, даже без настроенного remote: хвост с `changeset IS NULL` —
  то, что уедет `push`.
- **Чего в `fields` нет.** Local-колонок (`position`, `projection_hash`,
  `run_id`, …), `uid` (совпадает с `entity_uid`), колонок, придержанных
  реестром (`withheld_columns`: ссылки на restricted-документ). Строка класса
  local — restricted-документ и его потомки, машинный абсолютный `path`,
  routine-находка — операции не даёт вовсе.
- **Каскад внутри БД** (`ON DELETE CASCADE` без ORM-каскада) операций детей не
  даёт: на другой реплике каскад сработает от удаления родителя.

## 4. Вычисляемые представления

### 4.1 `section_totals`

```sql
CREATE VIEW section_totals AS
SELECT
  s.row_id           AS section_id,
  COUNT(t.row_id)    AS tasks_total,
  SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS tasks_done,
  SUM(CASE WHEN t.status IN ('in_progress','in-progress') THEN 1 ELSE 0 END) AS tasks_in_progress,
  SUM(CASE WHEN t.status='cancelled' THEN 1 ELSE 0 END) AS tasks_cancelled
FROM plan_section s
LEFT JOIN plan p ON p.row_id = s.plan_id
LEFT JOIN task t ON t.section_id = s.row_id AND t.project_id = p.project_id
GROUP BY s.row_id;
```

### 4.2 `plan_totals`

Аналогично — агрегат по плану (`LEFT JOIN task t ON t.plan_id = p.row_id AND
t.project_id = p.project_id`). Используется при генерации Progress Overview.

> **Считаются только задачи проекта плана (миграция 0048, AFT-024).** В общей
> hub-БД задача другого проекта с `plan_id`/`section_id` нашего плана (легаси-данные)
> раньше попадала в счётчики, хотя `audit`/`export`/`chain_layout`/`critical_path`/
> `ready_batch` её уже не видели. Условие стоит в `ON`, а не в `WHERE`: раздел без
> своих задач остаётся строкой с нулями.

> **Статус сравнивается с классом эквивалентности, а не со строкой (миграция 0035).**
> `pending` ≡ `todo` и `in-progress` ≡ `in_progress` — один бакет
> (`domain.entities.TASK_STATUS_ALIASES`), и в живой БД лежат оба написания сразу:
> в БД, не прошедшей 0037, они лежат оба сразу. До 0035 все три view'а сравнивали
> `task.status` точной строкой по легаси-варианту, поэтому задача, взятая в работу
> из `todo`, не попадала в `tasks_in_progress` (Progress Overview показывал её как
> не начатую), а задача в статусе `todo` — в `ready_tasks`. Тот же класс бага ADO-182
> закрыл на уровне репозитория (`equivalent_task_statuses`); до view'ов он тогда не
> дошёл. Литералы в миграции зафиксированы намеренно — миграция не должна меняться
> вместе с картой алиасов; синхронность стережёт
> `tests/infra/test_totals_status_aliases.py`.
>
> **После 0037 в колонке остаётся только канон.** Бэкфилл свёл `pending` → `todo`
> и `in-progress` → `in_progress` во всех БД реестра, а write-путь канонизирует
> на записи (`task_service.update_status`), так что легаси больше не появляется.
> Сравнение по классу эквивалентности при этом остаётся: его читают ещё не
> мигрированные БД и восстановленные бэкапы.
>
> **`tasks_cancelled` — отдельная колонка (миграция 0039, ADO-078).** «Сколько
> осталось» сервисный слой выводит как `total − done − cancelled`, а не
> `total − done`: закрытых состояний два
> (`task_status_machine.TERMINAL_STATUSES`), и отменённая задача работы не
> требует. Считать её в остатке значило вечное «8 из 10» на плане, где взять
> нечего. Колонка именно отдельная, а не слагаемое к `tasks_done`: число
> отменённых должно быть видно, иначе «сделано 10» врёт про план, где сделано
> 8. `backlog` в остатке остаётся — он припаркован, а не закрыт. Колонка
> добавлена последней, порядок прежних полей не сдвинут. Синхронность
> стережёт `tests/infra/test_totals_cancelled.py`.

### 4.3 `ready_tasks`

```sql
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status IN ('todo','pending')
  AND t.blocked_reason IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM dependency d
    JOIN task dep ON dep.row_id = d.to_task_id
    WHERE d.from_task_id = t.row_id
      AND d.kind = 'blocks'
      AND dep.status NOT IN ('done', 'cancelled')
  );
```

`blocked_reason IS NULL` — миграция 0041 (ADO-225): `task_set_blocker` статус
не меняет, а задача с внешним блокером не должна быть готовой. Тот же
критерий у `chain_layout.ready_ids` и, зеркально, у `list_blocked`.

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
| Story    | `US-<NNN>` | уникален в пределах проекта (миграция 0047, STO-018) |
| Plan     | `<MODULE-ID>-<kebab>` | `M1-auth-module` |
| Document | `<path-without-ext>` | `modules/M1-auth/overview` |
| Scenario | `SCN-<NNN>` | `SCN-001`; уникален в пределах проекта, не переиспользуется |

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
