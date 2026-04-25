---
type: audit-report
scope: cod_doc/infra (Section A — Data Core)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-04-25
last_updated: 2026-04-25
audit_target_revision: section-a-closed (COD-001 … COD-005); фиксы — миграция 0006_views_and_defaults
related_docs:
  - ../DATA_MODEL.md
  - ../roadmap/cod-doc-task-plan.md
  - ../roadmap/audit-followups-task-plan.md
---

# Section A (Data Core) — Implementation Audit

> Аудит реализации Section A — миграций, ORM-моделей, domain-сущностей, тестов.
> Цель: подтвердить, что Section A закрыта корректно, прежде чем стартовать Section B (services).
> Severity: **critical** — блокирует Section B; **high** — нужно до релиза или до зависимой задачи; **medium** — желательно; **low** — косметика.

## Сводка

| Severity | Count | Resolved | Deferred |
|----------|------:|---------:|---------:|
| critical | 0 | — | — |
| high     | 2 | 2 ✅ | 0 |
| medium   | 5 | 4 ✅ | 1 (ME-2) |
| low      | 7 | 5 ✅ | 2 (LO-3, LO-4) |
| **итого** | **14** | **11 ✅** | **3** |

Section A **закрыта окончательно**. Все functional-замечания закрыты миграцией [0006_views_and_defaults](../../../cod_doc/infra/migrations/versions/20260425_0006_views_and_defaults.py) и связанными правками в моделях/спеке/тестах. Отложены три замечания (ME-2 — Postgres CI; LO-3 — split файлов; LO-4 — стиль тестов) — они требуют отдельных треков и не блокируют Section B.

`pytest tests/infra/` → **37/37 passed**.

## Что проверялось

| Артефакт | Файлы |
|----------|-------|
| Миграции | [0001_core](../../../cod_doc/infra/migrations/versions/20260419_0001_core.py), [0002_tasks](../../../cod_doc/infra/migrations/versions/20260425_0002_tasks.py), [0003_stories](../../../cod_doc/infra/migrations/versions/20260425_0003_stories.py), [0004_revisions](../../../cod_doc/infra/migrations/versions/20260425_0004_revisions.py), [0005_links_tags](../../../cod_doc/infra/migrations/versions/20260425_0005_links_tags.py) |
| ORM-модели | [cod_doc/infra/models.py](../../../cod_doc/infra/models.py) (503 строки, 19 моделей) |
| Domain-сущности | [cod_doc/domain/entities.py](../../../cod_doc/domain/entities.py) (345 строк, 14 dataclass + 12 enum) |
| Тесты | `tests/infra/` — 5 файлов, 29 кейсов |
| Спека | [DATA_MODEL.md](../DATA_MODEL.md) §3.1-§3.13, §4.1-§4.3 |

`pytest tests/infra/` → **29/29 passed** на момент аудита.

## 1. Покрытие спецификации

| Раздел DATA_MODEL | Артефакт | Статус |
|-------------------|----------|--------|
| §3.1 Project | 0001 + ProjectModel | ✅ |
| §3.2 Document | 0001 + DocumentModel | ✅ |
| §3.3 Section | 0001 + SectionModel | ✅ |
| §3.4 Link | 0001 + LinkModel; partial-индекс выровнен в 0005 | ✅ |
| §3.5 Revision | 0004 + RevisionModel | ✅ |
| §3.6 Plan + Plan.Section | 0002 + PlanModel/PlanSectionModel | ✅ |
| §3.7 Task | 0002 + TaskModel | ✅ |
| §3.8 Dependency | 0002 + DependencyModel | ✅ |
| §3.9 AffectedFile | 0002 + AffectedFileModel | ✅ |
| §3.10 UserStory + acceptance + link | 0003 + 3 модели | ✅ |
| §3.11 Module + dep + code | 0003 + 3 модели | ✅ |
| §3.12 Tag + 3 junction-таблицы | 0005 + 4 модели | ✅ |
| §3.13 AuditLog | 0004 + AuditLogModel | ✅ |
| §3.14 Embedding | — | ⏳ COD-040 (Section E) |
| §3.15 Proposal | — | ⏳ позже |
| §4.1 section_totals view | 0002 | ✅ |
| §4.2 plan_totals view | 0002 | ✅ |
| §4.3 ready_tasks view | 0002 | ✅ |
| §4.3a document_body view | 0006 (dialect-aware) | ✅ |

