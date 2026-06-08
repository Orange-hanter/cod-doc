---
type: audit-report
scope: agent-tools-completion
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-06-08
last_updated: 2026-06-08
related_docs:
  - ../roadmap/agent-tools-completion-task-plan.md
  - ../roadmap/ROADMAP.md
  - 2026-06-04-self-improvement-compared.md
audience: [contributors, agents]
---

# Audit — Agent Tools Completion (Section H / STB-001)

> **Контекст.** Self-improvement audit ([2026-06-04](2026-06-04-self-improvement-compared.md))
> поднял P0-1: cycle-5 agent-profile тулы (AGT-003..007) выглядели как
> `NotImplementedError` stubs. Повторная сверка с кодом показала, что bodies
> **уже реализованы** в `services/agent_service.py`, а реальный gap — это
> (а) устаревший docstring и (б) отсутствие end-to-end покрытия через
> `mcp.call_tool`. Этот аудит фиксирует закрытие P0-1 (задача STB-001 плана
> `stabilization-2026-06`).

## TL;DR

Cycle-5 agent profile полностью функционален. Закрыты обе дыры:

- **Docstring drift** — комментарий в `agent_tools.py` («AGT-003..AGT-007 — MCP
  wrappers… Bodies live in `cod_doc.services.agent_service` and have full test
  coverage») честно описывает thin-wrapper-слой; устаревшая формулировка
  «Bodies will be implemented» отсутствует.
- **End-to-end покрытие** — `tests/integration/test_agent_profile_mcp.py`
  гоняет все 6 тулов через настоящую MCP-сессию (`ClientSession.call_tool`).

P0-1 закрыт.

## Скоуп

| ID | Что сделано |
|----|-------------|
| AGN-001 | Docstring в `agent_tools.py` (AGT-003..007 block) — корректный thin-wrapper текст без «to be implemented». |
| AGN-002 | `AGENTS.md` cycle-5: agent-profile описан как реализованный (services + MCP-обёртки). |
| AGN-010 | `tests/integration/test_agent_profile_mcp.py` — фикстуры + `agent_capabilities` smoke (tools/list == 6 имён `AGENT_TOOLS`). |
| AGN-011 | `agent_pick` end-to-end: seed ready task → `call_tool("agent_pick")` → assert task card (task / context / navigation / applicable_skills / success_criteria / legal_status_transitions), статус → `in-progress`. |
| AGN-012 | `agent_complete` (status → `done`, lock released в БД) + `agent_release` (status → `todo`, lock released). |
| AGN-013 | `agent_get` (unknown `what` → `found:false` + `legal_what`), `agent_report` (`kind=progress` → `ok:true` + `next_actions`). |
| AGN-020 | Этот audit-отчёт. |

## Покрытие тестами

`tests/integration/test_agent_profile_mcp.py` — **6 тестов**, все через
реальную MCP-сессию (stdio/in-memory `ClientSession`), а не прямой вызов
service-функций:

| Тест | Тулы | Проверка |
|------|------|----------|
| `test_agn010_agent_capabilities_smoke` | `agent_capabilities` | tools/list == `AGENT_TOOLS`; ключи caps; profile=`agent`; canonical statuses |
| `test_agn011_agent_pick_end_to_end` | `agent_pick` | полный task card; статус → in-progress |
| `test_agn012_agent_complete_flow` | `agent_pick`+`agent_complete` | `ok:true`, `done`, lock released (БД) |
| `test_agn012_agent_release_flow` | `agent_pick`+`agent_release` | `ok:true`, `todo`, lock released (БД) |
| `test_agn013_agent_get_unknown_what` | `agent_get` | `found:false`, `legal_what` |
| `test_agn013_agent_report_progress` | `agent_report` | `ok:true`, `next_actions` |

**Результат прогона:** `6 passed` (≈60s, in-memory MCP client + async).
Все 6 тулов профиля `agent` покрыты end-to-end.

## Findings

- **F-H1 (resolved).** Docstring-drift из self-improvement audit неактуален —
  код и комментарий синхронны.
- **F-H2 (resolved).** Отсутствие MCP-level покрытия закрыто: было 0 e2e-тестов
  на уровне `call_tool`, стало 6.
- **F-H3 (note).** Тест seedит задачи через service-слой и резолвит проект
  через workspace-fallback; docker-MCP (`docker exec cod-doc`) не требуется для
  прогона — in-memory `ClientSession` достаточно.

## Acceptance

- [x] Docstring корректен (AGN-001).
- [x] `AGENTS.md` cycle-5 точен (AGN-002).
- [x] 6 integration-тестов через `mcp.call_tool` зелёные (AGN-010..013).
- [x] Audit-отчёт (AGN-020).

## Next step

- AGN-021 (manual smoke через `cod-doc-mcp --profile agent`) — опционально;
  требует распауза Docker. Не блокирует закрытие P0-1.
- Перейти к STB-002 (удаление legacy YAML-модулей) — следующий P0 в Треке A.
