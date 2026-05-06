---
status: draft
type: ux-proposal
author: human:dakh
date: 2026-05-06
scope: web-ui · docs-import
related:
  - cod_doc/templates/web/project/docs_list.html
  - cod_doc/api/web/pages/docs.py
  - cod_doc/services/import_service.py
---

# Proposal 13 · Переработка импорта документов

> 🎯 Цель: убрать ручной ввод `doc_key` и однофайловую загрузку, заменить
> на сканирование папки + выбор из списка. Минимизировать текстовый ввод
> везде, где это ещё не сделано.

## 1. Что не так сейчас

[`docs_list.html:128-162`](cod_doc/templates/web/project/docs_list.html#L128-L162) и
[`docs.py:439-496`](cod_doc/api/web/pages/docs.py#L439-L496):

| Симптом                                                                   | Причина                                                                         |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Кнопка «📥 Import markdown» в верхней панели не работает                  | `href="#import-section"` — якорь есть, но `<details>` не открывается, скролл уходит «в никуда» |
| Поле `Doc key` (`modules/M1-foo/overview`) — пользователь должен знать схему ключей | Импорт устроен «один файл за раз», ключ = ручной ввод путь-как-строка           |
| Импорт по одному файлу через `<input type="file">`                        | Нет батч-режима. Чтобы залить 30 модулей — 30 кликов                            |
| `Type` дублируется с тем, что обычно лежит в frontmatter `type:`           | Лишний контрол при том, что parse_markdown уже умеет читать FM                   |
| Подсказка «или: `cod-doc doc import …`» появляется при каждом импорте     | Учит CLI вместо того, чтобы делать UI самодостаточным                            |

И сквозная боль (см. user feedback): по проекту слишком много мест, где
человек печатает строки руками вместо выбора из списка.

## 2. Предлагаемая модель

### 2.1. Источник правды — папка проекта, не одиночный upload

Пользователь указывает **папку** (один раз, в Settings проекта или при
импорте drag-n-drop'ом). Сервер строит **manifest** — индекс всех `.md`
файлов с их frontmatter. Сравнивает с тем, что уже в БД. Показывает
diff: «новые / изменённые / удалённые / без изменений».

### 2.2. Сканирование без LLM

Никаких AI-проходов. Достаточно того, что parser уже умеет:

- `parse_markdown()` из [`import_service.py`](cod_doc/services/import_service.py)
  → frontmatter + H1 + sections.
- Чтение всех `*.md` в папке (`os.walk` + `.gitignore`-aware фильтр).
- Содержимое грузим **только при импорте**; для индекса достаточно
  `(path, mtime, sha256(head_4kb), frontmatter_dict, h1_title)`.

`doc_key` выводится автоматически:
- если в frontmatter есть `doc_key:` — берём его;
- иначе путь относительно root, без `.md`, без префикса `docs/`
  (`modules/M1-foo/overview.md` → `modules/M1-foo/overview`).

То есть **поле «Doc key» исчезает из формы** в 95% случаев. Остаётся
только как «advanced override» в раскрытой детали строки.

### 2.3. UI — список с чекбоксами

```
┌─ Import from folder ─────────────────────────────────────────┐
│  📁 docs/  · 47 .md files  [Rescan]                          │
│                                                              │
│  [✓] Select all new (12)   [ ] Select all changed (3)        │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ ✓ NEW       modules/M1-foo/overview     module-spec  │    │
│  │ ✓ NEW       modules/M2-bar/overview     module-spec  │    │
│  │ □ CHANGED   architecture/decisions      adr          │    │
│  │ ─ UNCHANGED roadmap/q3-2026             roadmap      │    │
│  │ ⚠ MISSING   modules/M0-old/overview     (was active) │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  [Import 12 selected]    [Cancel]                            │
└──────────────────────────────────────────────────────────────┘
```

Каждая строка — кнопка (вся строка clickable toggle). Никаких текстовых
полей. Тип проставляется из frontmatter; если не указан — выбор из
dropdown в развёрнутой детали (но не обязательное действие — есть
дефолт).

### 2.4. Backend — endpoints

- `GET /p/{slug}/docs/import/scan?path=...` → JSON manifest:
  `[{path, doc_key, type, title, status: "new"|"changed"|"unchanged"|"missing", reason}]`.
- `POST /p/{slug}/docs/import/apply` с телом `{paths: [...]}` — импорт
  выбранных. Идемпотентный: changed → новая ревизия, unchanged → noop.
- Существующий `POST /p/{slug}/docs/import` оставить как backward-compat
  для CLI/REST, но из UI не использовать.

### 2.5. Источник папки

Три варианта по убыванию автоматизма:
1. **Auto** — папка задаётся в Settings проекта (`project.docs_root`,
   относительно git-root). Самый частый кейс.
2. **One-shot** — drag-n-drop папки в браузер. Использует
   `webkitdirectory` или File System Access API.
3. **Path input** — текстовое поле как fallback, скрыто под «advanced».

## 3. Минимизация ввода — общий принцип проекта

Сделать пройденным мерилом для каждой формы вопрос: «можно ли
заменить input на select / chip / drag-n-drop / checkbox?». Кандидаты
на следующий заход (вне scope этого предложения, но в одном русле):

- Создание задачи (`task_create`): `module`, `priority`, `assignee` —
  всё селекты, но `title` ещё руками. Можно предлагать шаблоны.
- New blank doc: `doc_key` тоже руками. Можно складывать его из
  `(folder picker, title input)` где folder = выбор из существующих.
- Filter bar — уже хорошо.

## 4. Шаги внедрения

1. Починить кнопку «Import markdown» в шапке (открывать `<details>` JS'ом
   при клике на anchor) — чтобы не оставлять её сломанной до большой
   переделки. **Маленький отдельный COD-task.**
2. Завести `project.docs_root` (миграция + поле в Settings).
3. Добавить `services/import_service.scan_folder()` — чистая функция,
   возвращает manifest без записи в БД.
4. Endpoint `GET /docs/import/scan` + страница `/docs/import` с чекбокс-списком.
5. Endpoint `POST /docs/import/apply` (батч) + переиспользует
   `import_markdown()` per-file внутри одной транзакции.
6. Заменить старую форму в `docs_list.html` ссылкой «Bulk import →».
7. Удалить старую `<details>` после миграции существующих flow.

## 5. Открытые вопросы

- **Что делать с MISSING?** (файл удалили в FS, но в БД он active.)
  Варианты: ничего не делать (decoupled storage), пометить `deprecated`,
  показать как warning без действия по умолчанию. → склоняюсь к
  warning-only, действие через отдельную кнопку.
- **Конфликт `doc_key`** между двумя файлами с одинаковым
  frontmatter `doc_key:` — показывать ошибку в строке manifest, импорт
  блокировать.
- **Sub-projects** — если `docs_root` содержит вложенные проекты со
  своими `.cod-doc/`, их пропускать.