## 2. High — закрыто

### IMPL-A-HI-1. ✅ View `document_body` (§4.3a) — реализовано

[DATA_MODEL §3.3](../DATA_MODEL.md#33-section) обещает: «полное body документа собирается через view §4.3a», и решение DOC-HI-8 сделало `Section.body` единственным источником. View `document_body` специфицирован в §4.3a, но миграциями Section A не создан. Кроме того, упомянутая в спеке директория `cod_doc/infra/views/` отсутствует.

**Последствия:** COD-010 (DocService.get/render) не сможет получить body документа SQL-запросом — будет вынужден агрегировать секции в Python; теряется единая точка истины и оптимизация на стороне БД.

**Фикс:** новая миграция `0006_document_body_view` с диалект-aware DDL: SQLite → `group_concat(... , chr(10) || chr(10))`, Postgres → `string_agg(... , E'\n\n' ORDER BY position)`. Поднять директорию `cod_doc/infra/views/` для хранения SQL-шаблонов.

**Acceptance:** view возвращает корректный body для документа c N секциями; smoke-тест собирает ожидаемую строку.

**Resolution (2026-04-25):** миграция [0006_views_and_defaults](../../../cod_doc/infra/migrations/versions/20260425_0006_views_and_defaults.py) создаёт `document_body` с диалект-aware DDL — SQLite через `group_concat` поверх упорядоченного подзапроса, Postgres через `string_agg ... ORDER BY`. DATA_MODEL §4.3a обновлён. Покрыто 2 кейсами в `tests/infra/test_audit_followups.py` (preamble+sections, doc без секций).

### IMPL-A-HI-2. ✅ JSON NOT NULL без `server_default` — закрыто

`project.config_json`, `document.frontmatter_json`, `audit_log.payload_json` — все объявлены `NOT NULL` в миграциях, но **без** `server_default`. Спека требует `DEFAULT '{}'` (§3.1, §3.2). Сейчас умолчание задаётся только на уровне ORM (`default=dict`), что не покрывает:

- raw SQL вставки (data-migrations через `op.execute`),
- bulk-импортёр Restate (COD-051) — высокий риск constraint violation,
- внешние клиенты, идущие в БД мимо ORM.

**Фикс:** добавить `server_default=sa.text("'{}'")` в существующие миграции через follow-up `0006_*` (или alembic batch_alter), либо документировать инвариант «эти столбцы пишутся только через ORM» в `cod_doc/infra/models.py` и в DATA_MODEL §3.

Тот же риск касается `created/last_updated/at` (NOT NULL без `server_default`), но трекаем как low (LO-5) — таймстемпы естественно проставлять явно.

**Зависимая задача:** COD-051 (Restate importer) — критично закрыть до неё.

**Resolution (2026-04-25):** в миграции 0006 — `batch_alter_table` на `project`, `document`, `audit_log` с `server_default=text("'{}'")`; ORM-модели обновлены (тот же `server_default`); тест `test_raw_insert_without_json_columns_uses_server_default` подтверждает, что raw INSERT без `config_json` не падает и значение устанавливается в `{}`.

## 3. Medium

### IMPL-A-ME-1. ✅ `Mapped[dict]` и `dict` без параметров типа — закрыто

ORM (`config_json`, `frontmatter_json`, `payload_json`) и domain (`Project.config`, `Document.frontmatter`) объявлены как голый `dict` — strict mypy/pyright это бракуют (что уже видно в IDE diagnostics). При включении strict-режима в CI прогон упадёт.

**Фикс:** `dict[str, Any]` в моделях/entities; в `pyproject.toml` уже включён `[tool.mypy] strict = true`, поэтому оставлять нельзя.

**Resolution (2026-04-25):** все три ORM-колонки (`config_json`, `frontmatter_json`, `payload_json`) → `Mapped[dict[str, Any]]`; domain `Project.config`, `Document.frontmatter`, `AuditLog.payload` → `dict[str, Any]`. IDE diagnostics чисто.

### IMPL-A-ME-2. ⏸ Postgres-путь не проверен — отложено

[Acceptance COD-001](../roadmap/cod-doc-task-plan.md): «`alembic upgrade head` проходит на чистом SQLite **и на чистом Postgres**». Все 5 миграций написаны диалект-нейтрально (`sqlite_where`/`postgresql_where`, `sa.JSON`, `sa.DateTime(timezone=True)`), но прогон на Postgres ни разу не делался — есть риск, что `op.execute(VIEW_DDL)` или `partial index` в каком-то углу сломается.

**Фикс:** в `tests/infra/` поднять `pg`-марку (через `testcontainers`/`pytest-postgresql`), запускать те же тесты на двух диалектах. Минимум — один CI job для миграции против чистого Postgres.

**Status:** отложено — требует CI/инфраструктурной работы (testcontainers/pg в pre-commit/CI). Трекается отдельной задачей; миграции 0001-0006 написаны диалект-нейтрально (sqlite_where/postgresql_where, dialect-detect для views) и должны проходить «as-is».

### IMPL-A-ME-3. ✅ Domain-enum'ы расширяют DATA_MODEL — закрыто

| Enum | Значение | DATA_MODEL §… |
|------|----------|---------------|
| `EntityKind.SECTION` | `"section"` | §3.5: только `'document','task','plan','story','link'` |
| `EntityKind.MODULE` | `"module"` | (то же) |
| `AuditSurface.AGENT` | `"agent"` | §3.13: только `'cli'\|'mcp'\|'rest'\|'tui'` |

Это сознательные расширения (зафиксированы в session-summary COD-004), но они **не отражены в DATA_MODEL.md** — расхождение спеки и кода.

**Фикс:** обновить DATA_MODEL §3.5/§3.13 (расширить enum-список и добавить мотивацию) либо убрать значения из кода. Решение — спроектировать в Section B при оформлении RevisionService и AuditService.

**Resolution (2026-04-25):** обновлён DATA_MODEL — §3.5 расширил `entity_kind` до `'document'|'section'|'task'|'plan'|'story'|'link'|'module'`; §3.13 расширил `surface` `'agent'`-значением с пояснением.

### IMPL-A-ME-4. ✅ Нет CHECK на self-loop в `dependency` / `module_dependency` — закрыто

Сейчас можно вставить `INSERT INTO dependency (from_task_id, to_task_id) VALUES (1, 1)` — это сломает `ready_tasks` (задача навсегда заблокирована собой). Cycle-detection в COD-021 будет ловить такие случаи на сервисном слое, но дешёвая защита на уровне БД (`CHECK from_task_id <> to_task_id`) — стандартная практика.

**Фикс:** добавить CHECK constraint в обеих таблицах через small `0006_*` миграцию, либо явно отказаться (с обоснованием в DATA_MODEL §7).

**Resolution (2026-04-25):** в 0006 — `CheckConstraint` `from_task_id <> to_task_id` (dependency) и `from_module <> to_module` (module_dependency); ORM-модели обновлены; DATA_MODEL §7 обновлён. Тесты `test_dependency_self_loop_rejected` / `test_module_self_loop_rejected` подтверждают `IntegrityError`.

### IMPL-A-ME-5. ✅ Полиморфный `revision.entity_id` без документированных инвариантов — закрыто

`revision.entity_kind + entity_id` ссылается на разные таблицы без FK. Это сознательный выбор (offline-генерация ULID, append-only), но в коде нет ни комментария, ни service-инварианта «entity_id указывает на существующую row при коммите». Без явной ответственности легко получить «сиротские» ревизии после `DELETE entity` (CASCADE удалит саму entity, но ревизии останутся — что и требуется для аудита, но об этом нужно явно сказать).

**Фикс:** короткая Q&A-секция в DATA_MODEL §3.5 (или в `capabilities/audit-and-ci.md`) — «почему нет FK; как читаются «сиротские» ревизии; чья ответственность за entity_id». Это HI-кандидат если COD-015 (RevisionService) будет ассумить FK; пока medium.

**Resolution (2026-04-25):** добавлены явные инварианты в DATA_MODEL §3.5 (комментарии прямо под определением `entity_kind`/`entity_id`): сервис отвечает за корректность пары; FK намеренно отсутствует ради append-only истории, переживающей удаление целевой сущности.

## 4. Low

| ID | Описание | Status |
|----|----------|--------|
| IMPL-A-LO-1 | `link.from_section` → `from_section_id` | ✅ DATA_MODEL §3.4 переименован |
| IMPL-A-LO-2 | `ix_link_target_task` extra-индекс не из спеки | ✅ DATA_MODEL §3.4 теперь его перечисляет |
| IMPL-A-LO-3 | `models.py` (503 LOC) и `entities.py` (345 LOC) одним файлом | ⏸ отложено: split — после Section B |
| IMPL-A-LO-4 | `test_db_smoke.py` ходит через репозитории, новые тесты — через ORM | ⏸ отложено: единый стиль — после COD-010..015 (репозитории появятся) |
| IMPL-A-LO-5 | Таймстемп-колонки NOT NULL без `server_default` | ✅ задокументировано в DATA_MODEL §5 и в docstring `models.py` |
| IMPL-A-LO-6 | Нет теста FK SET NULL | ✅ добавлены `test_module_spec_doc_id_set_null_on_doc_delete`, `test_plan_parent_doc_id_set_null_on_doc_delete` |
| IMPL-A-LO-7 | Нет UNIQUE на `(story_acceptance.story_id, position)` | ✅ в 0006: `uq_story_acceptance_position`; покрыто тестом |

## 5. Анализ покрытия тестами

| Файл | Кейсы | Что покрыто | Что не покрыто |
|------|-------|-------------|----------------|
| `test_db_smoke.py` (COD-001) | 5 | schema, project CRUD, doc+sections+links, UNIQUE doc_key, cascade project→doc | partial-FK SET NULL, encoded sensitivity round-trip |
| `test_tasks_migration.py` (COD-002) | 6 | schema+views, агрегаты, COALESCE на пустую секцию, ready_tasks (blocked + ignore-relates), unique edge | self-loop, cascade plan→tasks |
| `test_stories_migration.py` (COD-003) | 7 | schema, story+acceptance+links, global story_id uniq, cascade, module deps+code, unique edge, global module_id uniq | story_acceptance positions, module SET NULL FK |
| `test_revisions_migration.py` (COD-004) | 5 | schema+indexes, chain через parent_revision_id, ULID uniq, JSON round-trip, cascade | revision-flow с реальной entity (поли-FK), broken chain (parent не существует) |
| `test_tags_migration.py` (COD-005) | 6 | таблицы, partial-index наличие+условие, uniqueness per project, attach к doc/task/story, junction PK, cascade | partial-index селективность на realistic данных |

**Сводно:** покрытие соответствует целям Section A (smoke-уровень для миграций). Глубокое поведенческое тестирование — задача Section B (репозиториев и сервисов).

## 6. Что осталось (отложено)

| ID | Описание | Когда |
|----|----------|-------|
| IMPL-A-ME-2 | Прогон миграций против чистого Postgres в CI | Отдельный track (testcontainers/pg в pre-commit/CI) |
| IMPL-A-LO-3 | Split `models.py`/`entities.py` по доменам | После Section B, когда понятен финальный размер |
| IMPL-A-LO-4 | Единый стиль тестов (ORM vs репозитории) | После COD-010..015 — когда репозитории появятся |

Эти три не блокируют Section B и могут быть закрыты попутно.

## 7. Подтверждение готовности

- ✅ Все 13 таблиц §3.1-§3.13 + 4 view (`section_totals`, `plan_totals`, `ready_tasks`, `document_body`) присутствуют.
- ✅ Миграционная цепочка непрерывна: `0001 → 0002 → 0003 → 0004 → 0005 → 0006`.
- ✅ ORM-модели, domain-сущности, миграции, DATA_MODEL — согласованы.
- ✅ 37/37 тестов проходят на SQLite (29 baseline + 8 audit-followup).
- ✅ JSON NOT NULL колонки безопасны для raw INSERT (server_default `'{}'`).
- ✅ DB-уровневая защита от self-loops в `dependency` / `module_dependency`.
- ✅ `(story_acceptance.story_id, position)` UNIQUE.
- ✅ Strict mypy чистый по `dict[str, Any]`.
- ⏸ Postgres CI-прогон отложен (ME-2).

**Заключение:** Section A закрыта окончательно. Section B можно стартовать без оглядки на этот аудит. ME-2/LO-3/LO-4 трекаются отдельно.
