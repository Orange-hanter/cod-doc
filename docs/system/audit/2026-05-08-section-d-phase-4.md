---
type: audit-report
scope: paperclip-adoption / Section D (Phase 4 — Adapter pattern)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-c-phase-3.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/10-adapter-pattern.md
  - ../../../cod_doc/agent/adapters/base.py
---

# Section D — Closure Report (Phase 4: LLM Adapter Pattern)

> **Назначение.** Зафиксировать закрытие 3 задач Section D
> (PCA-300, PCA-301, PCA-302) и findings → backlog.

## 1. TL;DR

- **PCA-300** — `LLMAdapter` Protocol + нейтральные типы
  (`ChatResponse`, `ChatMessage`, `ChatChoice`, `ToolCall`, `FunctionCall`,
  `AdapterCapabilities`) в `cod_doc/agent/adapters/base.py`. Нейтральный
  формат зеркалит OpenAI-структуру, так что парсинг ответов в orchestrator
  остался неизменным.
- **PCA-301** — три встроенных адаптера: `openai_compat` (прозрачная
  замена прежнего захардкоженного `AsyncOpenAI`), `anthropic` (нативный
  SDK + format-converters для tool-use), `mock` (детерминированная очередь
  для тестов, устраняет сетевые вызовы).
- **PCA-302** — `AdapterRegistry` с plugin-loader (JSON-файл), поле
  `Config.llm_adapter` (default `openai_compat`), DI в
  `Orchestrator.__init__(adapter=...)`, `_generate_tasks_from_master`
  обновлён. Все 13 тестов `test_orchestrator.py` мигрированы на
  `MockAdapter` (убраны патчи `AsyncOpenAI`).
- **26 новых тестов** в `tests/test_adapters.py`. **996 tests pass**
  (970 → 996).
- **4 findings** (H1-H4) → backlog Section F (PCA-924..927).

## 2. Section D deliverables

| # | Деливерабл | Файл / артефакт | Статус |
|---|------------|------------------|--------|
| D1 | Section D audit-report | `docs/system/audit/2026-05-08-section-d-phase-4.md` | ✅ |
| D2 | `LLMAdapter` Protocol + types | `cod_doc/agent/adapters/base.py` | ✅ |
| D3 | `openai_compat` adapter | `cod_doc/agent/adapters/openai_compat.py` | ✅ |
| D4 | `anthropic` adapter | `cod_doc/agent/adapters/anthropic.py` | ✅ |
| D5 | `mock` adapter | `cod_doc/agent/adapters/mock.py` | ✅ |
| D6 | `AdapterRegistry` + plugin loader | `cod_doc/agent/adapters/registry.py` | ✅ |
| D7 | `adapters/__init__.py` | `cod_doc/agent/adapters/__init__.py` | ✅ |
| D8 | `Config.llm_adapter` + `anthropic_api_key` | `cod_doc/config.py` | ✅ |
| D9 | `Orchestrator` DI refactor | `cod_doc/agent/orchestrator.py` | ✅ |
| D10 | Adapter tests (26) | `tests/test_adapters.py` | ✅ |
| D11 | `test_orchestrator.py` мигрирован на MockAdapter | `tests/test_orchestrator.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-300** — Protocol + нейтральные типы определены; `LLMAdapter`
      помечен `@runtime_checkable`; `MockAdapter` проходит isinstance-проверку.
- [x] **PCA-301** — `openai_compat` поведение идентично прежнему (нет
      регрессий, 970 предыдущих тестов зелёные). `anthropic` конвертирует
      tool-use в обе стороны. `mock` с пустой очередью возвращает дефолтный
      "done". Запись вызовов в `calls`.
- [x] **PCA-302** — `Orchestrator(project, config, adapter=mock_adapter)`
      wire adapter напрямую; без adapter → выбор из реестра по
      `config.llm_adapter`. Все тесты оркестратора используют MockAdapter,
      сетевых вызовов нет.

## 4. Findings (→ backlog)

### H1 — Streaming не реализован *(medium)*

`AdapterCapabilities.streaming=False` для всех адаптеров. `stream_chat()`
не входит в Protocol (намеренно оставлен на Phase 2 per proposal 10 §61).
UI streaming (показ промежуточного thinking) работает через event loop
orchestrator'а, а не через SDK streaming — это правильно для текущей
архитектуры. Если понадобится live-streaming ответа до tool-call'а —
нужно добавить `stream_chat` в Protocol + реализовать в обоих адаптерах.

**Рекомендация:** не делать сейчас. Зафиксировать как backlog.

### H2 — Cost tracking нормализован нулём для `openai_compat` *(low)*

`cost_estimate` в `openai_compat` возвращает `Decimal(0)` — нет статического
справочника цен для OpenRouter-моделей. `anthropic` содержит примерный
прайс для 3 моделей. Без корректных цен метрика `AgentRun.llm_tokens_in/out`
есть, но `cost_event` отсутствует.

**Рекомендация:** добавить статический dict цен для популярных OpenRouter
моделей (claude-sonnet, gpt-4o, gemini-pro) в `openai_compat.py`. Источник —
захардкоженный JSON в репо, обновляемый вручную. Отдельная F-задача.

### H3 — Проверка capabilities не реализована *(low)*

Proposal 10 Q6: «что делать если задача требует tool_use, а
`capabilities.tool_use=False`?». В текущей реализации orchestrator не
проверяет capabilities перед вызовом — если адаптер не поддерживает
tool_use, он просто упадёт с ошибкой от SDK.

**Рекомендация:** добавить проверку в `Orchestrator.__init__`:
```python
if not self.adapter.capabilities.tool_use:
    raise ValueError(f"Adapter {self.adapter.name!r} must support tool_use")
