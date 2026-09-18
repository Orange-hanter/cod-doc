---
type: standard
scope: frontmatter
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-28
---

# Frontmatter Standard

> Описывает обязательные и рекомендованные поля YAML frontmatter для всех документов проекта, управляемого COD-DOC.
> Наследуется от Restate (`Docs/standards/frontmatter.md`) с расширениями под БД-валидацию.

## 1. Общее правило

Каждый документ имеет frontmatter. Когда документ создаётся через `cod-doc doc new`, frontmatter генерируется автоматически из полей сущности `Document`. При ручном редактировании markdown и последующем import — COD-DOC парсит frontmatter и применяет как patch к записи `Document`.

## 2. Обязательные поля

| Поле | Значения | Мэппинг в БД |
|------|----------|--------------|
| `type` | `module-spec`, `module-subdoc`, `execution-plan`, `task-section`, `execution-log`, `standard`, `architecture`, `vision`, `guide`, `user-story`, `decision`, `open-question`, `redirect`, `design`, `audit`, `audit-report`, `journal`, `plan`, `analysis`, `research`, `capability`, `scenario-set` | `document.type` |
| `status` | См. таблицу §2a (зависит от `type`); чужие написания — §2b | `document.status` |
| `owner` | Строка (команда или роль) | `document.owner` |
| `last_updated` | `YYYY-MM-DD` | `document.last_updated` |
| `source_of_truth` | `true` / `false` *(или вложенный dict для `execution-plan` — см. §7)* | `document.source_of_truth` |

Источник истины по списку `type` — enum `DocumentType` (`cod_doc/domain/entities.py`);
таблица выше обязана совпадать с ним значение в значение. Восемь типов
(`design`, `audit`, `audit-report`, `journal`, `plan`, `analysis`, `research`,
`capability`) добавлены в ADO-015: они уже жили в корпусах, но импорт молча
превращал их в `module-spec`.

## 2a. Допустимые `status` по `type`

Каждый `type` определяет своё подмножество `status`. Несовместимая пара (например `type: execution-plan` + `status: active`) → error.

| `type` | Допустимые `status` | Терминал |
|--------|---------------------|----------|
| `module-spec`, `module-subdoc`, `standard`, `architecture`, `vision`, `guide`, `redirect` | `draft` → `review` → `active` → `deprecated` | `deprecated` |
| `execution-plan`, `task-section`, `execution-log` | `pending` → `in-progress` → `done` (опц. `blocked`, `cancelled`) | `done` / `cancelled` |
| `user-story` | `draft` → `accepted` → `delivered` → `archived` | `archived` |
| `audit-report`, `audit` | `active` (живой аудит **и** закрытый — в frontmatter его пишут `resolved`, см. §2b) → `deprecated` (замещён; в frontmatter `superseded`) | `deprecated` |
| `design`, `analysis`, `research`, `capability`, `decision`, `open-question`, `scenario-set` | `draft` → `review` → `active` → `deprecated` | `deprecated` |
| `plan` | `pending` → `in-progress` → `done` (опц. `blocked`, `cancelled`) | `done` / `cancelled` |
| `journal` | `active` — журнал не «завершается», он либо ведётся, либо `deprecated` | `deprecated` |

## 2b. Чужие статусы при импорте

В БД живут ровно четыре значения `status`: `draft`, `review`, `active`,
`deprecated`. Чужие корпуса пишут иначе (`living`, `final`, `done`, `accepted`,
`archived`, `superseded`, …). Импорт не выбрасывает их и не притворяется, что
понял: таблица `_ALIEN_STATUS_ALIASES` (`cod_doc/services/import_service.py`)
переводит известные написания, и **каждая такая замена попадает в
`ImportReport.warnings`** с `reason: alias`. Написание, которого нет в таблице,
даёт `draft` и `reason: unknown`.

| Чужое написание | Канонический `status` |
|---|---|
| `living`, `final`, `done`, `complete`, `completed`, `resolved`, `accepted`, `delivered`, `published`, `current`, `stable`, `in-progress` | `active` |
| `proposed`, `pending`, `wip`, `todo` | `draft` |
| `in-review`, `reviewing` | `review` |
| `archived`, `resolved`, `superseded`, `obsolete`, `rejected`, `cancelled` | `deprecated` |

Побочный эффект, о котором стоит знать: `final`, `done` и `resolved` становятся `active`,
поэтому FM-005 (`stale-doc`) начинает считать возраст исторических документов.
Это осознанный выбор — «завершённый» документ не то же самое, что снятый с
эксплуатации.

## 3. Условно-обязательные

