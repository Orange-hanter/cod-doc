---
type: audit-report
scope: doc-curator-section-b
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-19
last_updated: 2026-09-19
related_docs:
  - ../../../proposals/25-doc-curator-agent.md
  - ../roadmap/ROADMAP.md
audience: [contributors, agents]
---

# Audit — Plan `doc-curator-2026-09`, Section B closure («Agent surface swap»)

> **Контекст.** [RFC 25](../../../proposals/25-doc-curator-agent.md) §3.2
> перепрофилирует дефолтный MCP-профиль `agent` с task-centric
> (`agent_pick`…) на curator-набор (`agent_capabilities`, `ctx_search`,
> `ctx_docs`, `ctx_drift`, `context_get`, `agent_report`). План
> `doc-curator-2026-09`, секция B — 6 задач: CUR-004/005/006 (трекинг,
> done) + CUR-007 (`ctx_search` MCP-тул), CUR-008 (своп `AGENT_TOOLS`),
> CUR-009 (этот отчёт — документация и скилл догоняют код).

## 1. TL;DR

CUR-007 и CUR-008 реализованы кодом и тестами (PR #50, #62, оба открыты,
стек ветвей поверх `main`); CUR-009 закрывает разрыв между кодом и прозой:
AGENTS.md, CLAUDE.md, `docs/mcp-integration.md`, `deploy/launchd/README.md`,
`MASTER.md`, скилл `orchestrator` и сам RFC 25 всё ещё описывали состояние
«до свопа» (счётчики уже были верны — 6/21/129/133 — но текст обещал
`agent_pick` и «своп идёт планом»). Правки синхронизируют прозу с фактом:
профиль `agent` = 6 curator-тулов, `agent_pick`/`agent_get`/
`agent_complete`/`agent_release` видны только на `standard`/`full`.
Найдена и исправлена попутная неточность (F5: `ctx.*` строка каталога
занижала видимость трёх тулов). БД-статус секции B: 3/6 (`done`), три
оставшиеся (CUR-007/008/009) — `in_progress`, закрытие отложено на
after-merge (см. §4).

## 2. Deliverables

