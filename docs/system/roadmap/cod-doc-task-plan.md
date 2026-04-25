---
type: execution-plan
scope: cod-doc-bootstrap
status: pending
principle: test-first
created: 2026-04-19
last_updated: 2026-04-25
source_of_truth:
  vision: docs/system/VISION.md
  architecture: docs/system/ARCHITECTURE.md
  data_model: docs/system/DATA_MODEL.md
---

# COD-DOC — Bootstrap Execution Plan

> Dogfood нового стандарта. План внедрения COD-DOC разбит на секции и задачи согласно [standards/task-plan.md](../standards/task-plan.md).
> Источник истины — БД (после того, как этап A будет готов). До тех пор — этот markdown.

## Navigation

- [System MASTER](../MASTER.md)
- [Vision](../VISION.md)
- [Architecture](../ARCHITECTURE.md)
- [Data Model](../DATA_MODEL.md)

## Progress Overview

| Section | File | Total | Done | Remaining | Status |
|:--------|:-----|------:|-----:|----------:|:-------|
| A: Data Core | inline | 5 | 5 | 0 | ✅ done |
| B: Services | inline | 6 | 2 | 4 | 🔄 in-progress |
| C: Write Paths | inline | 4 | 0 | 4 | ❌ pending |
| D: MCP & CLI | inline | 4 | 0 | 4 | ❌ pending |
| E: Retrieval | inline | 3 | 0 | 3 | ❌ pending |
| F: Migration | inline | 3 | 0 | 3 | ❌ pending |
| **TOTAL**   |        | **25** | **7** | **18** | |

## Gap Analysis Summary

### Уже есть в cod-doc

- Проектный каркас (`cod_doc/core/project.py`), базовая модель Task, wizard, TUI.
- MCP-сервер-заготовка (`cod_doc/mcp/server.py`).
- REST API caркас (`cod_doc/api/`).
- Агент (`cod_doc/agent/orchestrator.py`).
- Templates `MASTER.md.j2`.

### Чего нет

- БД-схема из [DATA_MODEL.md](../DATA_MODEL.md).
- Сервисный слой (Doc/Plan/Task/Link/Story/Revision/Context).
- Валидация формата task-plan.
- Автолинковка, section-парсинг, embeddings.
- CLI/MCP-тулы целевого пакета.
- Импортёр Restate.

## Next Batch

Section A (Data Core) closed. Revision + Doc services landed. Next batch:

- **COD-011** — Test + Implement: TaskService (create/update_status/complete)
- **COD-013** — Test + Implement: LinkService (parse/resolve/verify/rename-cascade)
- **COD-023** — Implement: projection export/import (hash-based detection) — unlocked by COD-010
- **COD-040** — Implement: embeddings pipeline — unlocked by COD-010
- **COD-050** — Test: frontmatter/task-plan parser (property-based)

## Dependency Graph

```mermaid
graph TD
  COD_001[COD-001 migration: core]
  COD_002[COD-002 migration: tasks+plan]
  COD_003[COD-003 migration: stories]
  COD_004[COD-004 migration: revisions]
  COD_005[COD-005 migration: links]

  COD_010[COD-010 DocService]
  COD_011[COD-011 TaskService]
  COD_012[COD-012 PlanService]
  COD_013[COD-013 LinkService]
  COD_014[COD-014 StoryService]
  COD_015[COD-015 RevisionService]

  COD_020[COD-020 write-path validation]
  COD_021[COD-021 cycle detection]
  COD_022[COD-022 completion flow]
  COD_023[COD-023 projection export]

  COD_030[COD-030 CLI: task new]
  COD_031[COD-031 CLI: doc new/patch]
  COD_032[COD-032 MCP: task/doc tools]
  COD_033[COD-033 MCP: context.get]

  COD_040[COD-040 embeddings pipeline]
  COD_041[COD-041 ContextService L0/L1]
  COD_042[COD-042 ContextService L2/L3]

  COD_050[COD-050 frontmatter parser]
  COD_051[COD-051 Restate importer]
  COD_052[COD-052 freeze projection + rollback]

  COD_001 --> COD_002
  COD_002 --> COD_003
  COD_003 --> COD_004
  COD_004 --> COD_005

  COD_001 --> COD_010
  COD_002 --> COD_011
  COD_011 --> COD_012
  COD_005 --> COD_013
  COD_003 --> COD_014
  COD_004 --> COD_015

  COD_011 --> COD_020
  COD_011 --> COD_021
  COD_011 --> COD_022
  COD_010 --> COD_023

  COD_020 --> COD_030
  COD_023 --> COD_031
  COD_030 --> COD_032
  COD_031 --> COD_032
  COD_041 --> COD_033

  COD_010 --> COD_040
  COD_040 --> COD_041
  COD_041 --> COD_042

  COD_050 --> COD_051
  COD_032 --> COD_051
  COD_051 --> COD_052
```

