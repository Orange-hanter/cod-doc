---
type: audit-report
scope: documentation-consolidation
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - ../MASTER.md
  - ../../../MASTER.md
  - ../../../proposals/README.md
  - ../roadmap/cod-doc-task-plan.md
---

# Documentation Consolidation — Cycle 1 (Anchor & Disambiguate)

> **Назначение.** Зафиксировать находки по состоянию документации на старте 2026-05-07
> и оформить первую волну консолидации: устранить двойной MASTER, освежить L0-набор
> на корне, привести US-001..US-004 к фактическому состоянию delivered.

## 1. TL;DR

- **2 параллельных мастер-индекса** — `/MASTER.md` (загрязнён фикстурным заголовком
  `integration-test`, last_updated 2026-04-05) и `docs/system/MASTER.md` (актуальный,
  2026-05-02).
- **Stale L0-набор** (`/arch`, `/specs`, `/models`) — frontmatter v0.1/0.2 от
  2026-04-05; фактически живая архитектура описана в `docs/system/ARCHITECTURE.md`
  и `docs/system/DATA_MODEL.md`.
- **15 RFC** в `/proposals/` от 2026-05-06 не подключены к мастеру и не отражены в
  беклоге БД (58 done, 0 pending).
- **US-001..US-004 в draft**, фактически US-001/US-002/US-004 уже доставлены
  (commits `bb197bf`, `7e72b30`, `4441ce2`); US-003 покрыт инвентарём `tool_defs.py`
  (присутствуют все 6 целевых тулов).

## 2. Detailed Findings

### F1 — Двойной MASTER, корень с фикстурой

`/MASTER.md` начинается с заголовка `🧭 Project Navigator: integration-test` и
meta-блока, где `repo` указывает на `/private/var/folders/.../pytest-17/test_agent_run_full_cycle0/my-repo`.
Это явно остаток integration-теста, попавший в коммит. При этом все хеши в
Validation Table (5.1) совпадают с дисковыми (через `check_stale_refs` —
10/10 VALID), то есть **контент валиден, но meta вводит в заблуждение**.

**Решение цикла 1:** перепрофилировать `/MASTER.md` в тонкий навигатор-агрегатор,
указывающий на:
- `docs/system/MASTER.md` — system-of-truth для целевого состояния COD-DOC,
- `proposals/README.md` — каталог RFC,
- `arch/architecture.md`, `specs/modules.md`, `models/domain.md` — bootstrap-
  набор, оставляем для агентского L0-сценария, но помечаем явно legacy.

### F2 — Stale L0-набор

| Файл | meta.version | meta.last_updated | Реальный канонический документ |
|------|--------------|-------------------|---------------------------------|
| `arch/architecture.md` | 0.2 | 2026-04-05 | `docs/system/ARCHITECTURE.md` |
| `specs/modules.md` | 0.2 | 2026-04-05 | (нет прямого аналога — раскрыто в `docs/system/capabilities/`) |
| `models/domain.md` | 0.1 | 2026-04-05 | `docs/system/DATA_MODEL.md` |

Bootstrap-набор задумывался как L0-вход для агентского Snowball-протокола.
В `docs/system/` развился более глубокий пакет с capability-разбивкой. Вместо
удаления — обновляем frontmatter (статус `redirect` или ссылка на canonical),
оставляем существующий контент как валидный обзор, добавляем pointer на
`docs/system/`.

### F3 — Proposals не подключены к мастеру

`/proposals/` содержит 15 RFC по адаптации паттернов paperclip:

| Phase | Numbers | Тема |
|-------|---------|------|
| 1 | 01-04 | Skills layer, Heartbeat-context, Wake-payload, Run-id audit |
| 2 | 05, 09, 12 | Issue documents, Activity log, Approvals |
| 3 | 06, 07, 08, 11 | Atomic checkout, Routines (cron), Status taxonomy, AGENTS.md |
| 4 | 10 | Adapter pattern для LLM |
| n/a | 13, 14, 15 | Import UX, Legacy-tasks migration UX, Link system & rendering |

**Решение:** в Циклах 2-3 завести план `paperclip-adoption-task-plan` в
`docs/system/roadmap/`, kickoff-brief, и сгенерировать stories+tasks для
всех 15 предложений.

### F4 — Stories US-001..US-004 фактически delivered

