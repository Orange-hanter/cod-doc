---
type: capability
scope: doc-evolution
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-09-21
related_docs:
  - ../standards/revision-history.md
  - ../standards/document-link.md
  - auto-linking.md
---

# Capability — Documentation Evolution

> Управляемая эволюция документов: создание, патчинг, переименование, слияние, декомпозиция — без рассинхронизации с кодом и ссылками.

## 0. As implemented (2026-09-21)

SoT — строка `document` в SQLite; markdown — проекция. Живые тулы:
`doc_create` / `doc_export` / `doc_drift` / `doc_body` /
`doc_accept` / `doc_rename` / `doc_add_section` / `doc_patch_section` /
`doc_delete_section` (MCP-обёртки над `doc_service.add_section`,
`doc_service.patch_section` и `doc_service.delete_section`, optimistic
concurrency у патча — через `expected_parent_revision_id`, добавлены
2026-09-18; CLI-зеркала — `cod-doc doc add-section`, `cod-doc doc patch` и
`cod-doc doc delete-section`). Цикл `doc_create` → `doc_add_section` →
`doc_patch_section` → `doc_delete_section` проходится целиком без файла на
диске. Импорт файла обратно в БД — только CLI (`cod-doc doc import`) и кнопка
в web-UI; MCP-тула с таким именем нет. Ручная правка проекции →
`edited_in_place` / `ExportGuardError`. Ниже ещё встречаются dotted
`doc.patch_section` — читай их как `doc_patch_section`.

### 0.1 Секция, ушедшая из файла (ADO-213 / ADO-213)

До ADO-213 у секции, **исчезнувшей** из markdown, пути не было вовсе:
`import_or_update_markdown` умел только патчить и дописывать в конец. Дальше
`content_sha256_head` пиннил хэш файла, и `detect_drift` рапортовал `in_sync`
навсегда — расхождение копилось молча.

Закрыто тремя частями:

- **`doc_delete_section` / `cod-doc doc delete-section`** — точечное удаление
  по закону ADO-040 (revision + activity event одним атомарным вызовом,
  позиции уплотняются). Спрашивает подтверждение, `--yes` его снимает,
  `--dry-run` печатает удаляемое тело. `--json` выбирает формат вывода, а не
  согласие: вопрос уходит в stderr, stdout остаётся разбираемым (ADO-213).
  Тело переживает удаление в ревизии, но `revision revert` его не отыграет —
  строка, на которую указывает `entity_id`, уже не существует.
- **`doc import <file> --replace`** — файл считается полным телом: сироты
  удаляются, порядок секций в БД приводится к порядку в файле. Не по
  умолчанию: обычный `doc import` — это «подтяни мои правки», и тихое
  удаление секции по неполному файлу опаснее сироты. В обычном режиме сироты
  уходят предупреждениями в `ImportReport.orphan_sections`.
  Режим предъявляет счёт до, а не после: `--dry-run` прогоняет импорт и
  откатывает, без `--yes` спрашивает по числу удаляемых секций, а файл,
  который распарсился **в ноль секций** при непустой БД, отвергается
  (`ReplaceWouldEmptyError`) — оборванная запись и пустой буфер редактора
  выглядят ровно так; сознательное опустошение документа — `--force`
  (ADO-213).
- **Сигнал в `doc drift`** — `orphan_sections` рядом с `DriftStatus`, по
  образцу `metadata_mismatch` (ADO-092) и по той же причине: четыре статуса
  разбивают состояния **хэша содержимого**, а этот класс проходит сквозь все
  четыре. Словарь отчёта собирает одна функция — `DriftReport.as_payload()`
  (ADO-216); ею пользуются все шесть поверхностей: MCP `doc_drift` /
  `doc_drift_all` / `ctx_drift`, CLI `doc drift --json` и карточка куратора
  (`curator_service._drift_card` → `curator_next`). До неё словарь был
  переписан шесть раз вручную, и `metadata_mismatch` не попадал ни в одну
  копию: документ приезжал в список проблем со статусом `in_sync` и без
  причины — на живой БД так висело 35 документов.

Тождество секции — `import_service.match_file_sections`: сначала якорь,
потом нормализованный текст заголовка. Только по якорю сравнивать нельзя:
хранимый якорь не обязан быть выведенным из заголовка, `scenario_service`
пишет `anchor="scn-001"` рядом с `## SCN-001 — …`, из которого `_slugify`
даёт `scn-001--completing-a-task-…`. На живой БД cod-doc сравнение по одному
якорю дало 29 документов / 146 секций, из них 16 / 80 — ложные, весь корпус
`docs/system/scenarios/*`; в `--replace` каждая такая секция удалялась и
пересоздавалась под выведенным якорем, теряя родословную ревизий, ломая
ссылки `#scn-001` и унося каскадом строки `doc_comment`.

## 1. Проблема ручного подхода

В Restate:

- Новый раздел спеки модуля добавляется правкой большого файла → конфликты при параллельной работе.
- Переименование документа (`Modules/M1 AUTH.md` → `Modules/M1 AUTH v2.md`) требует правки всех входящих ссылок.
- Декомпозиция модуля (превратить один файл в директорию с `overview.md`, `domain.md`, …) делается руками.
- Поля `last_updated`, `last_reviewed` забываются.

COD-DOC берёт это на себя.

## 2. Операции

