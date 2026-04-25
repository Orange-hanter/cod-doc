---
type: capability
scope: context-retrieval
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../ARCHITECTURE.md
  - ../DATA_MODEL.md
---

# Capability — Concentrated Context Retrieval

> Получение «минимально достаточного» контекста проекта по запросу. Замена ручному «прочитай весь Docs/obsidian/Modules/…».

## 1. Зачем

Агент, начинающий сессию, в Restate тратит огромный бюджет на чтение больших файлов (`Architecture.md` 17K, `SYSTEM_OVERVIEW.md` 73K). Это известная боль (см. `AGENT_START.md`, Snowball Protocol). COD-DOC решает её **структурно**:

- Документ в БД — это набор секций, каждая с anchor, title, body и контекстом (tags, links, story-связи).
- Запрос на контекст возвращает JSON + цитатные фрагменты, подобранные под заданный token budget.
- Нет «прочитать всё и выкинуть 90%».

Идейная близость: Restate `MASTER.md` `context_depth: L0/L1/L2` формализован в сервисе.

## 2. Depth-уровни

| Level | Что включено | Когда |
|-------|--------------|-------|
| `L0` | MASTER + явный target документ (metadata only) | Старт сессии, высокоуровневый обзор |
| `L1` | L0 + body target + прямые связи (module spec, открытый task-plan, ≤ 3 open questions, ≤ 3 user stories) | Работа внутри одного модуля |
| `L2` | L1 + `depends_on`-цепочки, cross-module dependencies, соседние стандарты | Глубокая работа с границами |
| `L3` | L2 + semantic-search top-k по всему корпусу | Только по явному запросу; дорогой |

## 3. Контракт `context.get`

```json
{
  "target": {
    "kind": "module" | "document" | "task" | "story" | "plan",
    "id":   "M1-auth" | "modules/M1-auth/overview" | "AUTH-025" | "US-014" | "M1-auth-module"
  },
  "depth": "L0" | "L1" | "L2" | "L3",
  "token_budget": 8000,
  "formats": ["json", "markdown"]
}
```

Ответ:

```json
{
  "target_summary": { "title": "...", "status": "...", "owner": "..." },
  "core": {
    "master_excerpt": "...",
    "target_body": "...",
    "sections": [{ "anchor": "data-model", "heading": "...", "excerpt": "..." }]
  },
  "related": {
    "documents": [{ "doc_key": "...", "title": "...", "why": "spec" }],
    "tasks":     [{ "task_id": "AUTH-025", "status": "pending", "why": "open" }],
    "stories":   [{ "story_id": "US-014", "why": "linked" }],
    "dependencies": [...]
  },
  "hints": {
    "next_best_reads": ["..."],
    "open_questions": ["..."]
  },
  "meta": {
    "depth": "L1",
    "tokens_used": 6412,
    "truncated": false,
    "generated_at": "2026-04-19T..."
  }
}
```

## 4. Алгоритм сборки (L1)

1. Резолв target.
2. Pull target body + frontmatter.
3. Если target — `module` или `document`:
   - включить plan-progress: Progress Overview (из `plan_totals`), Next Batch (из `ready_tasks`).
   - включить open questions для модуля (документ с type=`guide`, tag=`open-questions`).
   - включить ≤ 3 user stories, linked через `story_link`.
4. Если target — `task`:
   - include section body + верхние 2 задачи depends_on + 2 reverse-dependents.
5. Сборка ответа, compress-stages:
   - a. Полный body target-секций (не урезается).
   - b. Для связанных документов — `summary` field или первые 600 символов.
   - c. Если бюджет превышен — секции связанных сортируются по tag-match, убираются с хвоста.
6. Меткой `truncated: true` маркируется, если что-то обрезано.

## 5. Semantic search (L3)

Используется только по явному запросу.

- Embeddings хранятся per-section (OpenAI/Anthropic-compatible или local-BGE, выбор — в конфиге).
- Индекс обновляется асинхронно по событию `RevisionCommitted`.
- Запрос возвращает top-k секций (default k=5) с distances и excerpts.
- Не ходит в LLM-провайдер «на ходу» — всё локально через `sqlite-vss` / `pgvector`.

Идея скопирована у Restate `tools/lightrag`, но без внешнего сервиса — встроенный индекс сохраняет целостность.

## 6. Поверхности

| Поверхность | Команда |
|-------------|---------|
| CLI | `cod-doc context get --target module:M1-auth --depth L1 --budget 8000` |
| MCP | `context.get(...)` — основной интерфейс для агентов |
| REST | `GET /api/v1/context?target=...&depth=...` |

## 7. Результат, пригодный для LLM-prompt

Отдельная команда `cod-doc context prompt --target module:M1-auth --depth L1`:

- Возвращает готовый markdown, где секции помечены заголовками `# Target`, `# Related Docs`, `# Task Progress`.
- Формат стабильный → агент умеет парсить.

## 8. Кэширование

- Ответ `context.get` кэшируется по `(target, depth, content_hash)`. Cache invalidation — при появлении новой revision на любом included объекте.
- TTL можно отключить в конфиге (`cache.context.ttl`), в embedded-профиле кэш по умолчанию — on-disk sqlite KV.

## 9. Интеграция с Snowball Protocol (наследие cod-doc)

Текущий `MASTER.md.j2` из cod-doc описывает уровни L0-L2 декларативно. В новом дизайне:

- MASTER по-прежнему существует и рендерится из БД.
- Но его «context_depth» больше не нужно читать руками — агент зовёт `context.get(depth=...)`, и ответ уже соответствует декларативному протоколу.
- Поле `MASTER.meta.context_depth` остаётся — для совместимости и как отображение того, что сейчас загружено в последнюю сессию.

## 10. Пример использования агентом

Запрос: «добавить rate-limit на logout в M1 AUTH».

```
1. context.get(target=module:M1-auth, depth=L1)
   → target_body: overview; sections: api-specification, auth-lifecycle;
     related.tasks: 3 open (AUTH-022 open, AUTH-024 pending, AUTH-050 tests);
     related.stories: US-014.
2. task.create(plan=M1-auth-module, title="Implement: logout rate-limit",
               type=feature, depends_on=[AUTH-022])
3. doc.patch_section(doc=modules/M1-auth/overview, anchor=api-specification,
                     new_body=... rate-limit mention...)
```

Всё — без чтения 70K байт и без ручного выбора «что релевантно».

## 11. Гарантии

- Respuesta не включает контент других проектов, если target не cross-project.
- Respuesta не включает ссылок на приватные секции (`audience: [internal]`) если вызов — `mcp:<external-client>`.
- Size bound: если `truncated: true`, `meta.missing_hints[]` подсказывает, какие слоты выкинуты; агент может запросить их явным follow-up.
