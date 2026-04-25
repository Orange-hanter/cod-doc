---
type: standard
scope: document-link
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../capabilities/auto-linking.md
  - ../DATA_MODEL.md
---

# Document Link Standard

> Формат ссылок в markdown-проекции и правила резолвинга в БД.

## 1. Поддерживаемые формы

| Форма | Пример | Когда |
|-------|--------|-------|
| **Canonical ref** | `[[doc:modules/M1-auth/overview]]` | Преферред; независим от реальных путей |
| **Wiki-link** | `[[M1 AUTH v2]]` | Для совместимости с Obsidian / Restate |
| **Markdown relative** | `[overview](../modules/M1-auth/overview.md)` | Для совместимости с GitHub viewer |
| **Task ref** | `[[task:AUTH-025]]` | Жёсткая ссылка на задачу |
| **Story ref** | `[[story:US-014]]` | Жёсткая ссылка на историю |
| **Section ref** | `[[doc:modules/M1-auth/overview#data-model]]` | Ссылка на конкретный anchor |
| **URL** | `https://...` | Внешние; не резолвятся COD-DOC, только проверка достижимости (опционально) |

## 2. Canonical ref — преферред формат

`[[doc:<doc_key>]]`, где `doc_key` берётся из БД (`document.doc_key`) и не зависит от реального пути в проекции.

- Переименовал документ — `doc_key` менять необязательно (смысл не меняется), а если менять, COD-DOC атомарно обновляет все входящие ссылки.
- Новый экспорт генерирует canonical ref в виде relative-markdown-ссылки для читаемости на GitHub.

## 3. Резолвинг

Сервис `LinkService.resolve(link)`:

1. **Canonical ref** → прямой lookup `document` по `doc_key`.
2. **Wiki-link** → lookup по `title` / `doc_key` (exact match), с fallback на fuzzy (Levenshtein ≤ 2) и предупреждением.
3. **Markdown relative** → нормализация пути → lookup по `document.path`.
4. **Task / Story ref** → lookup по `task_id` / `story_id`.

Результат пишется в `link.resolved` + `link.to_doc_key` / `to_task_id` / `to_story_id`.

## 4. Битые ссылки

Ссылка считается битой, если:

- Target не найден.
- Target имеет `status=deprecated` и не имеет `canonical_source`.
- Target — это section-ref на несуществующий anchor.

Битая ссылка фиксируется с `broken_reason`. Не удаляется.

Политика:

- Hard error на write-path, если автор явно добавил новую битую ссылку.
- Warning в `cod-doc audit`, если ссылка сломалась после рефакторинга target-документа (редкий случай, должен ловиться автолинковкой).

## 5. Обновление при переименовании

Когда `DocService.rename(doc_key, new_doc_key)`:

1. Транзакционно апдейтятся все `link.to_doc_key`.
2. Регенерируется markdown-проекция для всех затронутых документов.
3. Пишется по одной `revision` на каждую секцию с изменённым body.

Это одна транзакция. Ручные правки не требуются.

## 6. Section anchors

- Anchor — kebab-case от heading (`## Data Model` → `data-model`).
- Дубликаты auto-disambiguate: `data-model`, `data-model-1`, …
- Хранится в `section.anchor`; выгружается в markdown как явный `<a id="..."></a>` не нужен — читатели GitHub и Obsidian умеют.

## 7. Внешние URL

- Не резолвятся автоматически (нет сетевых запросов на write-path).
- По запросу `cod-doc link verify --external` — фоновая проверка HTTP-статуса. Результат пишется в `link.last_checked` / `link.broken_reason`.

## 8. Cross-project ссылки

Когда в проекте A нужна ссылка на документ проекта B (managed тот же COD-DOC):

```
[[doc:project:restate/modules/M1-auth/overview]]
```

Резолв через запрос к таблице `document` с `project_id` по slug.

## 9. Запрещённые практики

- Жёстко вбитые абсолютные пути (`/Users/...`) — error.
- Длинные цепочки `../../../` — warning (предложить canonical ref).
- Плейнтекстовые упоминания документов без ссылок — предупреждение при audit (автолинковка может предложить превратить в ссылку, см. [capabilities/auto-linking.md](../capabilities/auto-linking.md)).

## 10. Хранение

Все ссылки — в таблице `link` (см. [DATA_MODEL.md §3.4](../DATA_MODEL.md)). Ссылка принадлежит `section`, а не `document`, чтобы понимать местоположение точно.

## 11. Примеры

```markdown
Смотри [[doc:modules/M1-auth/overview]] и задачу [[task:AUTH-025]].

История [[story:US-014]] полностью покрыта секцией
[[doc:modules/M1-auth/overview#api-specification]].

GitHub-friendly форма (генерируется для проекции):
см. [Auth Overview](../modules/M1-auth/overview.md) и [AUTH-025](../plans/M1-auth/tasks/section-c-lifecycle.md#auth-025).
```
