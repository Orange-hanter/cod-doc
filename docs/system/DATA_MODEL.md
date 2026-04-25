---
type: data-model
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
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
         └─< AuditLog (всё, что прошло через write-path)
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

```sql
CREATE TABLE document (
  row_id           INTEGER PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES project(row_id),
  doc_key          TEXT    NOT NULL,             -- 'modules/M1-auth/overview'
  path             TEXT    NOT NULL,             -- relative projection path
  type             TEXT    NOT NULL,             -- 'module-spec','execution-plan',...
  status           TEXT    NOT NULL,             -- frontmatter.md §Status
  source_of_truth  INTEGER NOT NULL DEFAULT 1,   -- boolean
  owner            TEXT,
  title            TEXT    NOT NULL,
  body             TEXT    NOT NULL,             -- canonical markdown body
  frontmatter_json TEXT    NOT NULL DEFAULT '{}',
  projection_hash  TEXT,                         -- hash последнего export
  created          TEXT    NOT NULL,
  last_updated     TEXT    NOT NULL,
  last_reviewed    TEXT,
  UNIQUE(project_id, doc_key)
);
CREATE INDEX ix_document_type ON document(type, status);
```

### 3.3 `Section`

Секции документа — для тонких правок и локальных ревизий.

```sql
CREATE TABLE section (
  row_id       INTEGER PRIMARY KEY,
  document_id  INTEGER NOT NULL REFERENCES document(row_id),
  anchor       TEXT    NOT NULL,  -- 'data-model'
  heading      TEXT    NOT NULL,
  level        INTEGER NOT NULL,  -- 1..6
  position     INTEGER NOT NULL,  -- порядок внутри документа
  body         TEXT    NOT NULL,
  UNIQUE(document_id, anchor)
);
```

### 3.4 `Link`

Исходящие ссылки, выделенные из body секции.

```sql
CREATE TABLE link (
  row_id         INTEGER PRIMARY KEY,
  project_id     INTEGER NOT NULL REFERENCES project(row_id),
  from_section   INTEGER NOT NULL REFERENCES section(row_id),
  raw            TEXT    NOT NULL,  -- как написано: '[[M1 AUTH v2]]' или '../M1 AUTH v2.md'
  kind           TEXT    NOT NULL,  -- 'wiki','markdown','url','task','story'
  to_doc_key     TEXT,
  to_task_id     TEXT,
  to_story_id    TEXT,
  resolved       INTEGER NOT NULL DEFAULT 0,
  last_checked   TEXT,
  broken_reason  TEXT
);
CREATE INDEX ix_link_target ON link(to_doc_key);
CREATE INDEX ix_link_broken ON link(resolved) WHERE resolved = 0;
```

### 3.5 `Revision`

Универсальная иммутабельная история.

```sql
CREATE TABLE revision (
  row_id       INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  entity_kind  TEXT    NOT NULL,   -- 'document','task','plan','story','link'
  entity_id    INTEGER NOT NULL,   -- row_id соответствующей сущности
  author       TEXT    NOT NULL,   -- 'agent:task-steward','human:dakh','mcp:claude'
  at           TEXT    NOT NULL,
  diff         TEXT    NOT NULL,   -- unified diff либо JSON-patch
  reason       TEXT,
  commit_sha   TEXT                -- если привязано к git-коммиту
);
CREATE INDEX ix_revision_entity ON revision(entity_kind, entity_id, at);
```

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

### 3.13 `AuditLog`

```sql
CREATE TABLE audit_log (
  row_id     INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(row_id),
  actor      TEXT    NOT NULL,            -- 'agent:task-steward' / 'human:dakh'
  surface    TEXT    NOT NULL,            -- 'cli'|'mcp'|'rest'|'tui'
  action     TEXT    NOT NULL,            -- 'task.create'|'doc.patch'|...
  payload    TEXT    NOT NULL,            -- JSON аргументов
  result     TEXT    NOT NULL,            -- 'ok'|'error:…'
  at         TEXT    NOT NULL
);
```

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

1. **Insert Dependency** → run cycle-check, ошибка если появится цикл.
2. **Update Task.status → done** → ensure все `depends_on` уже `done`, иначе error.
3. **Delete Document** запрещён, пока на него есть живые `Link` (force-flag только в сервисе с audit).
4. **Update Document.body** → триггерит пересчёт ссылок и (асинхронно) эмбеддингов.

## 8. Почему не NoSQL / plain markdown

- NoSQL не даёт ACID-транзакций на «создал задачу → пересчитал секцию → записал revision → обновил ссылки».
- Plain markdown = Restate сегодня = уже знаем, что деградирует.
- Реляционка даёт чистый recursive CTE для графа зависимостей и критического пути без внешнего графового движка.
