---
name: audit-cadence
description: |
  Закрытие фазы → audit-report в docs/system/audit/.
  Открытие новой фазы → kickoff-brief в docs/system/roadmap/.
  Триггеры: audit, phase, kickoff, close, complete, section, briefing,
  consolidation, milestone.
---

# Skill — Audit cadence

## Когда подгружается

Задачи, в которых пользователь / LLM **закрывают фазу работ**
(секцию, milestone, consolidation cycle) или **открывают новую**.
Триггер-keywords: `audit`, `phase`, `kickoff`, `close`, `complete
section`, `consolidation`, `milestone`, `closure`, `briefing`.

## Два события, два артефакта

| Событие | Артефакт | Куда кладётся |
|---------|----------|---------------|
| Закрытие фазы / секции / cycle | **audit-report** (`type: audit-report`) | `docs/system/audit/<YYYY-MM-DD>-<scope>-cycle-N.md` (или `<section>.md`) |
| Открытие новой фазы | **kickoff-brief** (`type: kickoff-brief`) | `docs/system/roadmap/<scope>-kickoff-<YYYY-MM-DD>.md` |

Пример: завершён Phase 1 paperclip-adoption → пишется
`audit/2026-XX-YY-paperclip-phase-1.md`. Стартует Phase 2 → пишется
`roadmap/paperclip-phase-2-kickoff-2026-XX-YY.md`.

## Скелет audit-report

```
---
type: audit-report
scope: <section / cycle name>
status: active
source_of_truth: true
owner: cod-doc core
created: <YYYY-MM-DD>
last_updated: <YYYY-MM-DD>
related_docs: [...]
---

# <Section / Cycle> — Closure / Audit Report

## 1. TL;DR
## 2. Deliverables (table: # / item / file / status)
## 3. Findings (numbered F1, F2, ...)
## 4. Plan health
## 5. Acceptance
## 6. Out of cycle (handed off → next)
```

## Скелет kickoff-brief

```
---
type: kickoff-brief
scope: <new phase / section>
status: active
source_of_truth: false
canonical_source: <path to execution-plan>
owner: cod-doc core
audience: [next-session-agent, contributors]
---

# <Phase> — Kickoff Brief

## 1. TL;DR
## 2. Контекст / Состояние
## 3. Первый tick (что сделать сразу)
## 4. Acceptance for this phase
## 5. Команды
```

## Правила

- В одном цикле — ровно **N** audit-отчётов на N циклов; findings одного
  цикла → backlog в следующем (паттерн «findings → tasks → next cycle»).
- Audit-report — `source_of_truth: true`, kickoff-brief — `false`
  (canonical_source указывает на execution-plan).
- При закрытии секции переключай связанные docs со `status: active` →
  `status: resolved` (для audit-отчётов прошлых циклов, чьи задачи
  закрыты).
- Kickoff-brief живёт до закрытия фазы; потом архивируется в audit-отчёт.

## Связанное

- [docs/system/MASTER.md](../../../docs/system/MASTER.md) — глобальный индекс
  audit/ + roadmap/.
- [standards/frontmatter.md](../../../docs/system/standards/frontmatter.md) —
  допустимые `type:` и `status:`.
