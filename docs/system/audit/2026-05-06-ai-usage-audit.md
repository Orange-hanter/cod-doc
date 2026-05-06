---
type: audit-report
scope: AI/LLM-использование в COD-DOC (orchestrator + ai_generate + ai_text + MCP)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-06
last_updated: 2026-05-06
audit_target_revision: HEAD = b07a97e (post WEB-013..014/COD-070..079, daemon UI)
related_docs:
  - ../MASTER.md
  - ../capabilities/agents-and-skills.md
  - ../capabilities/context-retrieval.md
  - 2026-05-06-cli-vs-web-parity.md
related_code:
  - cod_doc/agent/
  - cod_doc/services/ai_generate.py
  - cod_doc/services/ai_text.py
  - cod_doc/services/context_service.py
  - cod_doc/services/trace_service.py
  - cod_doc/services/model_catalog.py
  - cod_doc/api/web/pages/docs.py
  - cod_doc/api/web/pages/stories.py
  - cod_doc/api/web/fragments/tasks_fields.py
---

# AI usage — System Audit (2026-05-06)

> Полный inventory всех точек, где COD-DOC обращается к LLM либо отдаёт
> tools для внешних AI-агентов. Цель — зафиксировать поверхность,
> ограничения и боли пользователя; в §6 предложены направления развития.

## 0. TL;DR

- **8 AI-поверхностей** (orchestrator + 6 generation/improve flow + embeddings).
- **Один провайдер** — OpenRouter (OpenAI-compatible API). Прямых вызовов
  на `api.anthropic.com` нет.
- **Default-модель** — `anthropic/claude-sonnet-4-6`, переключаема через
  [config.py:64](../../../cod_doc/config.py).
- **Promp caching отсутствует** во всех вызовах — даже при повторной отправке
  MASTER.md и больших system-prompts. Главный источник потенциальной экономии.
- **Retry only в orchestrator-loop** ([retry.py](../../../cod_doc/agent/retry.py));
  все `ai_generate`/`ai_text` вызовы — single-shot try/except.
- **Cost tracking отсутствует**: токены пишутся в `trace_call`, но не
  агрегируются в дашборд и не превращаются в $.
- **Streaming только в orchestrator** через WebSocket
  [api/websocket.py](../../../cod_doc/api/websocket.py); генерация stories/tasks/docs/improve —
  blocking, без HTMX-progress.
- **Human-in-the-loop**: есть инструмент `ask_human` (агент может спросить),
  но нет обязательной approve-стадии в орхестраторе. Web-flow approve через
  preview→save (хорошо), CLI/daemon flow approve опционален.

## Сводка

| Severity | Count | Описание |
|---|---:|---|
| critical | 0 | — |
| high | 4 | Нет prompt caching; нет cost tracking; retry на gen-flows; UX блокирующих ожиданий |
| medium | 6 | Streaming генерации; provenance AI-output; budget guard; embeddings не интегрированы; rate-limit UX; context-degrade видимость |
| low | 3 | Каталог моделей вручную; deprecated-version detection; cost prediction до запуска |
| **итого** | **13** | — |

---

## 1. Inventory: где AI вызывается

### 1.1 Orchestrator loop (главный AI-runner)

**Где:** [cod_doc/agent/orchestrator.py:21,70-87,407-415](../../../cod_doc/agent/orchestrator.py),
[prompts.py](../../../cod_doc/agent/prompts.py), [tool_defs.py](../../../cod_doc/agent/tool_defs.py),
[tools.py](../../../cod_doc/agent/tools.py), [retry.py](../../../cod_doc/agent/retry.py).

| Аспект | Значение |
|---|---|
| Клиент | `AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key)` |
| Модель | `cfg.model` (default `anthropic/claude-sonnet-4-6`) |
| System prompt | `prompts.py` — Snowball Protocol, fail-fast, гибридные ссылки |
| Tools | `read_file`, `write_file`, `calc_hash`, `get_context`, `get_project_status`, `create_task`, `update_task`, `story_get`, `story_link`, `ask_human`, `search_docs`, `reindex` |
| Retry | `with_retry` (4 попытки, exp backoff, jitter) для transient (RateLimit/5xx/Connection) |
| Context-degrade | 3 ступени (`no_master` → `refs_only` → `minimal`) при `context_length_exceeded` |
| Budget | Soft cap `cfg.max_context_tokens` (default 100K), мягкое урезание перед запросом |
| Trace | `trace_service.record(kind="chat", task_id, input_tokens, output_tokens, tool_calls)` |
| Stream → UI | `event_bus.publish(slug, ...)` → WebSocket `/ws/projects/{slug}` (TUI/Web) |