---

## Section A: Data Core

### COD-001

```yaml
id: COD-001
title: "Migration: core tables (project, document, section, link)"
section: A-Data-Core
status: done
depends_on: []
type: migration
priority: critical
affected_files:
  - cod_doc/infra/migrations/0001_core.py
  - cod_doc/infra/db.py
```

**Description:** Поднять SQLAlchemy + Alembic. Создать таблицы `project`, `document`, `section`, `link` согласно [DATA_MODEL.md §3](../DATA_MODEL.md). Поддержать оба диалекта (SQLite/Postgres) — различие только в типах JSON.

**Acceptance:**
- `alembic upgrade head` проходит на чистом SQLite и на чистом Postgres.
- Базовые CRUD-операции через репозиторий (insert/select/update) покрыты smoke-тестами.

> ✅ **Implemented 2026-04-19** (commit `pending`): SQLAlchemy 2.0 + Alembic, схема §3.1-§3.4 (project/document/section/link с sensitivity, content_hash, preamble), репозитории Project/Document/Section, smoke-тесты `tests/infra/test_db_smoke.py` — 5/5 passed. Postgres-проверка отложена до фактического деплоя; SQL-диалект-нейтральный код.

### COD-002

```yaml
id: COD-002
title: "Migration: plan + plan_section + task + dependency + affected_file"
section: A-Data-Core
status: done
depends_on: [COD-001]
type: migration
priority: critical
affected_files:
  - cod_doc/infra/migrations/versions/20260425_0002_tasks.py
  - cod_doc/infra/models.py
  - cod_doc/domain/entities.py
  - tests/infra/test_tasks_migration.py
```

**Description:** Таблицы из [DATA_MODEL.md §3.6-3.9](../DATA_MODEL.md). Вьюхи `section_totals`, `plan_totals`, `ready_tasks`.

**Acceptance:** миграция проходит; view возвращают корректные агрегаты на ручном seed.

> ✅ **Implemented 2026-04-25** (commit `pending`): таблицы plan/plan_section/task/dependency/affected_file (§3.6-3.9), view'ы `section_totals` / `plan_totals` / `ready_tasks` (§4.1-§4.3) — `ready_tasks` фильтрует только по `kind='blocks'`. ORM-модели (`PlanModel`, `PlanSectionModel`, `TaskModel`, `DependencyModel`, `AffectedFileModel`) и domain dataclasses + enums. Smoke-тесты `tests/infra/test_tasks_migration.py` — 6/6 passed; общий suite — 11/11.

### COD-003

```yaml
id: COD-003
title: "Migration: user_story + story_acceptance + story_link + module"
section: A-Data-Core
status: done
depends_on: [COD-002]
type: migration
priority: high
affected_files:
  - cod_doc/infra/migrations/versions/20260425_0003_stories.py
  - cod_doc/infra/models.py
  - cod_doc/domain/entities.py
  - tests/infra/test_stories_migration.py
```

**Description:** Stories и Modules из [DATA_MODEL.md §3.10-3.11](../DATA_MODEL.md).

> ✅ **Implemented 2026-04-25** (commit `pending`): таблицы user_story, story_acceptance, story_link, module, module_dependency, module_code (§3.10-3.11). Уникальные индексы: `user_story.story_id` и `module.module_id` — глобально (§6); `module_dependency(from, to)` — без дублей. Cascade delete от `user_story` на `story_acceptance` / `story_link`. ORM-модели + domain dataclasses + enums (`UserStoryStatus`, `StoryLinkKind`, `StoryRelation`, `ModuleStatus`, `ModuleCodeKind`). Smoke-тесты `tests/infra/test_stories_migration.py` — 7/7 passed; общий suite — 18/18.

### COD-004

```yaml
id: COD-004
title: "Migration: revision + audit_log"
section: A-Data-Core
status: done
depends_on: [COD-003]
type: migration
priority: critical
affected_files:
  - cod_doc/infra/migrations/versions/20260425_0004_revisions.py
  - cod_doc/infra/models.py
  - cod_doc/domain/entities.py
  - tests/infra/test_revisions_migration.py
```

**Description:** Revisions append-only, с индексами для `cod-doc log`. AuditLog для всех write-path вызовов.

> ✅ **Implemented 2026-04-25** (commit `pending`): таблицы `revision` (§3.5) с unique `revision_id` (ULID, 26 chars) и индексами `ix_revision_entity` (entity_kind, entity_id, at) / `ix_revision_parent` для chain-walk; `audit_log` (§3.13) с `payload_json` (JSON-колонка) и индексами `ix_audit_action`, `ix_audit_actor` под фильтрацию по action/time и actor/time. CASCADE от project. ORM-модели + domain dataclasses + enums (`EntityKind`, `AuditSurface`). Smoke-тесты `tests/infra/test_revisions_migration.py` — 5/5 (chain through `parent_revision_id`, ULID uniqueness, JSON round-trip, cascade); общий suite — 23/23.

