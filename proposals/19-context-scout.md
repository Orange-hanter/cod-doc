# 19 — Context-Scout: «умный grep» через cod-doc MCP

> Категория: 🟡 Адаптация · Риск: низкий · Зависимости: doc_search (FTS5), OBI-040 (FTS5), model_catalog

## Контекст: «а где у нас…?»

Vibecoder постоянно задаёт проекту вопросы, на которые `grep` отвечает плохо:

- «Где у нас считается food cost?» → `grep -r "food_cost" cod_doc/` покажет 30 строк, не объяснит, какая из них актуальна.
- «Почему мы перешли с YAML на SQLite для БД?» → нужен контекст ADR-001, не git log.
- «Какие задачи блокируют релиз?» → `task_list_blocked` есть, но без группировки по «почему заблокировано».

Существующие тулы cod-doc уже умеют искать по частям:
- `doc_search` — FTS5 по `docs/**`.
- `task_search` (через `task_list` + filters) — по tasks.
- `adr_list` / `adr_get` — по ADR.
- OBI-040 — unified FTS5 (repos + docs + tasks).

**Не хватает:** единой точки входа, которая **агрегирует** результаты из всех этих источников и возвращает **human-friendly summary**.

## Текущее состояние cod-doc

- FTS5 индексы: `docs` (`search_service`), `tasks`, `repo_index` (OBI-040), `code_ref`.
- MCP: `doc_search` (через `doc_list` + filters), `task_list`, `task_summary`, `adr_list`.
- `tool_search`, `tool_describe` — поиск по самим MCP-тулам.
- **Нет:** агрегатора, который один раз спрашивает все 4 источника и выдаёт ranked summary.

## Предложение

Создать `cod_doc/services/context_scout.py` + CLI-команду `cod-doc scout`:

### 4.1. Pipeline

```
scout(query="где считается food cost", project="mozarella", limit=10)
  ↓
  1. doc_search(query)              → top 5 docs (FTS5 ranked)
  2. task_search(query)             → top 5 tasks (status filter опц.)
  3. adr_search(query)              → top 3 ADRs
  4. code_ref_search(query)         → top 5 файлов (через OBI-040)
  ↓
  5. LLM-rerank (опц.): "вот 18 кандидатов, выбери top-5, дай 1-абзацный answer"
  ↓
  6. Return:
     {
       "answer": "...",                          # LLM-синтез
       "evidence": [
         {"type": "doc",   "id": "...", "title": "...", "snippet": "..."},
         {"type": "task",  "id": "COD-456", "status": "in_progress", "title": "..."},
         {"type": "adr",   "id": "ADR-003", "status": "ACCEPTED", "title": "..."},
         {"type": "file",  "path": "cod_doc/services/food_cost.py", "refs": 3},
       ],
       "cost": {"input_tokens": N, "output_tokens": M, "model": "..."}
     }
```

### 4.2. CLI

```bash
$ cod-doc scout "где считается food cost" --project=mozarella

📍 Answer (claude-sonnet-4-6, 1.2s):
   Food cost считается в `cod_doc/services/food_cost.py`, метод `calculate_dish_cost`.
   Использует таблицу `recipes` + `warehouse_stock`. Связанная задача COD-456 (in_progress),
   документация: docs/system/DATA_MODEL.md#recipes. ADR-005 определяет,
   что food cost должен учитывать depletion из QuickResto.

📂 Evidence (5):
   1. 📄 docs/system/DATA_MODEL.md (rank 0.92)
      "...table `recipes` is the canonical source for ingredient-level cost..."
   2. 🔧 COD-456 (in_progress, 3 days)
      "Add depletion-aware food cost calculation"
   3. 📐 ADR-005 (ACCEPTED)
      "QuickResto depletion flow → recipes → food_cost"
   4. 🗂️ cod_doc/services/food_cost.py (3 references)
   5. 📄 docs/handbook/mozarella/restaurant-ops.md (rank 0.71)
      "...food cost рассчитывается ежедневно в 23:00..."

💰 Cost: $0.0012 (1240 in / 380 out)

$ cod-doc scout "почему SQLite а не Postgres" --no-llm  # быстрый режим, без LLM-rerank
📂 Evidence (8):
   1. 📐 ADR-001 (ACCEPTED) "Single-tenant SQLite as canonical state"
   2. 📄 docs/system/ARCHITECTURE.md#storage
   ...
```