**Ограничения:**
- Один монолитный system prompt, без `cache_control` блоков → каждый
  iteration платит за весь prompt заново.
- `with_retry` не различает `RateLimit-with-retry-after` vs generic 429:
  использует один и тот же `2^attempt` backoff, что может вызвать ранний
  rebound при честном rate-limit от OpenRouter.
- `context_length_exceeded` лестница умна, но **не сохраняет, какой level
  был достаточен**: следующая итерация снова стартует с L0 и может снова
  получить 429.
- Tool execution (`ToolExecutor.execute`) не имеет timeout — медленный
  `read_file` на большом файле блокирует loop.

### 1.2 Generation pipelines (AI-genrate в Web)

**Где:** [cod_doc/services/ai_generate.py](../../../cod_doc/services/ai_generate.py),
вызывается из [api/web/pages/docs.py](../../../cod_doc/api/web/pages/docs.py),
[api/web/pages/stories.py](../../../cod_doc/api/web/pages/stories.py).

| Функция | Цель | System prompt | Endpoint |
|---|---|---|---|
| `generate_stories` | Из подборки docs → JSON список stories | `_STORY_SYSTEM_PROMPT` | `POST /p/{slug}/stories/generate` |
| `generate_tasks_for_story` | Story → 2-4ч задачи | `_TASK_SYSTEM_PROMPT` | `POST /p/{slug}/stories/{id}/tasks/generate` |
| `generate_doc_from_sources` | N docs → новый документ | `_DOC_SYSTEM_PROMPT` | `POST /p/{slug}/docs/generate` |
| `generate_master_from_folder` | Скан папки → MASTER.md draft | `_MASTER_SYSTEM_PROMPT` | используется в `import_master/scan` |

