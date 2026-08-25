---
type: capability
scope: backup-and-export
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-06-08
last_updated: 2026-06-08
related_docs:
  - ../DATA_MODEL.md
  - ../standards/revision-history.md
  - audit-and-ci.md
audience: [contributors, agents]
---

# Capability — Backup, Export & Recovery

> ⚠️ **Намечено, не реализовано (на 2026-06-08).** Это спецификация будущей
> возможности (DOC-ME-2). Команды `cod-doc backup|restore|export` ещё не
> существуют в CLI; документ задаёт целевой контракт, чтобы реализация и
> аудит были согласованы заранее.

## 1. Зачем

БД (`.cod-doc/state.db`) — источник истины для документов, задач, ссылок и
истории (см. [DATA_MODEL.md](../DATA_MODEL.md)). Markdown — это проекция. Значит
надёжность данных = надёжность БД, и нужны три операции: снять снимок, восстановить
его, и выгрузить данные в нейтральный формат для миграции на другое хранилище.

## 2. Backup

`cod-doc backup --output state.tar.gz`

- Архивирует `.cod-doc/state.db` **плюс** projection-hash baseline (чтобы
  восстановление можно было сверить с принятым состоянием проекции).
- Включает версию схемы (alembic revision), чтобы `restore` мог проверить
  совместимость.
- Безопасно на горячей БД: снимок берётся через `VACUUM INTO` / резервное
  копирование SQLite, не копированием файла под нагрузкой.

## 3. Restore

`cod-doc restore <archive>`

- Проверяет совместимость миграций: если alembic-revision архива новее/старее
  текущего кода — отказ с инструкцией (накатить миграции / обновить пакет), а не
  тихая порча.
- Восстанавливает БД и сверяет projection-hash: расхождение → предупреждение
  `restore-drift`, а не молчаливая перезапись markdown.
- Идемпотентен по отношению к уже совпадающему состоянию.

## 4. Export

`cod-doc export --format markdown|json|sqlite-dump`

Выгрузка для миграции к другому хранилищу или внешнего анализа:

- `markdown` — рендер всех документов в проекцию (controlled-операция, см.
  предупреждение о round-trip ниже).
- `json` — структурированный дамп сущностей (documents/sections/tasks/links/
  revisions) для импорта в другую систему.
- `sqlite-dump` — сырой SQL-дамп схемы и данных.

## 5. Риски и ограничения

- **Round-trip fidelity.** Закрыто в ADO-010 (находка F7 аудита 2026-07-29):
  `import → export` байт-идентичен для всех 71 документа `docs/` — frontmatter
  переиспускается дословно (`document.frontmatter_raw`), H1 восстанавливается
  из `document.title`, `preamble` отделён от первой секции. Пере-сериализация
  frontmatter включается только когда БД разошлась с файлом по
  `type/status/owner/sensitivity/source_of_truth/title`.
  Известные нормализации (не документы, а служебные файлы): если после
  закрывающего `---` не было пустой строки, она появится; отсутствующая пустая
  строка после заголовка секции тоже добавляется. Прежний чек-поинт —
  [audit/2026-06-05-doc-drift-source-of-truth.md](../audit/2026-06-05-doc-drift-source-of-truth.md).
- **Защита от перезаписи.** `doc export` отказывается писать поверх файла,
  который не совпадает ни с последним export'ом, ни с последним принятым
  import'ом (правка руками), и — на CLI/MCP — поверх чужого репозитория.
  `--dry-run` показывает unified diff, `--force-write` снимает обе защиты.
- **Совместимость схемы.** `restore` без проверки alembic-revision запрещён.
- **Секреты.** Бэкап может содержать `sensitivity: internal` контент — хранить
  как секрет, не коммитить в публичный репозиторий.

## 6. Связь с CI

Регулярный `cod-doc backup` можно завести как routine (cron) или CI-job; артефакт
кэшируется/выгружается так же, как описано в
[audit-and-ci.md §4.4](audit-and-ci.md).
