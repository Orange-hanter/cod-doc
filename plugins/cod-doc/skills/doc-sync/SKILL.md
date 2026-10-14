---
name: doc-sync
description: |
  Документы cod-doc пишутся в БД напрямую — MCP-тулами `doc_*` или CLI
  `cod-doc doc …`, без промежуточного markdown. Семантика drift, починка
  чужой правки проекции, реестр MASTER.md.
  Триггеры: drift, doc import, edited_in_place, stale_export, conflict, MASTER.md, хэш,
  реестр, правка документации, новый документ, документ не виден в БД.
---

# Doc sync — markdown это проекция, истина в БД

Документ создаётся и правится **в БД**, тулами. `.md` на диске — проекция:
её не пишут руками, чтобы потом импортировать, и не выгружают после записи.

Слаг проекта: `cod-doc project list --json` → `[{slug, root_path, db_url}]`,
слаг — элемент, чей `root_path` совпадает с корнем репо.

## Запись документа

Порядок: MCP-тул → CLI. Python-скрипты поверх `services/` и SQL в `state.db`
не используются.

| Действие | MCP | CLI |
|---|---|---|
| Новый документ | `doc_create(project, doc_key, type, status, title, preamble=…)` | `cod-doc doc create --key … --type … --title … -p <slug>` |
| Title, type, owner, sensitivity, preamble | `doc_update(project, doc_key, title=…, type=…, owner=…, sensitivity=…, preamble=…)` | `cod-doc doc update <doc_key> --title … -p <slug>` |
| Добавить секцию | `doc_add_section(project, doc_key, anchor, heading, body, level, position)` | `cod-doc doc add-section <doc_key> <anchor> --heading … --body-file - -p <slug>` |
| Прочитать секцию (+ head-ревизия) | `doc_section_get(project, doc_key, anchors=[…])` | `cod-doc doc section <doc_key> <anchor> -p <slug>` |
| Заменить тело секции | `doc_patch_section(project, doc_key, anchor, body, expected_parent_revision_id=…)` | `cod-doc doc patch <doc_key> <anchor> --body-file - --expected-revision … -p <slug>` |
| Переставить секцию (история сохраняется) | `doc_move_section(project, doc_key, anchor, position)` | `cod-doc doc move-section <doc_key> <anchor> --position N -p <slug>` |
| Переименовать заголовок/якорь секции (ссылки следуют) | `doc_rename_section(project, doc_key, anchor, heading, new_anchor=…)` | `cod-doc doc rename-section <doc_key> <anchor> --heading … -p <slug>` |
| Удалить секцию | `doc_delete_section` | `cod-doc doc delete-section <doc_key> <anchor> -p <slug>` |
| Удалить документ (необратимо; MCP — сначала dry run) | `doc_delete(project, doc_key)` → `doc_delete(…, dry_run=False)` | `cod-doc doc delete <doc_key> -p <slug>` |
| Переименовать ключ/путь | `doc_rename` | `cod-doc doc rename` |
| DRAFT/REVIEW → ACTIVE | `doc_accept` | `cod-doc doc accept` |
| Любой статус (deprecated, done, …) | `doc_update_status(project, doc_key, status)` | `cod-doc doc status <doc_key> <status> -p <slug>` |
| Положить в раздел дерева | `doc_set_node` | `cod-doc doc tree move` |

У мутаций есть `dry_run` / `--dry-run` — для правки чужого текста смотри diff
до записи. `patch` с `expected_parent_revision_id` не перетрёт правку, которая
легла после твоего чтения.

После записи **не** создавай и не обновляй `.md`: ни руками, ни `doc export`.
`stale_export` на только что записанном документе — норма, а не задача.

## Семантика drift

| Статус | Значение | Действие |
|---|---|---|
| `edited_in_place` | кто-то правил файл, БД отстала | **дефект** — см. ниже |
| `stale_export` | БД свежее проекции на диске | **норма**; не трогать |
| `missing` | файла нет | норма для документа, созданного тулом; вслепую не пересоздавать |
| `conflict` | после выгрузки правили и БД, и файл | **дефект**, решает человек: `doc export <key> --dry-run` показывает, что потеряет файл |

`doc export` на диск — под guard'ом до byte-identical round-trip; не зови его.

### `edited_in_place` — правка проекции мимо тулов

Это ремонт чужой правки, не способ писать документы. Если файл правил ты сам —
перенеси изменение тулом (`doc_patch_section` / `doc_add_section`) и верни
файл (`git checkout -- <file>`): дальше он честно покажет `stale_export`. Если правку
сделал человек и она должна остаться — `cod-doc doc import <file> -p <slug>`
(перечитывает файл целиком в БД), затем `cod-doc doc drift -p <slug> --all`.

## MASTER.md

Гибридная ссылка `📁 /path | 🗃️ doc:key` хэша не требует: устаревание
документа ловит `doc drift`. Ссылка с хвостом `| 🔑 sha:…` (старый формат
чужих проектов) требует пересчёта после изменения документа:
`cod-doc hash update` или MCP `master_hash_update(project, dry_run=False)`.
Если `MASTER.md` — документ проекта, пересчёт пишет его в БД, файл не
трогает; `doc import MASTER.md` после него не нужен и вернул бы старые хэши.
В ссылку на документ из БД хэш не добавляй.

## Не забывай

- Числа в документации (счётчики тулов, документов) сторожат anti-drift тесты
  проекта: правишь одну сторону — правь обе.
- Тесты с CLI-выводом гоняй `env -u FORCE_COLOR`: rich красит вывод внутри
  `CliRunner`, строковые ассерты падают на ANSI-кодах.