| Операция | Сервис | Что делает атомарно |
|----------|--------|---------------------|
| Создание документа | `DocService.create` | Skeleton по `type`, frontmatter, первая revision |
| Патч секции | `DocService.patch_section` | Diff, revision, re-index links, re-embed |
| Замена секции целиком | `DocService.replace_section` | Аналог, но diff = полный replace |
| Вставка секции | `DocService.insert_section` | Позиционирование по anchor, автоматические anchors |
| Переименование | `DocService.rename` | Cascade update всех `link.to_doc_key` |
| Декомпозиция | `DocService.split_into_folder` | Разбивает на subdocuments, создаёт entrypoint |
| Слияние | `DocService.merge` | Обратная операция split |
| Перевод в deprecated | `DocService.deprecate` | Обновляет frontmatter, проставляет `canonical_source` |

## 3. Skeleton-шаблоны

Для каждого `type` — собственный шаблон. Шаблоны хранятся в `cod_doc/templates/` (как сейчас `MASTER.md.j2`), дополняются:

- `module-spec.md.j2` — структура из Restate `standards/module-spec.md`.
- `execution-plan.md.j2` — Navigation + Progress Overview + Gap Analysis + Next Batch.
- `task-section.md.j2` — `# Section X:` + back-link + разделитель.
- `user-story.md.j2` — persona, narrative, acceptance.
- `architecture.md.j2`, `standard.md.j2` — minimal.

`cod-doc doc new --type module-spec --module M1-auth` генерирует документ из шаблона, сохраняет в БД.

## 4. Patch API

### 4.1 Через CLI

```bash
cod-doc doc patch modules/M1-auth/overview \
  --section "Data Model" \
  --from-file /tmp/new-data-model.md \
  --reason "Add account_status column"
```

### 4.2 Через MCP

```
→ doc.patch_section({
    doc_key: "modules/M1-auth/overview",
    anchor: "data-model",
    new_body: "...",
    reason: "Add account_status column"
  })
← { revision_id: "01HQX5Z9F0K8RNG6CB7VHQK4XX", updated_links: 3, reindex_queued: true }
```

## 5. Inline-правки под patch-review

Для крупных изменений (обычно агентом) доступен режим предложения:

```
→ doc.propose_edit({ doc_key, patch, reason })
← { proposal_id, pending_approval: true }
→ doc.approve(proposal_id) | doc.reject(proposal_id)
```

Это нужно, когда COD-DOC в командной среде и не все агентские правки идут напрямую. Для одиночного режима можно включить auto-approve (`config.doc.auto_approve_from: ["agent:task-steward"]`).

## 6. Переименование и декомпозиция

### 6.1 Rename

```bash
cod-doc doc rename \
  modules/M1-auth \
  modules/M1-auth/overview \
  --reason "Decomposing into folder"
```

Атомарно:

1. Переносит body/frontmatter в новый `doc_key`.
2. Удаляет старый row **только** после пересчёта ссылок.
3. Генерирует legacy-redirect документ с `canonical_source = new_doc_key`, если флаг `--keep-redirect`.

### 6.2 Split into folder

```bash
cod-doc doc split modules/M1-auth-v2 --into \
  overview,domain,contract,database,implementation,roadmap \
  --strategy by-heading
```

Разрезает по H2, создаёт subdocuments, генерирует entrypoint `modules/M1-auth/overview` с «Decomposition Status» + «Quick Links Table».

## 7. Актуализация полей

Сервис поддерживает автоматические поля:

- `last_updated` — пишется при любом patch.
- `last_reviewed` — пишется явной командой `cod-doc doc review <doc> --by <owner>`.
- `implemented_in` — можно освежать из workspace-map (см. [capabilities/auto-linking.md](auto-linking.md)).
- `affected_files` для связанных задач обновляются при rename файлов кода (если включена `code-tracking`).

## 8. Stale detection

`cod-doc audit --stale`:

- Находит документы с `status=active` и `last_updated > 180d`.
- Для модульных спек: если `implemented_in` указывает на несуществующий код — `code-drift` warning.
- Для task-plans: если `last_updated` плана отстаёт от `max(last_updated)` его задач — error «plan not synced».

## 9. Работа с секциями

Структурные изменения:

- `cod-doc section move <doc> <anchor> --after <other-anchor>` — переставляет порядок.
- `cod-doc section promote <doc> <anchor>` — поднимает заголовок на уровень выше.
- `cod-doc section extract <doc> <anchor> --to <new-doc>` — выносит секцию в новый документ, ставит на место ссылку.

Все операции пишут revision и обновляют ссылки.

## 10. Code drift detection

Побочный эффект capability: если в frontmatter `implemented_in: [restate-api/src/auth/]`, COD-DOC умеет:

- При каждом `cod-doc sync` проверить существование директорий.
- Перекрывать frontmatter список реальных подпапок (prompt: «следующие новые файлы не упомянуты в спеке…»).
- Генерировать task типа `docs` «Docs: document new files in M1 AUTH» с `affected_files = ...` для незакрытой разницы.

Механика опирается на [standards/task-plan.md §9 affected_files](../standards/task-plan.md) и замыкает цикл doc ↔ code.

## 11. Пример полного цикла

1. Автор просит агент: «распиши модуль биллинга».
2. Агент: `doc.new(type="module-spec", module_id="M1-billing")` → skeleton.
3. Агент: несколько `doc.patch_section` — раздел за разделом.
4. Для каждой секции `ContextService.build` возвращает только «достаточный» контекст.
5. По мере записи структурных фактов автоматически создаются задачи (`task.create` из Acceptance).
6. По ссылке в MASTER — `link` резолвится автоматически.
7. Revision-лог документа — полная история для ревью.

## 12. Соотношение с ручным редактированием

Ручные правки markdown поддерживаются, но:

- При import: parsing + diff vs current body + revision с `author: human:<login>`.
- Markdown, не попадающий в БД (новый файл под `docs/system/`), регистрируется как `type=guide` с `last_updated = now`.
- Файлы с расхождением `projection_hash` остаются «на согласовании» до `cod-doc doc accept <doc>`.
