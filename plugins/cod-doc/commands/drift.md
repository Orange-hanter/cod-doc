---
description: Проверить дрейф markdown ↔ БД cod-doc и починить edited_in_place через doc import
argument-hint: "[путь к .md | --all] [--fix]"
---

Аргумент: `$ARGUMENTS` (пусто = `--all`, без починки).

Слаг проекта резолвь как в `/cod-doc:status`.

**Проверка**

```
<cod-doc> doc drift -p <slug> --all --json      # или MCP ctx_drift / doc_drift_all
```

**Семантика — путать нельзя**

| Статус | Значение | Действие |
|---|---|---|
| `edited_in_place` | файл правлен на диске, БД отстала | **дефект** → `doc import` |
| `stale_export` | БД свежее проекции на диске | **норма** в files-are-source режиме; не трогать |
| `missing` | файла на диске нет | разбираться, вслепую не пересоздавать |

**Починка** (только если в аргументе есть `--fix`)

Для каждого `edited_in_place`:

```
<cod-doc> doc import <path> -p <slug>
```

Если правленый файл входит в hash-реестр корневого `MASTER.md` — после
импорта ещё `<cod-doc> hash update`, а затем `doc import MASTER.md`,
иначе реестр разъедется с файлами.

`doc export` на диск не запускай — он под guard'ом до byte-identical
round-trip.

Финал: повторный `doc drift --all`, в ответе — было/стало по каждому статусу
и список файлов, которые импортировал. Ничего не импортируй без `--fix`.
