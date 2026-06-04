# 2026-06-04 — Self-Improvement Compared (miniMax-m3 vs deepseek-v4-pro)

> 📊 Meta: `{"scope": "self-audit", "models": ["miniMax-m3 (Hermes)", "deepseek-v4-pro (openrouter)"], "method": "independent parallel review", "result": "hybrid RFC #21 proposed"}`

## TL;DR

Двухнезависимое ревью COD-DOC двумя LLM-моделями (`miniMax-m3` локально, `deepseek-v4-pro` через openrouter) дало **73% совпадений** в топ-observations. Обе модели независимо обнаружили:

- 🔴 Cycle-5 agent profile (AGT-003..007) — 5 из 6 тулов = `NotImplementedError` stubs.
- 🔴 5 legacy YAML-backed модулей (`legacy_*.py`, 863 строки) — dead code после DB-миграции.
- 🟡 8 мест с `pragma: no cover` на degraded paths — нет CI-сигнала на регрессию.
- 🟡 `Config.load()` грузит YAML с диска на каждый MCP-вызов.
- 🟠 `task: Any` workaround в `agent_service.py:119` (circular import).
- 🟠 `event_bus._subscribers` — module-level defaultdict, утечка при reload.

**Концептуальное расхождение** в предложенной RFC #21 привело к **гибридной** версии:
- **Hard exceptions** (`TaskNotFoundError`, `StatusTransitionError`) → DB-таблица `error_audit` (подход pro).
- **Soft failures** (degraded paths, defensive guards) → in-memory ring buffer (подход mine).
- **Activity events** на обоих tier'ах для WebSocket real-time visibility.

## Методология

### Source-of-truth baseline
Перед запуском ревью обе модели получили **одну и ту же** базу фактов:
- 119 MCP-тулов (`mcp.list_tools()` enumeration).
- Hot-files по `git log --all --pretty=format: --name-only | sort | uniq -c | sort -rn` (топ-15: `api/web/pages.py` × 20, `agent/orchestrator.py` × 19, `task_service.py` × 18, `mcp/server.py` × 16, …).
- Large-files по `find cod_doc -name "*.py" -exec wc -l {} \;` (топ: `api/web/pages/docs.py` 1078, `stories.py` 860, `task_service.py` 851, `task_tools.py` 729, `orchestrator.py` 706, …).
- 8 мест с `pragma: no cover` (полный список ниже).
- 5 audit-отчётов (Section A, B, C, G, Web) + 4 web-batch checkpoint'а.
- 9 task-планов в `docs/system/roadmap/`.
- Cycle-5 agent profile — 6 тулов, 1 реализован, 5 stubs.

### Independence protocol
- **Pro** (`deepseek-v4-pro`) запускался через `delegate_task` с явным указанием: «не смотри в предыдущий контекст, работай только из репо».
- **Mine** (`miniMax-m3`) писался **до** того, как я прочитал pro-отчёт.
- Сравнение сделано **после** обеих версий, методом «что совпало / что уникально / где расхождение».

## Совпадения (11/15 — сильный сигнал)

| # | Observation | Severity | Files |
|---|---|---|---|
| 1 | AGT-003..007 — `NotImplementedError` stubs | 🔴 P0 | `mcp/tools/agent_tools.py:121-173` |
| 2 | Legacy YAML/DB двойные пути (5 файлов, 863 строки) | 🔴 P0 | `mcp/tools/legacy_*.py` |
| 3 | `pragma: no cover` × 8 на degraded paths | 🟡 P1 | `event_bus.py:123,132`; `run_context.py:140,179,214`; `websocket.py:48`; `routine_service.py:463`; `adapters/registry.py:93` |
| 4 | Skill matcher — keyword substring, fragile | 🟡 P1 | `agent/skill_matcher.py:46-83,128-132` |
| 5 | `Config.load()` без кеша → I/O на каждый MCP | 🟡 P1 | `mcp/tools/_db.py:27`; `config.py:155` |
| 6 | Cycle-5 task-centric agent profile = правильно | ✅ Strength | `mcp/profiles.py:18-49` |
| 7 | Event bus с deferred до commit = правильно | ✅ Strength | `services/event_bus.py:122,131` |
| 8 | Audit cadence + run_id = killer feature | ✅ Strength | `services/run_context.py` |
| 9 | `event_bus._subscribers` global state leak | 🟠 Dep | `event_bus.py:56` |
| 10 | `iter_skill_records()` дублируется mcp↔services | 🟠 Dep | `agent/skill_matcher.py:21` ↔ `services/skill_service.py:15` |
| 11 | `task: Any` workaround в `agent_service.py:119` | 🟠 Dep | `services/agent_service.py:119` |

