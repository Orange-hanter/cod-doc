---
name: doc-sync
description: |
  Синхронизация markdown ↔ БД в cod-doc: doc import после правки файла, hash
  update для реестра MASTER.md, семантика drift, регистрация нового документа.
  Триггеры: drift, doc import, edited_in_place, stale_export, MASTER.md, хэш,
  реестр, правка документации, документ не виден в БД.
---

# Doc sync — markdown это проекция, истина в БД

Правка tracked-`.md` на диске оставляет БД позади → drift `edited_in_place`.
PostToolUse-хук плагина напомнит (он проверяет файл по таблице `document`,
поэтому молчит на untracked-файлах); дальше — вручную.

Слаг проекта: `cod-doc project list` или
`sqlite3 -readonly .cod-doc/state.db "select slug, root_path from project"`.

## Правка существующего документа

```bash
cod-doc doc import <file.md> -p <slug>    # frontmatter + body → БД
cod-doc doc drift -p <slug> --all         # контроль: edited_in_place == 0
```

Если файл входит в hash-реестр корневого `MASTER.md`:

```bash
cod-doc hash update                       # пересчёт реестра (правит MASTER.md)
cod-doc doc import MASTER.md -p <slug>    # и сам MASTER.md — тоже в БД
```

## Семантика drift

| Статус | Значение | Действие |
|---|---|---|
| `edited_in_place` | файл правлен, БД отстала | **дефект** → `doc import` |
| `stale_export` | БД свежее, проекция на диске старее | **норма** в files-are-source; не трогать |
| `missing` | файла нет | разобраться; вслепую не пересоздавать |

`doc export` на диск — под guard'ом до byte-identical round-trip; наружу
экспортировать не надо.

## Новый документ → регистрация в БД

CLI `doc import` работает только по уже известным ключам. Новый файл
регистрируется сервисом:

```python
from cod_doc.domain.entities import DocumentType
from cod_doc.services import import_service
# внутри transactional(sf) as s:
import_service.import_markdown(
    s, project_id=<id>, doc_key="docs/system/новый-документ",   # путь без .md
    raw_markdown=raw, fallback_title="…",
    fallback_type=DocumentType.MODULE_SPEC,
    author="claude-x", reason="registration")
```

Повторный `import_markdown` того же ключа падает — для обновления есть
`import_or_update_markdown` (его и зовёт `doc import`).

Массовая регистрация нового дерева документации — `cod-doc import docs -p
<slug> --dry-run` сначала, и только после просмотра плана — без `--dry-run`.

## Не забывай

- Корневой `MASTER.md` — regen-on-write: правил его, импортируй его.
- Числа в документации (счётчики тулов, документов) сторожат anti-drift тесты
  проекта: правишь одну сторону — правь обе.
- Тесты с CLI-выводом гоняй `env -u FORCE_COLOR`: rich красит вывод внутри
  `CliRunner`, строковые ассерты падают на ANSI-кодах.