Все 4 функции делят helper `_chat_json` ([ai_generate.py:305-348](../../../cod_doc/services/ai_generate.py#L305)):

```python
client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
completion = client.chat.completions.create(
    model=cfg.model,
    messages=[{"role": "system", ...}, {"role": "user", ...}],
    max_tokens=cfg.max_tokens,
    response_format={"type": "json_object"},
)
```

**Ограничения** (все четыре flow):
- **Single-shot try/except** — никакого retry. Один RateLimit → AIBackendError → красная плашка в UI.
- **Sync API** (`OpenAI`, не `AsyncOpenAI`) → блокирует event loop FastAPI на
  длительные генерации (наблюдаемо: 10-30 секунд на crowded MASTER.md).
- **Нет streaming** — пользователь ждёт молча. Только спиннер HTMX.
- **JSON-mode без strict schema validation на стороне модели**: парсинг
  через `json.loads` + ручные `_coerce_story/task/...`. На малых моделях
  иногда возвращается prose поверх JSON → AIBackendError.
- **User-message обрезается до 60K символов** в `generate_stories` —
  для больших проектов выходит за scope без warning'а.
- **Нет prompt cache** (даже когда тот же набор docs шлётся повторно).

### 1.3 `improve_text` (inline AI-редактура полей)

**Где:** [cod_doc/services/ai_text.py:48-104](../../../cod_doc/services/ai_text.py),
endpoint [tasks_fields.py:199](../../../cod_doc/api/web/fragments/tasks_fields.py).

```bash
POST /p/{slug}/tasks/{task_id}/fields/{field}/improve
```

| Аспект | Значение |
|---|---|
| System prompt | `_SYSTEM_PROMPT` — senior technical editor, сохраняет markdown + язык |
| User message | `intent` (что изменить) + `text` (исходный) |
| Trace | `trace_service.record(kind="improve", ...)` |
| UI | Возвращает HTMX-фрагмент с suggested-text, пользователь принимает/отклоняет |

**Ограничения:**
- Никакого diff-вью: пользователь видит готовый текст, но не **что
  именно** изменилось. Принять = переписать всё одним блоком.
- Нет сохранения истории intent'ов — каждый раз пишешь «сделай короче»
  заново.
- Тот же синхронный sync OpenAI client → блокирует event loop.

### 1.4 Generate tasks from MASTER (autonomous mode)

**Где:** [orchestrator.py:521-567](../../../cod_doc/agent/orchestrator.py).

Daemon-loop при наличии `daemon_enabled=true` берёт первые ~4000 символов
MASTER.md, формирует **inline prompt** и просит LLM создать задачи через
ограниченный tool-set (`create_task`, `get_project_status`).

**Ограничения:**
- **Inline prompt без шаблона** — нет version control, нельзя A/B тестировать.
- **Hard-coded 4000 символов** обрезки MASTER — для крупных проектов
  половина контекста теряется без warning'а.
- Нет idempotency: повторный запуск может создать дубликаты задач, кроме
  как через `task_find_duplicate` (MCP-only utility).

### 1.5 Embeddings (опционально)

**Где:** [cod_doc/services/](../../../cod_doc/services/) (ChromaDB),
конфиг в [config.py:87-103](../../../cod_doc/config.py).

| Аспект | Значение |
|---|---|
| Backend | `embedding_backend` ∈ {`openai` (default), `local` sentence-transformers} |
| Модель | default `openai/text-embedding-ada-002` |
| Хранилище | ChromaDB на диске (`cfg.chroma_path`) |
| Tools | `search_docs(query)`, `reindex()` — доступны агенту через `tool_defs.py` |

**Ограничения:**
- **Не интегрировано в основной loop** — orchestrator не делает retrieval
  автоматически перед LLM-вызовом. Доступно только если LLM сам решит
  вызвать `search_docs`.
- **Нет hook'ов на изменения** — индекс обновляется руками через
  `reindex()`. После `doc_create`/`section_patch` Chroma не знает.
- Default `text-embedding-ada-002` — устаревшая модель (2022), точность
  заметно ниже `text-embedding-3-small`/`-large` (2024) при том же ценнике.

### 1.6 MCP server (внешние AI потребляют наши tools)

**Где:** [cod_doc/mcp/](../../../cod_doc/mcp/), запуск `cod-doc mcp`.

Это inverse-направление: **наши инструменты экспортируются** во внешний
MCP-клиент (Claude Code, Claude Desktop), который сам решает, когда их
звать. Мы здесь не платим за токены — платит хост-агент.

**Ограничения** (с т.з. нашего проекта):
- Нет per-tool authorization model — клиент имеет полный доступ к доменной
  модели проекта. Для multi-tenant сценария недостаточно.
- `tools/list` отдаётся целиком (~50 tools); большие LLM это поглощают,
  но мелкие модели путаются в выборе.

---

## 2. Конфигурация и управление ключами

| Поле | Default | Где | UI |
|---|---|---|---|
| `api_key` | — (required) | [config.py:58](../../../cod_doc/config.py) | `/settings` (password input) |
| `base_url` | `https://openrouter.ai/api/v1` | config.py:60 | `/settings` |
| `model` | `anthropic/claude-sonnet-4-6` | config.py:64 | `/settings` (dropdown + custom) |
| `max_context_tokens` | 100_000 | config.py:68-74 | `/settings` |
| `max_tokens` | output cap | config.py | — |
| `embedding_backend` | `openai` | config.py:88-95 | `/settings` |
| `embedding_model` | `openai/text-embedding-ada-002` | config.py:96-103 | `/settings` |
| `agent_enabled` | false | config.py | `/settings` |
| `agent_interval` | (sec) | config.py | `/settings` |

Хранится в `~/.cod-doc/config.yaml` либо в env (`COD_DOC_*`).

**Ограничения:**
- Один глобальный `api_key` на все проекты — нельзя выставить разные
  тарифные ключи для prod-data и sandbox.
- API-ключ читается plain-text из `config.yaml` (perms 0600 не enforce'ятся).
- **Нет валидации** ключа на сохранении: ошибка вылезет только при первом
  AI-вызове.

---

## 3. Каталог моделей и провенанс

[model_catalog.py](../../../cod_doc/services/model_catalog.py) описывает
8 моделей: Sonnet 4.6 / Opus 4 / Haiku 4.5, GPT-5, GPT-4.1-mini,
Gemini 2.5 Pro, DeepSeek R1, Llama 3.3-70b. Используется только для
dropdown в Settings, **не валидирует** выбранную модель.

**Provenance AI-output в БД:**

| Сущность | Признак «AI-сгенерировано» |
|---|---|
| Document | `frontmatter.generated_from = [source_doc_keys]` (только для `/docs/generate`) |
| Story | `Revision.reason = "ai-generate"` |
| Task | `Revision.reason = "ai-generate:{story_id}"` или daemon-флоу |
| Field improve | revision `reason="ai-improve:{field}"` (см. tasks_fields.py — требует ручной проверки) |

**Ограничения:**
- Признак AI-author размазан по `Revision.reason` — **нет агрегата**
  «все AI-сгенерированные сущности проекта». Аналитика «сколько AI vs human
  пишет» из БД достаётся регулярным выражением по reason.
- Нет связи `revision → trace_call` — невозможно из конкретной правки
  попасть в её LLM-вызов (tokens, latency, model).
- Document `frontmatter.generated_from` не индексируется → запрос «что
  сгенерировано из MASTER.md» = сканирование всех docs.

---

## 4. Логирование и tracing

**Trace-таблица** ([trace_service.py](../../../cod_doc/services/trace_service.py)):
поля `model`, `task_id`, `kind` (chat/search/reindex/improve), `input_tokens`,
`output_tokens`, `duration_ms`, `tool_calls` (JSON), `error`.

**Где видно пользователю:**
- Вкладка «Trace» на task-detail (если task_id связан) — list newest-first.
- Глобальной страницы «AI activity» нет.

**Ограничения:**
- `task_id` может быть NULL → orphan-traces (например, доковая генерация
  не привязана к задаче) **невидимы** в UI. Доступ только через прямой
  SQL-запрос.
- **Нет ретеншн-полиси** — таблица растёт линейно, vacuum нет.
- **Нет cost-колонки**. Чтобы посчитать $, нужен JOIN с `model_catalog.py`
  (статика в коде, не в БД).
- Tool_calls как JSON-string → нет fast-фильтра «все вызовы read_file»
  без LIKE.

---

## 5. Ограничения и боли (резюме)

### 5.1 High

#### AI-HI-1. Нет prompt caching
**Симптом:** каждый orchestrator-iteration отправляет один и тот же
~3K-токенный system prompt + MASTER.md заново. На 50-step задаче —
переплата ×50. Все четыре `ai_generate.*` flow тоже без cache.

**Где:** [orchestrator.py:407-415](../../../cod_doc/agent/orchestrator.py),
[ai_generate.py:320-328](../../../cod_doc/services/ai_generate.py).

OpenRouter поддерживает Anthropic prompt caching (через `cache_control`
блоки) и провайдер-специфичные cache hints. Не используется.

#### AI-HI-2. Нет cost tracking и budget guard
**Симптом:** пользователь не видит затрат. Daemon-режим с `agent_enabled`
+ `agent_interval=60` может за ночь сжечь $50 без алерта. Cost-fields
нет ни в `trace_call`, ни в UI.

#### AI-HI-3. Generation flows без retry
**Симптом:** transient 429/503/connection-reset на `/docs/generate` или
`/stories/generate` → красная плашка, пользователь жмёт refresh, всё
заново (платим за второй prompt). [ai_generate.py:329-330](../../../cod_doc/services/ai_generate.py)
ловит **любой** Exception как fatal.

#### AI-HI-4. Блокирующий UX долгих генераций
**Симптом:** `/docs/generate` на крупном корпусе занимает 20-40 сек.
Пользователь видит спиннер, не понимает «жив ли запрос», иногда
F5 → дубль. Нет streaming, нет «X% complete», нет cancel.

### 5.2 Medium

#### AI-ME-1. Generation использует sync `OpenAI` в FastAPI handler
[ai_generate.py:317](../../../cod_doc/services/ai_generate.py) — sync
client внутри def-handler'а. Блокирует event loop. Под нагрузкой даже
один-два параллельных запроса делают весь Web фоновым.

#### AI-ME-2. Streaming доступен только в orchestrator (WS)
Web-flow генерации (stories/tasks/docs/improve) — single-shot. Нет SSE,
нет partial-render. WEB-030 (SSE run console) для daemon-loop ещё не
реализован.

#### AI-ME-3. Embeddings не используются автоматически
ChromaDB настроен и работает, но retrieval перед LLM-вызовом не делается.
Агент должен сам додуматься позвать `search_docs` — на практике редко.
Эффективность контекста ниже потенциальной.

#### AI-ME-4. Provenance AI-output фрагментирован
См. §3. Аналитика «AI-vs-human» = regex по `revision.reason` + сканирование
`frontmatter`. Нет JOIN`ов на trace_call → cost-per-document не
посчитать.

#### AI-ME-5. Context-degrade не fed back
`orchestrator.py` после успеха в degraded-режиме всё равно стартует следующую
итерацию с L0. Если нагрузка стабильна, мы каждый раз платим за один и
тот же 429.

#### AI-ME-6. Rate-limit UX без `Retry-After`
[retry.py:108-141](../../../cod_doc/agent/retry.py) использует синтетический
exp backoff даже при наличии `Retry-After` header'а от OpenRouter — может
ретраить раньше или позже оптимального.

### 5.3 Low

#### AI-LO-1. `model_catalog.py` обновляется вручную
При выходе Sonnet 4.7 нужен code-PR. Нет sync с OpenRouter `/v1/models`.

#### AI-LO-2. Нет deprecated-version detection
Если `cfg.model` выпадает из списка моделей у провайдера — узнаём через
fail в проде.

#### AI-LO-3. Нет cost-prediction до запуска
В `/docs/generate` нет поля «estimated cost: $X.XX» на основе размера
source-docs × cost модели. Пользователь жмёт «Generate» вслепую.

---

## 6. Предложения по развитию (для решения болей пользователя)

Группированы по типу боли. Каждая запись — backlog-кандидат, не план.
Закрытие — после отдельного RFC/proposal.

### 6.1 Деньги и контроль расхода

**P-1. Cost tracking + dashboard** *(закрывает AI-HI-2, AI-LO-3)*
- Добавить колонку `cost_usd` в `trace_call` (compute из `model_catalog`
  при записи).
- Страница `/p/{slug}/ai-activity` (или вкладка в `/settings/usage`):
  taxonomies — model × kind × day.
- Soft budget в `config.yaml`: `daily_budget_usd` → daemon-loop стопится
  при достижении.
- Hard guard на `/docs/generate` и подобные: если запрос превысит
  N% дневного бюджета — confirm-dialog.

**P-2. Cost-prediction перед запуском**
- На форме `/docs/generate`: лайв-счётчик «~$0.04 input, ~$0.02 output, max ~$0.30»
  на основе токенайзинга source-docs (`tiktoken` или эвристика).
- То же для `/stories/generate`, `/improve`.
- Боль: пользователь жмёт «Generate» и **знает**, что покупает.

**P-3. Prompt caching** *(закрывает AI-HI-1)*
- В orchestrator: вынести system prompt + MASTER.md в `cache_control`-блок
  (Anthropic-style через OpenRouter).
- Замерить Cache-hit rate в `trace_call` (новое поле `cache_read_tokens`,
  `cache_write_tokens`).
- ROI: на 50-step задаче — экономия input-tokens 80-90%.
- Боль: пользователь видит, что повторные запуски стоят на порядок дешевле.

### 6.2 UX долгих операций

**P-4. Streaming генерации в Web** *(закрывает AI-HI-4, AI-ME-2)*
- Перевести `/docs/generate`, `/stories/generate`, `/improve` на async
  +`stream=True` + SSE (`hx-ext="sse"` уже работает в `_layout/project_tabs.html`).
- Partial render: показывать секции по мере прибытия.
- Кнопка «Cancel» — `DELETE /jobs/{job_id}`.
- Боль: ожидание перестаёт ощущаться как «зависло».

**P-5. Retry для generation flows** *(закрывает AI-HI-3)*
- Применить `with_retry` ко всем `_chat_json`-вызовам.
- Различать transient (429/503/connection) vs fatal (auth/4xx-other).
- Боль: один отвал сети не теряет работу.

**P-6. Async-везде в Web AI-handler'ах** *(закрывает AI-ME-1)*
- Поменять [ai_generate.py:_chat_json](../../../cod_doc/services/ai_generate.py)
  на `async def` + `AsyncOpenAI`.
- FastAPI-handler'ы переключить с `def` на `async def`.
- Боль: при двух пользователях UI не подвисает.

### 6.3 Качество AI-вывода и доверие

**P-7. Diff-view для `improve`** *(закрывает часть AI-ME-x в §1.3)*
- Возвращать не plain-text, а diff (старый ↔ новый) как в `_frag/section_view`.
- Поле «Why?» — почему AI предложил эту правку (опциональный prompt-trick:
  попросить вернуть `{before, after, rationale}`).
- Боль: пользователь видит **что именно** меняется и решает осознанно.

**P-8. Embeddings retrieval перед LLM-вызовом** *(закрывает AI-ME-3)*
- Hook'и на `doc_create` / `section_patch` / `task_create` →
  пересчёт Chroma-вектора (background task).
- В orchestrator: перед основным LLM-вызовом — top-K retrieval по текущей
  задаче, добавление в `<context_refs>`.
- Миграция default embedding-модели на `text-embedding-3-small`.
- Боль: агент находит релевантные секции **сам**, без необходимости
  явно перечислять refs в задаче.

**P-9. Provenance UI: agg AI-output** *(закрывает AI-ME-4)*
- Колонка `created_by_kind` в `Document` / `Task` / `Story`: `human|ai|hybrid`.
- Внешний ключ `revision.trace_call_id`.
- Страница `/p/{slug}/ai-activity` (см. P-1) показывает: «AI создал N tasks /
  M docs за период, средняя стоимость $X».
- Боль: «можно ли доверять этому документу?» — ответ виден, не догадка.

### 6.4 Reliability

**P-10. Honor `Retry-After`** *(закрывает AI-ME-6)*
- В [retry.py](../../../cod_doc/agent/retry.py): если в exception есть
  `retry_after` (RateLimitError parses header), использовать его вместо
  exp backoff.

**P-11. Адаптивный context-level memory** *(закрывает AI-ME-5)*
- Сохранять в `task_meta` last successful context-level (`L0|no_master|refs_only`).
- Следующая итерация стартует с него, не с L0.

**P-12. Tool-execution timeout**
- В `ToolExecutor.execute` — `asyncio.wait_for(..., timeout=30)`.
- Боль: orchestrator не висит на rogue read_file из 1GB-лога.

### 6.5 Долго-играющий backlog (P-13..15)

**P-13. Per-project / per-environment API-ключ**
Сейчас один global. Дать override в `Project.config` для разделения
prod/sandbox-расходов.

**P-14. Auto-sync `model_catalog`** *(закрывает AI-LO-1, AI-LO-2)*
Cron-job (или ленивый refresh при старте) → fetch OpenRouter `/v1/models`
→ update local catalog. Detection deprecation → warning в `/settings`.

**P-15. Обязательная approval-stage для daemon-режима**
Сейчас daemon пишет в БД напрямую. Добавить `daemon_approval_required=true`:
вместо commit'а агент создаёт `proposal` (см. [proposals/12-approvals.md](../../../proposals/12-approvals.md)),
человек ревьюит в UI, нажимает accept. Боль: автономный режим перестаёт
быть «страшным».

---

## 7. Топ-5 изменений с максимальным impact

| # | Предложение | Закрывает | Затраты | Польза |
|---|---|---|---|---|
| 1 | **P-3 Prompt caching** | AI-HI-1 | средние (контракт `cache_control`) | -80 % input-tokens на повторных запусках |
| 2 | **P-1 Cost tracking + dashboard** | AI-HI-2, AI-LO-3 | средние (новые поля + page) | пользователь перестаёт бояться daemon |
| 3 | **P-4 Streaming генерации** | AI-HI-4, AI-ME-2 | средние (async переход + SSE) | UX перестаёт «зависать» |
| 4 | **P-5 Retry для gen-flows** | AI-HI-3 | малые (применить `with_retry`) | устранение «случайных» падений |
| 5 | **P-8 Embeddings retrieval** | AI-ME-3 | средние (hook'и + миграция модели) | агент сам находит контекст |

---

## 8. Методология

1. Inventory собран grep'ом по `cod_doc/`:
   - `grep -rnE "AsyncOpenAI|OpenAI\(|claude|openrouter|cache_control|stream="`,
   - `grep -rn "AIBackendError\|trace_service\|response_format"`,
   - проверены `cod_doc/agent/`, `cod_doc/services/`, `cod_doc/api/web/pages/`,
     `cod_doc/api/web/fragments/`, `cod_doc/mcp/`.
2. Каждое утверждение в §1-§4 проверено чтением файла на указанной строке.
3. Severity:
   - **high** — пользователь теряет деньги или продуктивность каждый день;
   - **medium** — UX-degradation, обходимое;
   - **low** — гигиена, операционные риски.
4. Предложения в §6 не претендуют на план; это backlog-затравка для
   отдельных RFC.

## 9. Что осталось вне scope

- **Безопасность ключей** (encryption-at-rest, env-only) — отдельный
  security audit.
- **Качество промптов** (prompt-engineering review system-prompts) —
  требует A/B-тестинга, отдельный workstream.
- **Этика AI-output** (галлюцинации, fail-fast в prompts.py есть, но
  не оценены количественно) — отдельный quality-audit.
- **MCP authorization model** — будет рассматриваться при появлении
  multi-tenant сценария.

## 10. Changelog

| Дата | Событие |
|---|---|
| 2026-05-06 | Аудит проведён. 13 находок (4 high, 6 medium, 3 low). 15 предложений сгруппированы по типу боли. Топ-5 приоритизирован. |