### COD-005

```yaml
id: COD-005
title: "Migration: link (parsed) + tag"
section: A-Data-Core
status: done
depends_on: [COD-004]
type: migration
priority: high
affected_files:
  - cod_doc/infra/migrations/versions/20260425_0005_links_tags.py
  - cod_doc/infra/models.py
  - cod_doc/domain/entities.py
  - tests/infra/test_tags_migration.py
```

**Description:** Таблицы `tag`, связующие таблицы; финальная проверка индексов.

> ✅ **Implemented 2026-04-25** (commit `pending`): таблицы `tag` (uniq `(project_id, name)`), `document_tag`, `task_tag`, `story_tag` (§3.12) — junction-таблицы с composite PK и CASCADE на обе стороны. Replaced full-column `ix_link_unresolved` with the partial `ix_link_broken WHERE resolved = 0` (§3.4) — горячий read-path «broken-links» останется дешёвым по мере роста resolved-доли. ORM-модели + `Tag` dataclass. Smoke-тесты `tests/infra/test_tags_migration.py` — 6/6 (схема, partial-index наличие+условие, uniqueness per project, attach-to-doc/task/story, дубль через PK, cascade-delete тэга); общий suite — 29/29. **Section A (Data Core) closed.**

---

## Section B: Services

### COD-010

```yaml
id: COD-010
title: "Test + Implement: DocService.create/get/patch_section/rename"
section: B-Services
status: done
depends_on: [COD-001, COD-015]
type: feature
priority: critical
affected_files:
  - cod_doc/services/doc_service.py
  - tests/services/test_doc_service.py
```

**Description:** Создание, чтение, патч секции, rename. Patch → unified diff → `revision`. Rename → cascade update ссылок (пока заготовка; реальный cascade — в COD-013).

**Acceptance:**
- `cod-doc doc new --type guide --title "Hello"` создаёт запись + skeleton.
- `cod-doc doc patch ... --section X` пишет revision.
- Тесты: создание/патч/rename; проверка frontmatter валидации.

> ✅ **Implemented 2026-04-25** (commit `pending`): функциональный API `create / get / get_sections / render_body / add_section / patch_section / rename`. Каждая мутация пишет revision: `create`/`rename` → `entity_kind=DOCUMENT`, `add_section`/`patch_section` → `entity_kind=SECTION` (DATA_MODEL §3.3 «Section.body — носитель»). `render_body` читает через view `document_body` (§4.3a). `patch_section` — no-op на одинаковом body; пробрасывает `expected_parent_revision_id` в RevisionService для optimistic concurrency. `rename` пишет JSON-patch diff `{op, from, to}`; cascade-update incoming-ссылок остался стуб-комментом — реальный cascade в COD-013. Кастомные исключения `DocumentNotFoundError` / `SectionNotFoundError`. Frontmatter-валидация остаётся за COD-020. Тесты — 15/15 (create+revision+UNIQUE+sections+render+patch path/no-op/conflict+rename path/no-op/unknown). Общий suite — 61/61.

> Зависимость дополнена `COD-015`: DocService использует RevisionService для записи revision; формально blocker'ом в исходной графе не значился, но фактически COD-015 был сделан перед COD-010, и API DocService опирается на `rev.write` / `rev.list_for_entity`.

### COD-011

```yaml
id: COD-011
title: "Test + Implement: TaskService (create/update_status/complete)"
section: B-Services
status: pending
depends_on: [COD-002]
type: feature
priority: critical
affected_files:
  - cod_doc/services/task_service.py
  - cod_doc/services/tests/test_task_service.py
```

**Description:** Создание задачи с валидацией формата, генерация id в пределах section-range, update status, complete (с проверкой depends_on). Пишет revision.

### COD-012

```yaml
id: COD-012
title: "Test + Implement: PlanService (recalc, ready, audit, export)"
section: B-Services
status: pending
depends_on: [COD-011]
type: feature
priority: high
```

**Description:** Derived статусы секции/плана. `ready()` через view. `audit()` — проверка циклов, drift. `export()` — регенерация Progress Overview/Next Batch/Dependency Graph в markdown.

### COD-013

```yaml
id: COD-013
title: "Test + Implement: LinkService (parse/resolve/verify/rename-cascade)"
section: B-Services
status: pending
depends_on: [COD-005]
type: feature
priority: high
```

**Description:** Парсер ссылок (remark + regex), резолвер, кэш, верификация, cascade при rename документа.

### COD-014

```yaml
id: COD-014
title: "Test + Implement: StoryService (CRUD, link, coverage)"
section: B-Services
status: pending
depends_on: [COD-003, COD-011]
type: feature
priority: medium
```

