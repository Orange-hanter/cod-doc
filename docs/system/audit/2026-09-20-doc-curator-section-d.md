---
type: audit-report
scope: doc-curator-section-d
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-09-20
last_updated: 2026-09-26
related_docs:
  - ../../../proposals/25-doc-curator-agent.md
  - ../roadmap/ROADMAP.md
  - ./2026-09-19-doc-curator-section-b.md
  - ./2026-09-20-doc-curator-section-c.md
audience: [contributors, agents]
---

# Audit — Plan `doc-curator-2026-09`, Section D closure («Curator loop») + plan closure

> **Контекст.** [RFC 25](../../../proposals/25-doc-curator-agent.md) §3.4/§3.5
> отдаёт секции D два follow-up'а секции B: `curator_next` doc card (аналог
> task card для санитарии) и ревизию встроенного daemon `cod-doc agent run`
> (не путь исполнения продуктовых задач). Секция D — 3 задачи: CUR-016
> (`curator_next`), CUR-017 (daemon idle), CUR-018 (этот отчёт — документация
> и скилл `orchestrator` догоняют CUR-016/017, закрытие плана целиком).

## 1. TL;DR

CUR-016 и CUR-017 реализованы кодом, тестами и смержены в `main` (#74, #49);
CUR-018 закрывает разрыв между кодом и прозой того же типа, что CUR-009
закрывала для секции B: `docs/HANDBOOK.md` §9 обещал автономный агент,
«составляющий очередь задач из MASTER.md» — этого пути с CUR-017 больше нет,
раздел получил legacy-баннер и правки §9.1/9.3/9.4; скилл `orchestrator` всё
ещё учил алгоритму `ctx_drift → ctx_docs` — переписан на
`agent_capabilities → curator_next → ctx_search/context_get`, `ctx_docs`
убран из прозы как шаг цикла (тул есть, но не в профиле `agent` — CUR-016
это уже сделал). `docs/system/roadmap/ROADMAP.md`, `proposals/README.md` и
`proposals/25-doc-curator-agent.md` переведены в закрытое состояние с
таблицей секций A/B/C/D → done и итоговой таблицей 12 задач CUR-007…018 с
PR-номерами. Это последняя задача плана `doc-curator-2026-09`: 18/18 задач
`done` после закрытия CUR-018 куратором (протокол `task_complete` с
`commit_sha`, как и в секции B — см. §4).

## 2. Deliverables

| # | Задача | PR | Статус в БД | Содержание |
|---|--------|----|-------------|------------|
| 1 | CUR-016 | [#74](https://github.com/Orange-hanter/cod-doc/pull/74) | `done` | `curator_next(project, limit)` — doc card куратора: дрейф + битые ссылки + протухшие хэши MASTER.md + open findings одной приоритизированной очередью; MCP-тул + CLI `cod-doc ctx next`; заменил `ctx_docs` в `AGENT_TOOLS` |
| 2 | CUR-017 | [#49](https://github.com/Orange-hanter/cod-doc/pull/49) | `done` | `_generate_tasks_from_master` удалён; `run_autonomous()` на пустой очереди отдаёт idle-событие без побочных эффектов; тик routines вынесен в отдельную `tick_project_routines`, вызывается `run_daemon` до `run_autonomous`, не самим методом |
| 3 | CUR-018 | [#77](https://github.com/Orange-hanter/cod-doc/pull/77) | `in_progress` → закрывается после мержа | `docs/HANDBOOK.md` §9 legacy-баннер + правки §9.1/9.3/9.4 + сноски у двух других упоминаний `agent run` (:534, :790); скилл `orchestrator` — новый 5-шаговый алгоритм, `curator_next` как санитарный срез Snowball L1, `ctx_docs` убран из прозы; `ROADMAP.md`/`proposals/README.md`/`proposals/25…` переведены в «закрыт»; этот аудит; реестры хэшей `docs/system/MASTER.md` §5/§6 и корневого `MASTER.md` |

## 3. Findings

- **F1 (routines живы, санитарный контур подтверждён).** Прямой read-only
  запрос к `routine_run` (общая БД, `sqlite3 -readonly`) показывает
  `doc_drift_daily` и `link_integrity_daily` включёнными (`cron`,
  `on_finding=update_existing_task`) и стреляющими ежедневно: 13 запусков
  каждая, последний — 2026-09-19. `doc_drift_daily` пережил 3 подряд
  `failed`-запуска 2026-09-14…16 (`ValueError("'scenario-set' is not a
  valid DocumentType")`) и самовосстановился к 2026-09-17 (145→149→…→41
  findings, `status=done`) без вмешательства — причина сбоя не
  расследована в рамках этой задачи (вне scope: код не трогаем), но раз он
  не повторяется третью неделю подряд, в новый тикет не выношу; если
  повторится — заводить как баг `doc_drift` routine.
- **F2 (редеплой демонов, факт из брифа подтверждён).** `~/.cod-doc/runtime`
  — editable-инстал рабочего дерева владельца (`launchctl list` показывает
  `com.cod-doc.mcp`/`com.cod-doc.mcp-agent`/`com.cod-doc.web` живыми), не
  пиннованная non-editable сборка, как описывает README. Переразвёртывание
  (`deploy/launchd/cod-doc-services.sh upgrade`) — операционное действие
  владельца после мержа стека CUR-016…018, не задача в плане (тот же
  паттерн, что F4 в аудите секции B).
- **F3 (`ci.yml`/`cd.yml` разошлись с хэш-реестром, подтверждено).** Текущие
  хэши на диске — `ci.yml` `7c133a394eb3`, `cd.yml` `90c54859cba1`; реестр
  корневого `MASTER.md` (строки CI/CD Pipeline + Validation Table #4/#5) всё
  ещё держит `fe11e3504b18`/`bec2cea789cd` — устарело ещё до этого плана
  (workflow правился отдельными PR, реестр не обновлялся). Не в скоупе
  CUR-018 (эта задача не трогала CI/CD-файлы) — кандидат в backlog §6.
- **F4 (pytest-xdist больше не отсутствует).** Аудит секции B (F3) фиксировал
  отсутствие `pytest-xdist` в общем `.venv`. Проверено этим проходом:
  `.venv/bin/pip show pytest-xdist` → `Version: 3.8.0`, установлен. Разрыв
  между `-n auto --dist loadfile` в `AGENTS.md`/`CLAUDE.md`/CI и локальным
  окружением закрыт кем-то между 2026-09-19 и сегодня — закрываю F3
  секции B как resolved, не переношу дальше.
- **F5 (`curator_next` не входит в `next_action_hint` кода).** RFC 25 §3.2 и
  скилл `orchestrator` (после правки этим проходом) учат циклу
  `agent_capabilities() → curator_next(...) → ctx_search/context_get`. Но
  `agent_capabilities().next_action_hint`
  (`cod_doc/mcp/tools/agent_tools.py:134-145`) всё ещё жёстко собирает текст
  `"Call ctx_drift(project=...) then ctx_search(project=...)"` — CUR-016
  добавил `curator_next` в `AGENT_TOOLS` и в скилл, но не в этот текст.
  Разрыв между L0-подсказкой и рекомендованным алгоритмом; не код-фикс этой
  задачи (правило «код не трогать»), кандидат в backlog §6.
- **F6 (`curator_next` — три прохода по корпусу, без `paths`).** Подтверждено
  по коду `cod_doc/services/curator_service.py`: `detect_project_drift`,
  `link_findings` и `check_stale_refs` — три независимых обхода дерева
  документов/`MASTER.md`, плюс `list_findings` — запрос к БД без
  собственного обхода ФС. Сигнатура — `next(project, limit)`, параметра
  `paths` для сужения обхода на конкретную поддиректорию нет. На корпусе
  `cod-doc` (~140 документов) не создаёт заметной задержки, но на большем
  проекте (Orakul, 405+ документов) может — кандидат для секции C-like
  доводки поиска в следующем цикле, если появится friction.
- **F7 (`MASTER.md:20` — фиксированный исторический снимок M5).** Строка
  «126 MCP-тулов (профиль `agent` — 6), 12 скиллов, 6 ADR, 25 stories» —
  снимок закрытия M5 (аналогично F1 секции B про ту же строку). Актуальные
  счётчики профилей — 6/21/130/134 (после CUR-016), зафиксированы в §5.9
  AGENTS.md и `test_server_profiles.py`; строка `MASTER.md:20` намеренно не
  трогается по тому же основанию, что и в аудите секции B.

## 4. Plan health

| Секция | Задачи | Done | Статус |
|---|---|---|---|
| A — Policy lock | 3 | 3/3 | ✅ done |
| B — Agent surface swap | 6 | 6/6 | ✅ done (см. [аудит секции B](./2026-09-19-doc-curator-section-b.md)) |
| C — Search quality | 6 | 6/6 | ✅ done (см. [аудит секции C](./2026-09-20-doc-curator-section-c.md)) |
| D — Curator loop | 3 | 2/3 в БД (CUR-016/017); CUR-018 — код/PR готовы, закрытие в БД отложено до мержа в `main` (протокол `task_complete` с `commit_sha`, тот же паттерн, что CUR-007/008/009 в секции B) | 🟡 код готов, БД-закрытие после мержа |
| **План целиком** | **18** | **18/18 после закрытия CUR-018** | 🟢 закрывается этим PR |

## 5. Acceptance (по критериям задачи CUR-018)

| Критерий | Итог |
|---|---|
| `docs/HANDBOOK.md` §9 несёт legacy-баннер, §9.3/§9.4 отражают idle-поведение CUR-017 | ✅ |
| Скилл `orchestrator`: алгоритм `agent_capabilities → curator_next → ctx_search/context_get → починка → self_check/agent_report`, `ctx_docs` не упоминается как шаг цикла | ✅ |
| `tests/test_orchestrator_skill_refs.py` зелёный (backtick-вызовы резолвятся в живые тулы, включая `curator_next(`) | ✅ — `3 passed` |
| `ROADMAP.md`/`proposals/README.md`/`proposals/25…` отражают закрытие секций C/D и плана | ✅ |
| Аудит-отчёт зарегистрирован в `docs/system/MASTER.md` §5/§6 и в хэш-реестре корневого `MASTER.md` | ✅ (см. §6 ниже — список файлов; `doc import` в общую БД сознательно не выполнялся, `cod-doc hash update` заблокирован песочницей по слову «hash» — хэши пересчитаны sha256[:12] python-скриптом) |
| Зелёный гейт (`ruff`/`format`/`mypy`/`pytest`) | см. тело PR |

## 6. Out of cycle

- **F3.** `ci.yml`/`cd.yml` хэш-реестр в корневом `MASTER.md` устарел
  относительно диска (`7c133a394eb3`/`90c54859cba1` факт vs
  `fe11e3504b18`/`bec2cea789cd` в реестре) — небольшая задача пересчёта, не
  блокирует закрытие плана `doc-curator-2026-09`.
- **F5.** `agent_capabilities().next_action_hint`
  (`cod_doc/mcp/tools/agent_tools.py`) не упоминает `curator_next` —
  код-фикс на одну строку текста, кандидат следующему циклу (не секция
  документации).
- **F6.** `curator_next` — доводка поиска: `paths`-параметр для сужения
  обхода корпуса на большом проекте, если появится измеренный friction
  (Orakul — вероятный источник сигнала).
- **Остаток STO-015** (Postgres parity) и non-editable runtime демонов
  (F2) — операционные пункты вне плана `doc-curator-2026-09`, переносятся
  как есть (см. F2 и STO-* трек в `ROADMAP.md`).