## Уникальные находки

### Нашёл pro, не нашёл mine (6)
- **Adapter-паттерн LLM** (`agent/adapters/base.py:115-137`) — `@runtime_checkable Protocol` для LLM backends, поддержка `openai_compat`/`anthropic`/`mock`. Это **архитектурная** сила, я её не упомянул.
- **ADR-иммутабельность** (commit `2a09c1b`) — после `ACCEPTED` ADR нельзя отредактировать, только `supersede` / `deprecate`. Pro выделил как Strength #5, я упомянул мимоходом.
- **`lifespan` в `api/server.py:40`** — значит, graceful shutdown инфраструктура уже есть, осталось добавить `dispose_all()`.
- **Skill matcher: stop-слова хардкод** (lines 67-82) — конкретика, я только общий fragility упомянул.
- **8 vs 8 мест pragma:no cover** — оба насчитали, но pro детальнее расписал каждое.
- **Specific file:line references** (18 ссылок в pro vs 12 в mine) — pro глубже в цитировании.

### Нашёл mine, не нашёл pro (3)
- **`refactor-large-files-task-plan.md` существует, status=pending** — мой отчёт использовал **существующий план** как контекст: «план есть, не начат, Section A = `task_service.py` декомпозиция». Pro предложил это как новую работу.
- **Hot-files ranking** (`api/web/pages.py` × 20 коммитов) — указывает на стабильно-активный файл, потенциальный tech debt.
- **Web pages: `docs.py` 1078 + `stories.py` 860 LOC** — крупные view-файлы, не упомянуты в `refactor-large-files-task-plan.md` (который покрывает только services). Я заметил, что web-pages **не охвачены планом**.

## Расхождение в RFC #21 (центральный архитектурный вопрос)

| Аспект | Pro (Transaction-Level Error Audit Trail) | Mine (Degraded-Path Auditability) | Гибрид |
|---|---|---|---|
| Storage для soft failures (degraded paths) | (не выделил как отдельный tier) | In-memory ring buffer (100) | **Ring buffer 100** — дешёво, не раздувает БД |
| Storage для hard exceptions (TaskNotFound, StatusTransition) | Новая DB-таблица `error_audit` с run_id + traceback | (не предлагал) | **DB-таблица** — для post-mortem через 3 месяца |
| Trigger | `audit_errors(...)` context manager | `@degraded_path("CODE")` decorator | **Оба**: decorator для Tier 1, context manager для Tier 2 |
| WebSocket events | `error.occurred` при critical | (не упомянул) | **`error.occurred` для Tier 2 critical, `degraded_path.triggered` для Tier 1** |
| Persistence | Полная (вся история) | Нет (теряется при рестарте) | **Гибрид: Tier 1 — нет, Tier 2 — да** |
| TTL / cleanup | Не упомянул | Не упомянул | **TTL-чистка `error_audit` через routine (proposal 07) — 90 дней** |

**Гибридная версия** (финальная) — `proposals/21-degraded-path-auditability.md`. Использует **сильные стороны** обоих подходов:
- Soft failures — ring buffer (быстро, не нагружает БД, идеально для degraded paths, которые не должны срабатывать).
- Hard exceptions — DB-таблица (для post-mortem, audit-trail, compliance).

## Метрики ревью