### 4.3. MCP-тул

```
scout(query, project?, limit=10, use_llm=true, model?) -> ScoutResult
```

### 4.4. Опции

| Флаг | Поведение |
|---|---|
| `--no-llm` | Без LLM-rerank, чистый FTS5 → ranked list |
| `--type=doc,task` | Фильтр по типам evidence (по умолчанию все 4) |
| `--status=in_progress` | Только задачи в этом статусе |
| `--json` | Машиночитаемый вывод (для пайплайнов) |
| `--since=7d` | Только недавние сущности |

## Эффект

| Метрика | До | После |
|---|---|---|
| Время на вопрос «где это в коде» | 5-15 мин (grep + ручной обход) | 5-15 сек (`scout` + 1 экран) |
| Контекст для LLM-агента | 1-2К токенов на ручной сбор | 1 вызов, structured response |
| Onboarding: «расскажи про проект» | день (читать MASTER.md) | 5 мин (10 запросов в scout) |

## Зависимости

| Компонент | Нужно для |
|---|---|
| `doc_search` (FTS5) | поиск по docs |
| `task_list` / `task_search` | поиск по задачам |
| `adr_list` | поиск по ADR |
| OBI-040 (`01ba3ab`) | unified FTS5 (repos + docs + tasks) |
| `model_catalog` (COD-059) | выбор LLM для rerank |
| agent_pick (`30fffed`) | pattern для project context resolution |

## Структура

```
cod_doc/services/
├── context_scout.py              # core: aggregate, rerank, format
├── context_scout_prompts.py      # LLM prompts
cod_doc/mcp/tools/
└── context_scout_tools.py        # MCP surface
cod_doc/cli/
└── scout.py                      # CLI: cod-doc scout
tests/services/
└── test_context_scout.py
```

## Риски и митигация

| Риск | Митигация |
|---|---|
| LLM-rerank галлюцинирует (упоминает несуществующий task) | Evidence-валидация: каждый `evidence.id` проверяется в БД перед возвратом. Если LLM сослался на несуществующее — fallback к FTS5-only с warning. |
| Latency (4 параллельных поиска + LLM) | Параллельный FTS5 (asyncio.gather); LLM-кэш на (query, project, 1h TTL). |
| Privacy: LLM видит task titles | `privacy=true` task'и фильтруются из evidence (как в proposal 18). |
| Разные FTS5 индексы используют разные токенизаторы | Нормализация запроса в scout: lowercase + trim + Unicode NFKC + длина min 2 символа. |

## Acceptance criteria

1. `cod-doc scout "..."` работает end-to-end, печатает Answer + Evidence + Cost.
2. `--no-llm` работает без LLM, latency < 1 сек на 100К docs.
3. LLM-rerank evidence проходит валидацию: все id существуют в БД.
4. `--json` режим возвращает машиночитаемый JSON для пайплайнов.
5. Cost tracking: реальное количество токенов (input/output) и стоимость в USD.
6. Privacy: task с `privacy=true` отсутствует в evidence.

## Альтернативы

- **`ripgrep + fzf + ручной обход`** — то, что делаем сейчас, не масштабируется.
- **Embeddings-based semantic search** — дороже, требует embedding-индекса, не использует существующий FTS5.
- **Просто встроить `grep` в CLI** — игнорирует структуру cod-doc, не использует ADR/task связи.

## Источники

- Cursor `@codebase` — pattern «задай вопрос проекту, получи ranked evidence».
- Sourcegraph Code Search — референс UX.
- Paperclip [`skill: paperclip-converting-plans-to-tasks`](https://github.com/paperclipai/paperclip) — узкий skill для одной задачи (наш scout = обратный skill: задача → где в коде).
