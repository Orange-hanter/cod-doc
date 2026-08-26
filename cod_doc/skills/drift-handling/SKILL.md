---
name: drift-handling
description: |
  Что делать при STALE / BROKEN / drift расхождении: hash mismatch,
  edited_in_place, missing files. Триггеры: drift, stale, broken,
  hash, mismatch, sync, sha, verify, projection_hash.
---

# Skill — Drift handling

## Когда подгружается

Задачи / контексты, где появляется расхождение между БД-проекцией и
файлом на диске, либо устаревший хэш в гибридной ссылке.
Триггер-keywords: `drift`, `stale`, `broken`, `hash`, `mismatch`,
`sha:`, `verify`, `projection_hash`, `update_master_hashes`,
`check_stale_refs`.

## Канонические статусы (`doc_drift`)

| Статус | Что означает | Что делать |
|--------|-------------|-----------|
| `in_sync` | DB content hash == projection_hash == file hash | ничего |
| `stale_export` | DB content изменился относительно последней экспорт-проекции | `doc.export(force=False)` или `force=True` после ручной проверки — но сперва проверь ADO-022 (ниже) |
| `edited_in_place` | Файл правился в обход revision-flow (file hash расходится с projection_hash) | reconciliation-flow: либо `import_document`, либо ручной merge с записью revision |
| `missing` | Файл удалён | если дока удалили — `doc.deprecate`; если случайность — восстановить из git history |

## Канонические статусы (`check_stale_refs`)

| Статус | Что делать |
|--------|-----------|
| `VALID` | OK |
| `STALE` | хэш в `MASTER.md` устарел → `update_master_hashes` после проверки контента |
| `BROKEN` | файл отсутствует на диске → задача восстановления через `task_create` |

## Алгоритм при обнаружении drift'а

1. Читать текущий статус через `doc_drift(doc_key)`.
2. Если `stale_export` и контент в БД — source of truth → `doc_export`.
3. Если `edited_in_place`:
   a. Прочитать файл и DB-content.
   b. Если правки **намеренные** → `import_document(file_path)` (применит
      content к БД + запишет revision).
   c. Если правки **нечаянные** → восстановить из БД (`doc_export
      force=True`).
4. Если `missing` → решить, удалили или потерялся; зависит от
   `last_updated` и git log.

## Старая БД: сначала `doc_backfill_projection` (ADO-022)

Если БД проекта заведена до миграции `0025_projection_fidelity`, у документов
`frontmatter_raw` и `title_in_body` = NULL — БД не помнит, как был устроен
файл. Массовый `doc_export` на такой БД **переписывает frontmatter** (ключи
переставляются, файлу без frontmatter дописывается выдуманный блок
`type/status/owner`) и добавляет `# H1`, которого в источнике не было. Именно
так однажды пострадали 107 файлов из 121.

- `doc_export` теперь сам отказывается писать такой файл. В сообщении есть
  слова `frontmatter_raw` и `backfill`.
- Лечение — `doc_backfill_projection(project)`: восстанавливает две колонки
  формы из файлов на диске. Доменные поля не трогает, поэтому изменения
  метаданных, сделанные в БД и ещё не выгруженные (например `doc_accept`),
  переживают операцию.
- `force_write=true` на этой ошибке — **не лечение, а сама порча.** Применять
  только осознанно и только когда файл на диске заведомо не нужен.
- `doc import` тоже снимет отказ, но применит frontmatter файла обратно к БД,
  то есть откатит несэкспортированные правки метаданных. Это второй вариант,
  не первый.

Порядок на пилотном проекте: `doc_backfill_projection` → `doc_drift_all` →
только потом `doc_export`.

## Что НЕ делать

- Не запускать `update_master_hashes` "наугад" — сначала убедись, что
  контент валиден; иначе зафиксируешь broken state.
- Не правь `projection_hash` руками; это поле обновляется только
  `doc_export`.
- Не глотай `STALE`/`BROKEN` молча — поднимай задачу через
  `task_create` с типом `bug` или `chore`.

## Связанное

- [capabilities/doc-evolution.md](../../../docs/system/capabilities/doc-evolution.md)
- [services/projection_service](../../services/projection_service/)
- skill `validation` (write-path checks).