| # | Задача | PR | Статус в БД | Содержание |
|---|--------|----|-------------|------------|
| 1 | CUR-007 | [#50](https://github.com/Orange-hanter/cod-doc/pull/50) | `in_progress` | MCP-тул `ctx_search(project, query, scope?, limit?)` — тонкая обёртка `search_service.search`, lazy reindex пустого FTS-индекса (`ensure_index`) |
| 2 | CUR-008 | [#62](https://github.com/Orange-hanter/cod-doc/pull/62) | `in_progress` | Своп `AGENT_TOOLS` на curator-набор; `agent_capabilities()` отдаёт `role: "doc-curator"`, `forbidden`, `next_action_hint` → `ctx_drift`/`ctx_search`; инверсия SYM-006D тестов |
| 3 | CUR-009 | этот PR (`worktree-cur-009-docs-b` → `worktree-cur-008-agent-swap`) | `in_progress` | Документация/скилл/RFC догоняют CUR-007/008: AGENTS.md, CLAUDE.md, `docs/mcp-integration.md`, `deploy/launchd/README.md`, `MASTER.md` (×2), `cod_doc/skills/orchestrator/SKILL.md`, `proposals/25-doc-curator-agent.md`, `proposals/README.md`, этот аудит-отчёт |

## 3. Findings

- **F1 (MASTER.md:20, историческое).** Строка «126 MCP-тулов (профиль
  `agent` — 6), 12 скиллов, 6 ADR, 25 stories» — снимок закрытия M5
  (2026-09-06/07), зафиксированный явно как историческая метка. Оставлена
  без изменений по прямому указанию: правка обесценила бы фиксацию
  состояния на момент M5. Актуальные счётчики — 6/21/129/133 (см. §5.9
  AGENTS.md, `test_server_profiles.py`); измеренный этим проходом полный
  прогон — **2400 passed, 1 skipped** (`pytest tests/ --tb=short
  --timeout=120`, без `-n` — см. F3), не 1639. `CLAUDE.md` «Команды»
  оценивает «~2090» в комментарии к `-n auto --dist loadfile` — тоже
  устарело относительно факта; не правим здесь, чтобы не плодить ещё один
  быстро протухающий число в прозе, но фиксируем как data point.
- **F2 (поиск, блокирует CUR-012).** Живой FTS-индекс проекта `cod-doc`
  содержит только 63 строки типа `doc` — задачи и ADR не проиндексированы.
  `ctx_search`/`search_service.ensure_index` (CUR-007) делает lazy reindex
  только когда индекс **пуст**; непустой-но-неполный индекс (63/442 задач
  не входят) reindex не триггерит, и куратор получит частичную,
  вводящую-в-заблуждение выдачу по `task`/`adr`. Нужна отдельная задача в
  секции C («Search quality») — полный переиндекс по типам, не только
  lazy-on-empty. Кандидат: CUR-012.
- **F3 (тестовый гейт).** `pytest-xdist` не установлен в общем `.venv`
  (`pip show pytest-xdist` — «Package(s) not found»), хотя `CLAUDE.md`/
  `AGENTS.md`/CI документируют `-n auto --dist loadfile` как канонический
  полный прогон. Локальный прогон без `-n` (как в инструкции этой задачи)
  отрабатывает штатно — деградация только в скорости, не в корректности,
  но расхождение с задокументированной командой стоит закрыть (`pip
  install -e '.[dev]'` в общем `.venv` должен подтягивать `pytest-xdist`
  из `pyproject.toml` — проверить, не выпал ли пакет из extras).
- **F4 (переразвёртывание демона).** Постоянный HTTP-демон `:8802`
  (профиль `agent`) работает на пиннованной non-editable сборке
  `~/.cod-doc/runtime`, которая старше CUR-007: `ctx_search` отсутствует в
  каталоге тулов активной MCP-сессии этого хоста (проверено прямым
  вызовом `ToolSearch`/`mcp__cod-doc__ctx_search` — тул не резолвится, а
  `ctx_docs`/`ctx_drift` резолвятся). После мержа стека CUR-007…009 в
  `main` требуется пересборка колеса и `deploy/launchd/cod-doc-mcp-daemon.sh
  restart` для обоих демонов (`:8801`, `:8802`), иначе живые клиенты
  продолжат видеть pre-swap каталог.
- **F5 (самоправка в этом же проходе).** Таблица каталога MCP-инструментов
  в `docs/mcp-integration.md` (семейство `ctx.* (RFC 22 / RFC 25 §3.2)`)
  утверждала «Только профили standard/full» для всех четырёх `ctx_*`
  тулов — устарело с CUR-008, который завёл `ctx_docs`/`ctx_search`/
  `ctx_drift` в дефолтный `agent`. Исправлено этим проходом; остаётся
  только `ctx_drift_gate` как admin-only.
- **F6 (реестр аудитов не догнан).** `docs/system/MASTER.md` §5
  («Статусы документов пакета») в последний раз получал новую строку
  audit-отчёта 2026-07-29 (`audit/2026-07-29-state-of-the-project`); на
  диске с тех пор появилось ещё 9 отчётов
  (`2026-08-29-contract-audit` … `2026-09-11-sprint-m2-feedback-loop`) без
  строки в реестре. Этот проход добавляет только свою собственную запись
  (`2026-09-19-doc-curator-section-b`); backfill девяти пропущенных —
  отдельная небольшая задача, не блокирует CUR-009.

## 4. Plan health

| Секция | Задачи | Done | Статус |
|---|---|---|---|
| A — Policy lock | 3 | 3/3 | ✅ done |
| B — Agent surface swap | 6 | 3/6 в БД (CUR-004/005/006); CUR-007/008/009 — код/PR готовы, закрытие в БД отложено до мержа в `main` (протокол `task_complete` с `commit_sha`) | 🟡 код готов, БД закрытие после мержа |
| C — Search quality | 6 | 0/6 | ⏳ в очереди — F2 (частичный FTS-индекс) кандидат на первую задачу |
| D — Curator loop | 3 | 0/3 | ⏳ в очереди, не начата |

## 5. Acceptance (по критериям задачи CUR-009)

| Критерий | Итог |
|---|---|
| grep по 121/125 в перечисленных файлах пуст, кроме исторических упоминаний | ✅ — остались только `PCA-121` (task-id) и T11 в `proposals/25-doc-curator-agent.md`, помеченный как закрытый |
| `tests/test_orchestrator_skill_refs.py`, `tests/test_mcp_integration_doc.py` зелёные | ✅ (см. гейт в теле PR) |
| `cod-doc doc drift -p cod-doc --all` чист, хэши MASTER.md обновлены | 🟡 хэши пересчитаны локальным sha256-скриптом (см. отчёт PR — `cod-doc hash update` недоступен в этой песочнице); `doc drift`/`doc import` в общую БД из worktree сознательно не выполнялись (см. ниже) |
| Аудит-отчёт зарегистрирован в БД и MASTER | 🟡 зарегистрирован в `docs/system/MASTER.md` §5/§6 (markdown); регистрация в БД (`doc import`) откладывается на пост-мерж проход в основном чекауте — см. список файлов для импорта в теле PR |
| Зелёный CI | Локальный полный гейт (ruff/format/mypy/pytest) — см. PR; GitHub CI на stacked PR не запускается (архитектурное ограничение репозитория) |

## 6. Out of cycle

- **Секция C (Search quality).** F2 — полный реиндекс `task`/`adr`, не
  только `doc`; бюджет/лимиты `ctx_search`; кросс-проектный поиск как
  продолжение SYM-011 (CUR-013, см. правку `MASTER.md:31`).
- **Секция D (Curator loop).** `curator_next` агрегатор (RFC 25 §3.5),
  ревизия legacy `cod-doc agent run` daemon (RFC 25 §3.4).
- **F3/F6** — не блокируют CUR-009, переносятся как самостоятельные
  мелкие задачи (pytest-xdist в extras; backfill 9 audit-строк в
  `docs/system/MASTER.md` §5).
- **F4** — операционное действие после мержа (переразвёртывание демонов),
  не задача в плане.
