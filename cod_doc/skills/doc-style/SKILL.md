---
name: doc-style
description: |
  Стиль документации: язык, заголовки, гибридные ссылки, статусы.
  Триггеры: style, format, language, frontmatter, hybrid, link,
  ссылка, заголовок, проза.
---

# Skill — Documentation style

## Когда подгружается

Задачи, в которых **пишется или редактируется доковая проза** —
capability, audit, kickoff, README, HANDBOOK. Триггер-keywords:
`style`, `format`, `frontmatter`, `hybrid`, `link`, `ссылка`,
`заголовок`, `markdown`, `проза`, `документация`, `doc`.

## Язык

- **Проза — на языке проекта** (для cod-doc по умолчанию русский, если
  пользователь не сказал иное).
- **Идентификаторы** (поля, типы, status, имена сущностей и таблиц,
  task_id, doc_key) — **всегда на английском**, даже в русской прозе.
- Не смешивай транслит и кириллицу в идентификаторах.

## Заголовки

- `# Title` — один на документ, в начале (после frontmatter).
- `## N. Section` — нумерация для длинных доков; для коротких — без
  цифр.
- `### Subsection` — третий уровень. Глубже — редкое исключение.

## Гибридные ссылки

Формат:

```
📁 /path/to/file.ext | 🗃️ doc:sanitized_path | 🔑 sha:12hexchars
```

Полный формат — для записей в `MASTER.md` Validation Table (раздел 5.1).
Для inline — markdown-relative: `[label](relative/path.md)`.

Статусы (badge'и):

- `🟢 VERIFIED` — все три компонента согласованы.
- `🟡 DRAFT` — черновик, ещё не прошёл валидацию.
- `🟡 LEGACY` — корректный, но обзорный; canonical_source где-то ещё.
- `🔴 STALE` — хэш устарел.
- `🔴 BROKEN` — файл / doc-key отсутствует.

## Frontmatter

Каждый документ в `docs/system/` имеет YAML-frontmatter; обязательные
поля — `type`, `status`, `source_of_truth`, `owner`. Полная спецификация:
[standards/frontmatter.md](../../../docs/system/standards/frontmatter.md).

## Структура

- Не дублируй информацию между файлами. Если раздел нужен в двух доках —
  вынеси в отдельный markdown и ссылайся.
- Длинные таблицы → markdown-table; не псевдо-asciiart.
- Mermaid-диаграммы для зависимостей; код-блоки с ```mermaid.
- Списки — `- ` (не `*`); нумерованные `1. 2. 3.` (не `1) 2)`).

## Стиль абзацев

- Краткость > полнота. Один абзац — одна мысль.
- Не пиши "очень", "достаточно", "просто", "в общем" — обычно вода.
- Активный залог: «сервис пишет revision», не «revision записывается
  сервисом».

## Что НЕ делать

- Не сочинять примеры, которые не существуют в коде. Если ссылаешься на
  файл — проверь, что он есть (`check_stale_refs` или прямое чтение).
- Не вставлять ASCII-art из символов; используй mermaid для схем.
- Не оставлять `TODO` без owner'а и контекста.

## Связанное

- [standards/document-link.md](../../../docs/system/standards/document-link.md)
- [standards/frontmatter.md](../../../docs/system/standards/frontmatter.md)
- skill `audit-cadence` — при создании audit-report / kickoff.