```
Trivial + defensive.

### H4 — `self.client` deprecated shim не задокументирован *(low)*

В `Orchestrator.__init__` добавлен `self.client = getattr(self.adapter, "_client", None)` как legacy shim для кода, который обращается к `orchestrator.client` напрямую. Deprecated-warning не испускается, и нет списка таких мест.

**Рекомендация:** grep `orchestrator.client` + добавить `DeprecationWarning`
при обращении к `self.client`. Удалить shim в следующей major-версии.

## 5. Метрики

| Метрика | До Section D | После | Δ |
|---------|-------------:|------:|--:|
| LLM backends | 1 (hardcoded) | 3 built-in + plugin | +∞ |
| Adapter modules | — | 5 | +5 |
| Orchestrator LLM calls with network | 13 tests × N calls | 0 | -∞ |
| `tests/` total | 970 | 996 | +26 |
| Section D done tasks | 0 | 3 | +3 |
| Total A+B+C+D done | 35 | 38 | +3 |

## 6. Что не вошло (out of scope)

- **ollama adapter** (proposal 10 §82) — нет запроса на локальные модели.
- **Streaming** (H1) — see above.
- **External adapters CLI** (`cod-doc adapter add`) — только JSON plugin loader.
- **Cost dashboard** (H2) — нет UI-страницы для cost_estimate.

## 7. Следующий шаг

Sections A, B, C, D закрыты. **38 done tasks** суммарно.

Открытые направления:
- **Section E (UX & Migration, PCA-400..422)** — 7 задач. Folder manifest
  scanner, web import UI, legacy YAML migration, link redesign. Высокая
  видимость, средний риск.
- **Section F backlog** — 12 + 4 = 16 накопленных задач (F1-F6 + G1-G6 +
  H1-H4). Можно консолидировать до открытия Section E.

Findings H1-H4 заведены как PCA-924..927 в Section F.

Рекомендация: **Section E** (завершает весь RFC-беклог), либо
**Section F consolidation** (быстрые wins G2/G3/H3/H4).