Проверено по коду:

| Story | Acceptance ключевая | Код | Вывод |
|-------|---------------------|-----|-------|
| US-001 | context_refs в начальном промпте, превью ≤200 строк | `cod_doc/agent/orchestrator.py:176` `_render_context_refs(refs, max_lines=200)` | ✅ delivered |
| US-002 | forward_chain в начальном промпте | `cod_doc/agent/orchestrator.py:205` `_render_prerequisites(task)`, retry с убиранием MASTER | ✅ delivered |
| US-003 | tool palette = MCP palette | `cod_doc/agent/tool_defs.py` содержит все 6 целевых тулов: `plan_forward_chain`, `plan_reverse_chain`, `plan_ready`, `story_get`, `doc_body`, `link_list` | ✅ delivered |
| US-004 | Task с blocked_by/affects_files/acceptance/story_id | `cod_doc/core/project.py:43-60` поля присутствуют, сериализуются туда-обратно (`to_dict`/`from_dict`) | ✅ delivered |

**Решение цикла 1:** перевести US-001..US-004 в статус `delivered` через
`story_update_status`, добавить linked-доки на код-источник реализации.
Расхождение между `coverage()` derived-status (`draft` — нет привязанных
DB-задач) и pinned-status (`delivered`) фиксируем явно — DB-задачи сделаны
до того, как Stories вошли в схему; backfill историческими привязками не
делаем.

### F5 — Прочие наблюдения

- В DB зарегистрирован документ `arch/arch/architecture` с двойным
  префиксом — ошибочный bootstrap, кандидат на удаление в Цикле 4.
- DB-документ `MASTER` помечен `status: draft, source_of_truth: true`,
  тогда как сам файл живёт как L0 navigator. Привести к `redirect` или
  обновить frontmatter после Цикла 1.
- Свежие audit-файлы `2026-05-06-ai-usage-audit.md` и `2026-05-06-cli-vs-web-parity.md`
  не упомянуты в `docs/system/MASTER.md` — добавить в Цикле 4.

## 3. Cycle-1 deliverables

| # | Деливерабл | Файл/действие | Статус |
|---|------------|----------------|--------|
| D1 | Cycle-1 audit-report | `docs/system/audit/2026-05-07-doc-consolidation-cycle-1.md` | ✅ this file |
| D2 | `/MASTER.md` → thin navigator | rewrite | ⏳ |
| D3 | `arch/architecture.md` frontmatter refresh | edit meta + add canonical pointer | ⏳ |
| D4 | `specs/modules.md` frontmatter refresh | edit meta + add canonical pointer | ⏳ |
| D5 | `models/domain.md` frontmatter refresh | edit meta + add canonical pointer | ⏳ |
| D6 | US-001..US-004 → delivered | `story_update_status` + `story_link` на код | ⏳ |
| D7 | `docs/system/MASTER.md` changelog | append cycle-1 entry | ⏳ |

## 4. Out of cycle (handed off)

- **Cycle 2:** Phase 1 RFC (01-04) → kickoff brief + execution plan + stories +
  tasks.
- **Cycle 3:** Phase 2-4 RFC (05-12) и unscoped (13-15) → расширение плана.
- **Cycle 4:** link integrity, hash refresh, удаление мусорного doc-record
  `arch/arch/architecture`, нормализация doc-keys.
- **Cycle 5:** финальный close-out audit + memory updates.

## 5. Acceptance for cycle 1

- [ ] Корневой `/MASTER.md` не содержит фикстурного `integration-test`.
- [ ] Корневой `/MASTER.md` имеет prominent ссылки на `docs/system/MASTER.md`,
      `proposals/README.md`, `arch/architecture.md`, `specs/modules.md`,
      `models/domain.md`.
- [ ] У всех трёх legacy-доков (`arch/architecture.md`, `specs/modules.md`,
      `models/domain.md`) есть актуальный `last_updated: 2026-05-07` и
      явный pointer на canonical-источник.
- [ ] US-001..US-004 в статусе `delivered` с reason='cycle-1 verification'
      и хотя бы одним `story_link`.
- [ ] `docs/system/MASTER.md §6 Changelog` дополнен записью 2026-05-07.
- [ ] `check_stale_refs(cod-doc)` всё ещё 10/10 VALID после правок.