### COD-015

```yaml
id: COD-015
title: "Test + Implement: RevisionService (write, list, revert)"
section: B-Services
status: done
depends_on: [COD-004]
type: feature
priority: high
affected_files:
  - cod_doc/services/__init__.py
  - cod_doc/services/revision_service.py
  - tests/services/__init__.py
  - tests/services/test_revision_service.py
```

**Description:** append-only запись, получение истории сущности, revert (через обратный сервисный вызов).

> ✅ **Implemented 2026-04-25** (commit `pending`): `cod_doc/services/revision_service.py` — функциональный API: `write(session, *, project_id, entity_kind, entity_id, author, diff, ...)` (auto-fills ULID + chains via `parent_revision_id`), `list_for_entity(session, entity_kind, entity_id)` (oldest→newest), `RevisionConflictError` при mismatch `expected_parent_revision_id` (DATA_MODEL §3.5 optimistic concurrency). `revert` намеренно стуб — диспетчер по entity-сервисам, COD-022. Тесты — 9/9 passed (write/chain/list/filter/expected-parent match/mismatch/explicit-None varianты/revert NotImplementedError); общий suite — 46/46.

---

## Section C: Write Paths

### COD-020

```yaml
id: COD-020
title: "Implement: write-path validation (frontmatter + task-plan rules)"
section: C-Write-Paths
status: pending
depends_on: [COD-011]
type: feature
priority: critical
```

**Description:** Централизованный модуль валидации, используемый DocService и TaskService. Правила из [standards/frontmatter.md](../standards/frontmatter.md) и [standards/task-plan.md](../standards/task-plan.md).

### COD-021

```yaml
id: COD-021
title: "Implement: cycle detection + critical path (recursive CTE)"
section: C-Write-Paths
status: pending
depends_on: [COD-011]
type: feature
priority: high
```

### COD-022

```yaml
id: COD-022
title: "Implement: completion flow (depends_on gate + log + projection)"
section: C-Write-Paths
status: pending
depends_on: [COD-011, COD-015]
type: feature
priority: high
```

### COD-023

```yaml
id: COD-023
title: "Implement: projection export/import (hash-based detection)"
section: C-Write-Paths
status: pending
depends_on: [COD-010]
type: feature
priority: high
```

---

## Section D: MCP & CLI

### COD-030

```yaml
id: COD-030
title: "Implement: CLI — task/plan/story commands"
section: D-MCP-CLI
status: pending
depends_on: [COD-020]
type: feature
priority: high
affected_files:
  - cod_doc/cli/task.py
  - cod_doc/cli/plan.py
  - cod_doc/cli/story.py
```

### COD-031

```yaml
id: COD-031
title: "Implement: CLI — doc/link/revision commands"
section: D-MCP-CLI
status: pending
depends_on: [COD-023, COD-013]
type: feature
priority: high
```

### COD-032

```yaml
id: COD-032
title: "Implement: MCP tools (doc.*, task.*, plan.*, story.*, link.*, revision.*)"
section: D-MCP-CLI
status: pending
depends_on: [COD-030, COD-031]
type: feature
priority: critical
affected_files:
  - cod_doc/mcp/server.py
  - cod_doc/mcp/tools/
```

### COD-033

```yaml
id: COD-033
title: "Implement: MCP tool context.get (+ ContextService)"
section: D-MCP-CLI
status: pending
depends_on: [COD-041]
type: feature
priority: critical
```

---

## Section E: Retrieval

### COD-040

```yaml
id: COD-040
title: "Implement: embeddings pipeline (sqlite-vss / pgvector)"
section: E-Retrieval
status: pending
depends_on: [COD-010]
type: feature
priority: medium
```

### COD-041

```yaml
id: COD-041
title: "Implement: ContextService L0/L1"
section: E-Retrieval
status: pending
depends_on: [COD-040]
type: feature
priority: high
```

### COD-042

```yaml
id: COD-042
title: "Implement: ContextService L2/L3 + semantic search"
section: E-Retrieval
status: pending
depends_on: [COD-041]
type: feature
priority: medium
```

---

## Section F: Migration

### COD-050

```yaml
id: COD-050
title: "Test: frontmatter/task-plan parser (property-based)"
section: F-Migration
status: pending
depends_on: []
type: test
priority: critical
```

### COD-051

```yaml
id: COD-051
title: "Implement: Restate importer (docs/plans/stories/links/git-history)"
section: F-Migration
status: pending
depends_on: [COD-032, COD-050]
type: feature
priority: high
affected_files:
  - cod_doc/importers/restate.py
```

### COD-052

```yaml
id: COD-052
title: "Implement: projection freeze + accept flow"
section: F-Migration
status: pending
depends_on: [COD-023, COD-051]
type: feature
priority: high
```