| Метрика | Pro | Mine |
|---|---|---|
| Время | 4 мин 1 сек | ~3 мин (без delegating overhead) |
| API-вызовы | 18 (terminal + read_file) | ~10 (read + grep, более selective) |
| Tokens | 1 180 356 in / 12 750 out | ~6 000 in / 5 000 out (10x меньше) |
| File:line references | 18 | 12 |
| Top strengths | 5 | 5 |
| Top weaknesses | 5 | 5 |
| Strong dependencies | 5 | 5 |
| Quick wins | 7 | 7 |
| P0 / P1 / P2 items | 2 / 3 / 3 | 2 / 3 / 3 |
| RFC draft (lines) | ~250 (EAT) | ~280 (DPA) |
| Hybrid RFC (final) | — | — |
| **Стоимость** | **$0.55** (1.2M × $0.10 + 12.7K × $0.20 = $0.12 + $2.54 = ...) | **$0** (локальная модель) |

*Pro стоимость фактически: input 1.18M × $0.10/M = $0.118, output 12.75K × $0.20/M = $0.00255, итого $0.121.*

## Объединённый список рекомендаций (для планирования)

### P0 (точно делать)
1. **Реализовать AGT-003..007** (cycle-5 stubs → код) — оба согласны.
2. **Удалить legacy YAML/DB дубли** — оба согласны. 863 строки dead code.

### P1 (в ближайший месяц)
3. **Закрыть `pragma: no cover`** — оба согласны. 0.5-1 день, 8 unit-тестов.
4. **Применить RFC #21 в гибридной форме** — 1-2 недели (ring buffer + DB-таблица + 2 MCP тула + 2 web-страницы + tests).
5. **Кешировать `Config.load()`** — оба согласны. 0.5 дня. **Caveat:** проверить, не меняется ли Config без рестарта MCP-сервера.

### P2 (по возможности)
6. **Skill matcher → embedding similarity** — оба согласны. 1-2 дня.
7. **`iter_skill_records()` в `core/`** — оба согласны. 0.5-1 день. Pure refactor.
8. **`event_bus._subscribers` dispose** — оба согласны. 0.5 дня + lifespan hook.
9. **`task: Any` → Protocol** — оба согласны. 0.5 дня.
10. **Запустить `refactor-large-files-task-plan.md`** — мой отмечает, что план уже есть, status=pending. Section A = `task_service.py` декомпозиция.
11. **Добавить web-pages в refactor-план** — `docs.py` 1078 + `stories.py` 860 строк не покрыты.

## Следующие шаги

- [ ] Принять RFC #21 (гибрид) — см. `proposals/21-degraded-path-auditability.md`.
- [ ] Создать RFC-задачи в `docs/system/roadmap/` (через `plan_create` MCP-тул) для P0/P1 items.
- [ ] Закрыть секцию через audit-отчёт по факту реализации.
- [ ] Пересмотреть `refactor-large-files-task-plan.md` — добавить web-pages.

## Источники

- `cod_doc/services/event_bus.py:122-132` — deferred commit/rollback хуки.
- `cod_doc/services/run_context.py:140,179,214` — degraded paths.
- `cod_doc/mcp/tools/agent_tools.py:121-173` — AGT-003..007 stubs.
- `cod_doc/mcp/tools/legacy_*.py` — 5 legacy файлов, 863 строки.
- `cod_doc/api/server.py:40` — `async def lifespan` (уже есть).
- `cod_doc/agent/skill_matcher.py:46-83` — keyword matching.
- `cod_doc/config.py:155` — `Config.load()` classmethod.
- `cod_doc/services/agent_service.py:119` — `task: Any` workaround.
- `docs/system/roadmap/refactor-large-files-task-plan.md` — существующий план (status=pending).
- `proposals/01-skills-layer.md` — формат RFC.
- 2026-06-04 pro-отчёт (`deepseek-v4-pro`) — 18 file:line references.
- 2026-06-04 mine-отчёт (`miniMax-m3`) — 12 file:line references.
