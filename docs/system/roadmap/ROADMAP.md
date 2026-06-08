---
type: roadmap-index
scope: cod-doc-roadmap
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-06-05
last_updated: 2026-06-05
audience: [contributors, agents]
related_docs:
  - ../MASTER.md
  - task-graph.md
  - ../../../proposals/README.md
  - ../audit/2026-06-04-self-improvement-compared.md
---

# COD-DOC — Roadmap (canonical index)

> Единая точка приоритизации поверх всех task-планов, RFC и аудитов.
> Создан 2026-06-05 после трёхсторонней сверки (БД ↔ markdown ↔ **код**),
> которая показала, что markdown-статусы устарели в обе стороны, а проект
> **не закончен**: есть подтверждённые кодом дыры + непрожатый feature-трек.

## Правило источника истины

1. **БД приложения (`.cod-doc/state.db`) — source of truth для трекаемых задач.**
   Статус задачи определяется записью в БД, а не markdown.
2. **Код — арбитр при расхождении.** Если БД и markdown спорят — смотрим, что
   реально реализовано в коде (file:line), и приводим оба к нему.
3. **markdown Progress Overview — вторичен**, обновляется из БД/кода. Все
   планы со статусом `done` ниже синхронизированы 2026-06-05.

## Navigation

- [System MASTER](../MASTER.md)
- [Task graph](task-graph.md)
- [Proposals (RFC 01–21)](../../../proposals/README.md)
- [Self-improvement audit 2026-06-04](../audit/2026-06-04-self-improvement-compared.md)

## Ground-truth состояние планов (сверено с кодом 2026-06-05)

| План | Статус (истина) | Примечание |
|---|---|---|
| [paperclip-adoption](paperclip-adoption-task-plan.md) (RFC 01–15) | ✅ done | 82 done / 2 cancelled в БД; кроме Section H (см. A0-1) |
| [adr-system](adr-system-task-plan.md) | ✅ done | 8/8; миграции+сервис+9 MCP+CLI+web |
| [observability-and-indexing](observability-and-indexing-task-plan.md) | ✅ done | 8/8; metrics, commit-links, code-refs, repo-index, FTS5 |
| [refactor-large-files](refactor-large-files-task-plan.md) | ✅ done | ~20/20; пакеты сервисов/CLI/моделей/pages + CSS-split |
| [cod-doc bootstrap](cod-doc-task-plan.md) | 🔄 in-progress | открыты COD-042/043 (L2/L3), COD-052 (freeze) → A1-4 |
| [web-frontend](web-frontend-task-plan.md) | 🔄 in-progress | открыты WEB-031, WEB-042 → A0-3 |
| [agent-tools-completion](agent-tools-completion-task-plan.md) | 🟡 open | docstring+тесты; код AGT done → A0-1 |
| [audit-followups](audit-followups-task-plan.md) | 🔄 in-progress | ~10 doc-задач → A2-2 |
| RFC 16–20 (hackathon-track) | 🆕 не начато | только proposals → Трек B |

## Реальный бэклог

Два трека. **Решение: Трек A (долг) целиком приоритетнее Трека B (фичи).**
Открытые задачи Трека A заведены в БД как план `stabilization-2026-06`
(см. ниже) — БД остаётся единым трекером.

### Трек A — Стабилизация и tech-debt

#### A-P0 — сейчас (~4–5 дней)

| ID | Задача | Источник | Файлы |
|---|---|---|---|
| **A0-1** | Section H: убрать misleading docstring, дописать 4 integration-теста через `mcp.call_tool`, audit-отчёт (код AGT уже done) | audit P0-1 | `mcp/tools/agent_tools.py:115-118`, `AGENTS.md`, `tests/integration/test_agent_profile_mcp.py` |
| **A0-2** | Удалить 5 legacy YAML-модулей (863 строки dead code; флаг «спрятать» PCA-935 отменён → удаляем) | audit P0-2 | `mcp/tools/legacy_*.py` |
| **A0-3** | Закрыть 2 WEB-дыры: WEB-031 (import progress stream через WebSocket), WEB-042 (`cod-doc audit --web-routes`) | web-plan | `api/websocket.py`, `cli/cmd_audit.py` |

#### A-P1 — ближайший месяц (~3 недели)

| ID | Задача | Источник | Файлы |
|---|---|---|---|
| **A1-1** | Закрыть `pragma: no cover` ×8 на degraded paths (8 unit-тестов) | audit P1-3 | `event_bus.py:123,132`, `run_context.py:140,179,214`, `websocket.py:48`, `routine_service.py:463`, `adapters/registry.py:93` |
| **A1-2** | Кеш `Config.load()` (YAML с диска на каждый MCP-вызов; caveat: hot-reload) | audit P1-5 | `config.py:155`, `mcp/tools/_db.py:27` |
| **A1-3** | RFC #21 (degraded-path auditability, гибрид): ring buffer + `error_audit` + 2 MCP-тула + 2 web-страницы + TTL-cleanup | proposals/21 | миграция, `services/`, `mcp/tools/`, `api/web/pages/` |
| **A1-4** | COD-042/043 (ContextService L2/L3 семантика) + COD-052 (freeze/rollback projection) | cod-doc plan | `services/context_service.py`, `services/projection_service/` |

#### A-P2 — опортунистично

| ID | Задача | Источник |
|---|---|---|
| **A2-1** | Хвост refactor (подтвердить тесты RFL-070..075; CSS уже split) | refactor plan |
| **A2-2** | audit-followups: DOC-ME-1,2,3,5,6,7 + DOC-LO-2..5 (~10 doc-задач) | audit-followups |
| **A2-3** | Мелочи аудита: skill-matcher→embeddings, `iter_skill_records()`→core, `event_bus._subscribers` dispose, `task: Any`→Protocol | audit P2 |
| **A2-4** | Реанимировать PCA-947 (`activity_subscribe` SSE) если нужен event-driven orchestration | cancelled |

### Трек B — Feature-трек (hackathon RFC, после стабилизации)

Ветка `feat/hackathon-ideas-rfc`. Порядок из [proposals/README.md](../../../proposals/README.md):

- **B-MVP (1–2 недели каждая):** RFC 18 Vibecoder's Diary → RFC 19 Context-Scout → RFC 17 Living Specification.
- **B-Scale (3–4 недели):** RFC 16 AI-Pair-Hacker → RFC 20 Multi-Agent Standup.

Каждая RFC декомпозируется в отдельный план (`plan_create`) перед стартом.

## Порядок исполнения

1. **A-P0** → стабильная база.
2. **A-P1** → закрытие долга и наблюдаемости.
3. **A-P2** → опортунистичные чистки.
4. **Трек B** → фичи (18→19→17→16→20).

## История сверки

- **2026-06-04** — [self-improvement audit](../audit/2026-06-04-self-improvement-compared.md): двойное LLM-ревью, P0/P1/P2 backlog, RFC #21 (гибрид).
- **2026-06-05** — трёхсторонняя сверка БД↔markdown↔код; синхронизированы статусы 5 планов; создан этот индекс; Трек A заведён в БД (`stabilization-2026-06`).
