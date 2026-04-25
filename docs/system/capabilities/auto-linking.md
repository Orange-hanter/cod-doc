---
type: capability
scope: auto-linking
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../standards/document-link.md
  - ../DATA_MODEL.md
---

# Capability — Auto-Linking

> Поиск, резолвинг и поддержание ссылок без ручной работы.

## 1. Что автоматизируется

### 1.1 Резолвинг существующих ссылок
Любая ссылка в markdown тела секции парсится и разрешается против БД. Результат — в `link` (см. [DATA_MODEL.md §3.4](../DATA_MODEL.md)).

### 1.2 Выявление potential-links
Когда в тексте упомянут модуль / задача / документ без явной ссылки, COD-DOC предлагает превратить упоминание в ссылку.

Паттерны:

| Паттерн | Трактовка |
|---------|-----------|
| `AUTH-025` вне кода | → `[[task:AUTH-025]]` |
| `M1 AUTH` / `M1-auth` в тексте | → `[[doc:modules/M1-auth/overview]]` |
| `US-014` вне кода | → `[[story:US-014]]` |
| Имя документа (по `title`) | → кандидат в canonical ref |

Предложения показываются в `cod-doc audit --linkable`, но не применяются автоматически без подтверждения.

### 1.3 Поддержка при переименовании
Rename документа / задачи — все входящие ссылки обновляются атомарно (см. [standards/document-link.md §5](../standards/document-link.md)).

### 1.4 Верификация битости
Регулярный job `cod-doc link verify`:

- Проходит всю таблицу `link`.
- Внутренние ссылки — пытается перерезолвить.
- Внешние URL (при флаге `--external`) — HTTP HEAD с таймаутом.
- Результат в `link.resolved` / `link.broken_reason` / `link.last_checked`.

## 2. Индексация

При любом patch `DocService`:

1. Выделяет ссылки из body через markdown-AST (remark) + regex для wiki/canonical-ссылок.
2. Diff vs предыдущий набор → insert/update/delete в `link`.
3. Резолвит новые ссылки через `LinkService.resolve`.
4. Кэширует результат.

## 3. Алгоритм резолвинга

```text
INPUT: raw_link, from_section
1. Parse форму (canonical|wiki|markdown|task|story|url)
2. По форме → стратегия:
   canonical → doc_key exact
   wiki      → doc_key exact → title exact → fuzzy (Lev ≤ 2) → None
   markdown  → normalize(from_section.path, raw.path) → document.path exact → None
   task      → task_id exact → None
   story     → story_id exact → None
   url       → skip (только verify по запросу)
3. Если resolved → set link.resolved=1 и target-поля
4. Иначе → link.resolved=0, link.broken_reason="not-found"
```

Fuzzy-match требует подтверждения: при импорте — warning; при auto-suggestion — показывается как кандидат.

## 4. Cascade update при переименовании

Процедура:

```python
def rename(doc, new_doc_key):
    with tx():
        old_key = doc.doc_key
        doc.doc_key = new_doc_key
        # update входящих ссылок
        for link in Link.query.filter_by(to_doc_key=old_key):
            link.to_doc_key = new_doc_key
            affected_sections.add(link.from_section)
        # revision на документе
        Revision.create(entity_kind='document', entity_id=doc.row_id,
                        diff=frontmatter_diff, reason=f"rename {old_key}→{new_doc_key}")
        # revision на каждой затронутой секции (body не меняется,
        # но canonical-ref в рендере другой — поэтому diff проекции)
        for section in affected_sections:
            refresh_projection(section)
```

Gotcha: если входящая ссылка была написана как markdown-relative, body-текст тоже обновляется (потому что path изменился) — там уже полноценный diff.

## 5. Graph queries

На базе `link` доступны готовые запросы:

- **Обратные ссылки** (`cod-doc link incoming <doc>`) — кто на меня ссылается.
- **Исходящие** (`cod-doc link outgoing <doc>`) — куда я ссылаюсь.
- **Осиротевшие документы** (`cod-doc audit --orphans`) — `source_of_truth=true`, но входящих 0 (кроме root MASTER и NAVIGATION).
- **Кластер доков** (`cod-doc graph cluster --around <doc>`) — BFS по `link` до глубины `N`.

## 6. MCP поверхность

| Tool | Операция |
|------|----------|
| `link.list_broken` | Список битых ссылок с причинами |
| `link.incoming` | Обратные ссылки |
| `link.outgoing` | Прямые |
| `link.suggest` | Для куска текста вернуть потенциальные автоссылки |

## 7. Запрет тихой автозамены

COD-DOC **не перекладывает фразы в ссылки без согласования**. Аргумент: ложное срабатывание (упомянули «auth» в общем смысле) создаёт мусорные ссылки. Автозамена делается только:

- При явной команде `cod-doc link autofix <doc>`.
- Через MCP `link.apply_suggestions(ids=[...])`.

## 8. Валидация при записи

Write-path для `DocService.patch_section` включает hard-check:

- Новая ссылка, которая не резолвится, но явно задана автором → error (с сообщением «target-документ не существует; создайте или исправьте»).
- Ссылка на задачу, которой нет в БД → error.
- Ссылка на anchor, которого нет → error.

Это главный страхующий механизм от «тихого распада документации».

## 9. Обработка Obsidian-специфики

- `[[Document Name]]` парсится как wiki-link.
- `![[Document Name]]` (transclusion) — поддерживается при export: рендерится как цитата из целевого документа.
- Алиасы (`[[Doc|alias]]`) — сохраняются при экспорте, не теряются при rename.

## 10. Интеграция с code-ссылками

Помимо doc-ссылок, `related_code` / `implemented_in` фронтматтера — это ссылки на код. COD-DOC:

- Проверяет существование путей при `audit`.
- Ставит warning `code-drift`, если ref устарел.
- Может обновлять массив из workspace-map (аналог Restate `Docs/workspace-map.yaml`), если код-ссылка указана как prefix.

## 11. Производительность

- Таблица `link` индексирована по `to_doc_key`, `to_task_id`, `resolved`.
- Полный `link verify` на проект уровня Restate (~500 docs, ~4000 links) укладывается в несколько секунд — это чистый SQL.
- Внешние URL — отдельно и асинхронно, не блокирует write-path.