- `last_reviewed` — для `type` ∈ {`module-spec`, `architecture`, `standard`}.
- `created` — для любых, но COD-DOC заполнит автоматически при создании.
- Если `source_of_truth: false`:
  - `canonical_source` — одна из известных `doc_key`.
  - `scope` — причина существования (`legacy-redirect`, `derived-analysis-redirect`, `domain-appendix-redirect`).
  - `audience` — не пустой массив.
  - `related_code` — массив (может быть пустым `[]`).

## 4. Рекомендованные поля

| Поле | Назначение |
|------|------------|
| `tags` | Массив строк; мэппится в `tag` + `document_tag` |
| `project` | Slug проекта |
| `audience` | `[contributors, agents, product, ...]` |
| `related_code` | Массив путей |
| `implemented_in` | Dict (`backend: [...]`, `tests: [...]`) — для module-spec |
| `depends_on` | Массив ссылок на модули — для module-spec |
| `api_navigation` | Dict — для module-spec |
| `schema` | Dict — для module-spec |
| `task_plan` | Путь к плану модуля |

## 5. Поля, зарезервированные COD-DOC

Эти поля COD-DOC проставляет автоматически и может перезаписать при export:

| Поле | Назначение |
|------|-----------|
| `doc_key` | Идентификатор в БД (`modules/M1-auth/overview`) |
| `projection_hash` | Хеш последнего экспорта |
| `cod_doc_generated` | `true` если документ сгенерирован полностью из шаблона |
| `revision` | ID последней revision |

Автор не должен редактировать их вручную; при конфликте побеждает БД.

## 6. Валидация

Служба `DocService.validate_frontmatter(doc)` выполняется:

1. На каждом write-path действии.
2. На команде `cod-doc audit`.
3. На git pre-commit hook (устанавливается через `cod-doc hooks install`).

Правила:

- `FM-001` Неизвестное значение `type` → error. На пути **import** это не error, а
  предупреждение: значение вне enum откатывается к fallback в БД и попадает в
  `ImportReport.warnings` (`reason: unknown`), а **файл при этом не
  переписывается** — за это отвечает `_raw_matches_db` (ADO-010). Импорт чужого
  корпуса не имеет права ни ронять прогон, ни править чужой markdown.
- `FM-002` `status=active` при пустом `owner` → error.
- `FM-003` `source_of_truth: false` без `canonical_source` → error (для `execution-plan` см. §7 — dict-вариант исключён).
- `FM-004` `last_updated` в будущем → warning.
- `FM-005` `last_updated` старше 180 дней для `status=active` → warning (`stale-doc`).
- `FM-006` Несовместимая пара `type`/`status` (см. §2a) → error.
- `FM-007` *(reserved, см. [sensitive-data.md](sensitive-data.md))* Отсутствие `sensitivity` для документов с `type` ∈ `{module-spec, architecture, standard}` → warning. Реализуется в задаче COD-025.

## 7. Соотношение с task-plan ecosystem

Task-plan использует узкое подмножество и переопределяет часть значений:

- `status` в execution-plan: `pending` / `in-progress` / `done` (а не `draft`/`active`).
- `source_of_truth` в execution-plan: nested dict с указанием источников каждого аспекта (vision, architecture, data_model, …) вместо boolean. Пример:
  ```yaml
  source_of_truth:
    vision: docs/system/VISION.md
    architecture: docs/system/ARCHITECTURE.md
  ```
  При write-path валидации FM-003 (требование `canonical_source` при `false`) не применяется — наличие dict эквивалентно «у плана есть источники».
- `owner` не требуется (владелец — Task Steward по конвенции).

Подробнее: [task-plan.md](task-plan.md) и Restate `tools/task-plan-ecosystem.md §3`.

## 8. Примеры

### 8.1 Canonical module spec

```yaml
---
type: module-spec
module_id: M1-auth
module_name: Authentication
status: active
owner: backend-team
source_of_truth: true
version: "2.0"
created: 2026-02-14
last_updated: 2026-04-19
last_reviewed: 2026-04-19
implemented_in:
  backend: [restate-api/src/auth/]
  tests: [restate-api/src/auth/__tests__/]
depends_on: []
api_navigation:
  paths: [/api/v1/auth]
  schemas: [LoginRequest, LoginResponse]
task_plan: modules/M1-auth/M1-auth-task-plan
tags: [module, spec, auth]
---
```

### 8.2 Legacy redirect

```yaml
---
type: redirect
status: deprecated
source_of_truth: false
canonical_source: modules/M1-auth/overview
scope: legacy-redirect
owner: backend-team
last_updated: 2026-04-19
audience: [contributors, agents]
related_code: []
---
```

### 8.3 User story

```yaml
---
type: user-story
status: accepted
owner: product
source_of_truth: true
story_id: US-014
persona: Agency Owner
priority: high
tags: [agency, onboarding]
---
```
