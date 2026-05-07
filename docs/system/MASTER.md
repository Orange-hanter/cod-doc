---
type: documentation-master
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-05-07
audience: [contributors, agents]
related_code:
  - cod_doc/core/project.py
  - cod_doc/mcp/server.py
  - cod_doc/agent/orchestrator.py
  - cod_doc/api/routes.py
---

# COD-DOC System Documentation — Master Index

> Пакет описывает **целевое состояние** COD-DOC как автоматизированной системы управления проектной документацией с БД-бэкендом.
> Исходная точка (manual baseline): проект `~/Git/Restate` — полный ручной стек Obsidian + markdown-стандартов + MCP-серверов + LightRAG, который сейчас требует значительных человеческих усилий и постоянных ручных проверок (`node tools/task-plan-audit.mjs --strict`, вручную прописываемые ссылки, дублирующийся changelog, и т. д.).
> Целевая точка: COD-DOC хранит тот же граф знаний в БД, генерирует markdown-проекции как артефакты, валидирует и линкует всё автоматически.

---

## 1. Как читать пакет

Входные точки для разных ролей:

| Роль | Начать с | Затем |
|------|----------|-------|
| Продукт/видение | [VISION.md](VISION.md) | [capabilities/](capabilities/) |
| Архитектор | [ARCHITECTURE.md](ARCHITECTURE.md) | [DATA_MODEL.md](DATA_MODEL.md) |
| Разработчик ядра | [DATA_MODEL.md](DATA_MODEL.md) | [capabilities/](capabilities/), [roadmap/cod-doc-task-plan.md](roadmap/cod-doc-task-plan.md) |
| Автор контента | [standards/](standards/) | [capabilities/doc-evolution.md](capabilities/doc-evolution.md) |
| Мигратор с Restate | [migration/from-restate.md](migration/from-restate.md) | [DATA_MODEL.md](DATA_MODEL.md) |
| Агент/LLM | [capabilities/context-retrieval.md](capabilities/context-retrieval.md) | `MASTER.md` проекта |

---

## 2. Структура пакета

```text
docs/system/
├── MASTER.md                       ← этот файл (навигация)
├── VISION.md                       ← какие задачи COD-DOC закрывает
├── ARCHITECTURE.md                 ← слои и границы сервисов
├── DATA_MODEL.md                   ← сущности БД и связи
│
├── standards/                      ← форматы файлов и полей
│   ├── frontmatter.md
│   ├── task-plan.md
│   ├── document-link.md
│   ├── revision-history.md
│   └── sensitive-data.md           ← классификация и redaction
│
├── capabilities/                   ← целевые возможности, по одной на файл
│   ├── task-creation.md
│   ├── doc-evolution.md
│   ├── auto-linking.md
│   ├── context-retrieval.md
│   ├── plan-management.md
│   ├── user-stories-graph.md
│   ├── decisions-and-questions.md  ← ADR + Open Questions (free-form)
│   ├── adr-system.md               ← ADR как first-class entity (визуально + MCP)
│   ├── observability-and-indexing.md ← метрики, commit-integration, code-refs, repo+DB index (опционально)
│   ├── agents-and-skills.md        ← каталог агентов
│   ├── project-bootstrap.md        ← `cod-doc project new`
│   ├── web-frontend.md             ← server-rendered Web UI (Jinja + HTMX)
│   └── audit-and-ci.md             ← каталог проверок + git/CI
│
├── audit/
│   ├── 2026-04-19-initial-audit.md         ← первый формальный аудит пакета
│   ├── 2026-04-25-section-a-data-core.md   ← аудит ядра (Section A)
│   ├── 2026-04-25-section-b-services.md    ← аудит сервисов (Section B)
│   ├── 2026-04-28-section-c-capabilities.md ← аудит capability-layer (Section C)
│   ├── 2026-05-01-section-g-hardening.md   ← закрытие hardening (Section G)
│   ├── 2026-05-02-section-web-frontend.md  ← аудит web-секции (после WEB-001..011)
│   ├── 2026-05-02-checkpoint-web-batch-1..4.md ← mid-section checkpoints
│   ├── 2026-05-06-ai-usage-audit.md        ← аудит usage AI в системе
│   ├── 2026-05-06-cli-vs-web-parity.md     ← сравнение CLI и Web UI surface'ов
│   └── 2026-05-07-doc-consolidation-cycle-{1..5}.md ← цикловые аудиты консолидации
│
├── migration/
│   └── from-restate.md             ← как перевезти реальное состояние Restate
│
└── roadmap/
    ├── cod-doc-task-plan.md                  ← план внедрения (dogfood формата task-plan)
    ├── web-frontend-task-plan.md             ← план Web UI поверх FastAPI
    ├── web-frontend-kickoff-2026-05-02.md    ← brief на старт Section F (после аудита)
    ├── audit-followups-task-plan.md          ← фиксы пакета по аудиту
    ├── refactor-large-files-task-plan.md     ← план рефакторинга крупных файлов
    ├── paperclip-adoption-task-plan.md       ← план заимствований из paperclip (15 RFC → 44 задачи)
    ├── paperclip-adoption-kickoff-2026-05-07.md ← brief на Phase 1 paperclip
    ├── adr-system-task-plan.md               ← план ADR-системы (capability + visual UI, 8 задач)
    └── observability-and-indexing-task-plan.md ← опциональный план: метрики/commits/code-refs/repo-index/DB-index (8 задач, 5 stories US-021..US-025)
```

---

## 3. Источник истины

- **Пакет описаний (`docs/system/`)** — source of truth для поведения системы.
- **Код (`cod_doc/`)** — реализация; любое расхождение с пакетом — баг либо описания, либо кода.
- **БД проекта (`.cod-doc/state.db`)** — source of truth для содержимого отдельного пользовательского проекта. Markdown-файлы — проекции/экспорты.

Правило разрешения конфликтов:
1. Если в БД и markdown разное состояние и markdown не помечен как edited — перезаписываем markdown из БД.
2. Если markdown отредактирован вручную (hash изменился без соответствующего revision в БД) — агент запускает reconciliation-flow (см. [capabilities/doc-evolution.md](capabilities/doc-evolution.md)).

---

## 4. Соотнесение с возможностями из запроса

Запрос пользователя → конкретный документ пакета:

| Запрошенная возможность | Описано в |
|-------------------------|-----------|
| Создание задач (стандартизировано, автоматически) | [capabilities/task-creation.md](capabilities/task-creation.md) + [standards/task-plan.md](standards/task-plan.md) |
| Развитие документации | [capabilities/doc-evolution.md](capabilities/doc-evolution.md) |
| Автолинковка ссылок | [capabilities/auto-linking.md](capabilities/auto-linking.md) + [standards/document-link.md](standards/document-link.md) |
| Ссылки на документы | [standards/document-link.md](standards/document-link.md) |
| История изменений | [standards/revision-history.md](standards/revision-history.md) |
| Получение концентрированного контекста | [capabilities/context-retrieval.md](capabilities/context-retrieval.md) |
| Ведение плана | [capabilities/plan-management.md](capabilities/plan-management.md) + [standards/task-plan.md](standards/task-plan.md) |
| Пользовательские истории и граф зависимостей | [capabilities/user-stories-graph.md](capabilities/user-stories-graph.md) |

---

## 5. Статусы документов пакета

| Документ | Статус | Владелец |
|----------|--------|----------|
| VISION | draft | cod-doc core |
| ARCHITECTURE | draft | cod-doc core |
| DATA_MODEL | draft | cod-doc core |
| standards/* | draft | cod-doc core |
| capabilities/* | draft | cod-doc core |
| migration/from-restate | draft | cod-doc core |
| roadmap/cod-doc-task-plan | active | cod-doc core |
| roadmap/audit-followups-task-plan | active | cod-doc core |
| roadmap/web-frontend-task-plan | active | cod-doc core |
| audit/2026-04-19-initial-audit | active | cod-doc core |
| audit/2026-04-25-section-a-data-core | resolved | cod-doc core |
| audit/2026-04-25-section-b-services | resolved | cod-doc core |
| audit/2026-04-28-section-c-capabilities | resolved | cod-doc core |
| audit/2026-05-01-section-g-hardening | resolved | cod-doc core |
| audit/2026-05-02-section-web-frontend | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-1 | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-2 | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-3 | resolved | cod-doc core |
| audit/2026-05-02-checkpoint-web-batch-4 | resolved | cod-doc core |
| capabilities/web-frontend | active | cod-doc core |

Пока пакет в статусе `draft` — изменения допустимы без revision-истории. После `active` любая правка обязана вести к revision-записи (см. [standards/revision-history.md](standards/revision-history.md)). Статус `resolved` — для audit-отчётов, чьи задачи закрыты (см. [standards/frontmatter.md §7](standards/frontmatter.md)).

---

## 6. Changelog

| Дата | Событие |
|------|---------|
| 2026-04-19 | Начальная версия пакета; базовая структура, стандарты и capabilities. |
| 2026-04-19 | Проведён первый аудит ([audit/2026-04-19-initial-audit.md](audit/2026-04-19-initial-audit.md)); закрыто 6 задач (HI-1..5, LO-1) стабами; заведён follow-up план ([roadmap/audit-followups-task-plan.md](roadmap/audit-followups-task-plan.md)) с 17 оставшимися задачами. |
| 2026-04-25 | Аудиты Section A (Data Core) и Section B (Services) — оба `resolved`; см. [audit/2026-04-25-section-a-data-core.md](audit/2026-04-25-section-a-data-core.md), [audit/2026-04-25-section-b-services.md](audit/2026-04-25-section-b-services.md). |
| 2026-04-28 | Добавлен `standards/sensitive-data.md` в индекс §2; зафиксирован пробел: инфраструктура (scanner, redaction, фильтры контекста) пока отсутствует — вынесена в задачу COD-025. |
| 2026-04-28 | Аудит capability-layer (Section C) — выявлены пробелы: нет CI workflow (COD-024), LinkService.rename не каскадит body (COD-014a), web-layer обходит сервисы (WEB-020), TUI без тестов (COD-026). |
| 2026-05-01 | Section G (Hardening & DevX) закрыта целиком — 5/5 задач: COD-024a (strict ruff/mypy debt cleared, CI gates blocking), COD-014a (markdown-relative rename cascade), COD-025 (sensitive-data infrastructure: scanner+FM-007+SD-001 audit+SD-002 redaction+clearance helper), COD-026 (TUI smoke tests, попутно фикс bug в WizardScreen). Suite 402/402; см. [audit/2026-05-01-section-g-hardening.md](audit/2026-05-01-section-g-hardening.md). |
| 2026-05-02 | Аудит web-секции после закрытия Section A (Scaffold) + WEB-010/011 — см. [audit/2026-05-02-section-web-frontend.md](audit/2026-05-02-section-web-frontend.md). 16 находок (4 high, 7 medium, 5 low); 13 новых задач заведены в [roadmap/web-frontend-task-plan.md](roadmap/web-frontend-task-plan.md) (новая Section F: Hardening — WEB-005, 013, 022 ↑, 050..053; Section E расширена WEB-041, 042; Section B — WEB-006, 014, 060). WEB-040 и WEB-022 повышены до `high`. capability `web-frontend` переведён в `active`, добавлены §11 «Текущее состояние» и DI-конвенция в §7. Реализовано 5/14 endpoints (~36 %). |
| 2026-05-02 | Section F batch-1 закрыт: 5 задач (WEB-005 engine cache + DI helpers, WEB-040 web→infra bypass снят, WEB-022 alert/error model, WEB-041 tab strip include + status_options Jinja global, WEB-013 batch stats + pagination). 11 / 16 находок baseline-аудита закрыты. Suite 418 → 441; web-tests 27 → 66. Audit-отчёт `2026-04-28-section-c-capabilities` переведён в `resolved` (последняя его задача SC-HI-3 закрыта в WEB-040). Сделан checkpoint-аудит [audit/2026-05-02-checkpoint-web-batch-1.md](audit/2026-05-02-checkpoint-web-batch-1.md): 4 новых внутренних item-а (WEB-013b/022b/053 ↑/054). |
| 2026-05-02 | Section B batch-2 + полировка: 4 коммита (WEB-006 server-rendered markdown for doc_show, WEB-013b/022b/054 polish bundle из checkpoint #1, WEB-014 overview agg ready/progress/recent + POST .../complete, WEB-021 revisions log + filter). 13 / 16 находок baseline закрыты (SW-ME-3, SW-ME-7 в этом батче). Suite 441 → 483; web-tests 66 → 108. Endpoints shipped 5/14 → 8/14 (~57 %). Сделан checkpoint-аудит [audit/2026-05-02-checkpoint-web-batch-2.md](audit/2026-05-02-checkpoint-web-batch-2.md). |
| 2026-05-02 | Batch-3 + Section B closed: 3 коммита (WEB-004 plan view + Mermaid `<pre>`, WEB-060 settings page, WEB-051 static asset versioning). **Section B (Read views) — 6/6 done.** 14 / 16 находок baseline закрыты (SW-LO-1 в этом батче). Suite 483 → 500; web-tests 108 → 125. Endpoints shipped 8/14 → 10/14 (~71 %). 5 из 6 табов live (только Run остался disabled). Checkpoint-аудит [audit/2026-05-02-checkpoint-web-batch-3.md](audit/2026-05-02-checkpoint-web-batch-3.md). Inline fix: documentированы lifespan-vs-set_config footgun в `tests/api/conftest.py`. |
| 2026-05-02 | Batch-4 + Section C closed + baseline-аудит resolved: 2 коммита (WEB-012 HTMX section patch, polish bundle WEB-052/053/053b/014b). **Section C (Write paths) — 3/3 done.** **16 / 16 baseline-аудит findings закрыты** (SW-LO-2/3/5 в этом батче). Suite 500 → 512; web-tests 125 → 137. Endpoints shipped 10/14 → 13/14 (~93 %). Audit-отчёт `2026-05-02-section-web-frontend` переведён в `resolved`. Checkpoint-аудит [audit/2026-05-02-checkpoint-web-batch-4.md](audit/2026-05-02-checkpoint-web-batch-4.md). Остаётся только Section D (WEB-030/031 — SSE run console). |
| 2026-05-07 | **Documentation Consolidation — Cycle 1 (Anchor & Disambiguate).** Корневой `/MASTER.md` переписан как тонкий L0-навигатор → `docs/system/MASTER.md` + `proposals/README.md` + L0 bootstrap docs (фикстурный `integration-test`-заголовок устранён). Frontmatter обновлён в `arch/architecture.md`, `specs/modules.md`, `models/domain.md` (status: legacy-overview, canonical_source, last_updated 2026-05-07; хеши пересчитаны через `update_master_hashes` — 3/3 obs). Stories US-001..US-004 переведены в `delivered` после code-verification (orchestrator/_render_context_refs+_render_prerequisites, tool_defs.py 6/6, Task struct fields), привязаны к capability/standards-докам через `story_link`. Аудит-отчёт: [audit/2026-05-07-doc-consolidation-cycle-1.md](audit/2026-05-07-doc-consolidation-cycle-1.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 2 (Phase 1 backlog).** Заведён `paperclip-adoption-task-plan` + Section A в БД (через прямой `PlanRepository.add` — gap G1 в MCP-API), kickoff brief в `roadmap/`, 4 stories US-005..US-008 (`accepted`), 17 задач PCA-001..PCA-034. Зафиксированы 3 API-gap'а в Cycle-2 audit: G1 нет MCP-API создания плана, G2 task_create.blocked_by не персистится в dependency-edges, G3 task_create.story_id и .affects_files не персистятся. Аудит-отчёт: [audit/2026-05-07-doc-consolidation-cycle-2.md](audit/2026-05-07-doc-consolidation-cycle-2.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 3 (Phase 2-4 + UX + Tooling).** Все 15 RFC из `/proposals/` теперь имеют структурированный беклог: 11 новых stories US-009..US-019, 5 новых секций B/C/D/E/F в DB plan, 26 новых задач PCA-100..PCA-422 + PCA-901..PCA-903. Section F вынесена для tooling-фиксов G1/G2/G3 (PCA-901..PCA-903; PCA-902 `critical` как блокер базовых plan_ready/plan_audit/critical_path сценариев). Plan total: **43 задачи** (17/6/7/3/7/3 по секциям A..F). Аудит-отчёт: [audit/2026-05-07-doc-consolidation-cycle-3.md](audit/2026-05-07-doc-consolidation-cycle-3.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 4 (Cross-links & Integrity).** `link_list` показал 39 broken markdown-refs на `docs/system/MASTER` — обнаружен gap **G4** (link_service не резолвит relative-paths против source-doc directory) → расширил scope PCA-421 в plan paperclip-adoption. Doc-record `arch/arch/architecture` идентифицирован как фикстурный реликт (commit e51e85f, 2026-04-05). `doc_drift` для root `MASTER` и `docs/system/MASTER` — `stale_export` после edit-in-place (известное состояние). Cycle-2/3 audit-доки зарегистрированы как doc-records (active). `check_stale_refs` остаётся 10/10 VALID. Аудит-отчёт: [audit/2026-05-07-doc-consolidation-cycle-4.md](audit/2026-05-07-doc-consolidation-cycle-4.md). |
| 2026-05-07 | **Documentation Consolidation — Cycle 5 (Final Close-out).** Сводка по 5 циклам: +44 pending tasks (44 задачи в paperclip-adoption-task-plan), +15 stories (US-005..US-019, всего 19), +5 audit-отчётов, +6 doc-records, +2 roadmap-файлов. Заведён PCA-911 (low) для уборки `arch/arch/architecture.md` фикстуры. Memory обогащена двумя feedback-патернами: `mcp_field_persistence_gap` (echo-but-no-persist) и `consolidation_cycle_pattern` (N циклов → N audit-отчётов). Реализация PCA-001..PCA-911 намеренно не запущена в этом сеансе — это отдельный длинный фронт работ. Финальный аудит-отчёт: [audit/2026-05-07-doc-consolidation-cycle-5-final.md](audit/2026-05-07-doc-consolidation-cycle-5-final.md). |
| 2026-05-07 | **ADR System capability добавлена.** Заведена capability [adr-system](capabilities/adr-system.md) (Architecture Decision Records как first-class entity с автонумерацией, supersede-DAG, визуальным редактором и Mermaid-графом в Web UI). Story US-020 (`accepted`). Новый план [adr-system-task-plan](roadmap/adr-system-task-plan.md), 3 секции (Domain & MCP, Web UI, Templates & Migration), 8 задач ADR-001..ADR-008. Старт реализации после закрытия Section F paperclip-плана. |
| 2026-05-07 | **Observability & Indexing capability добавлена (опциональная).** Capability [observability-and-indexing](capabilities/observability-and-indexing.md) — метрики выполнения задач, commit→task linkage для истории работ, code-refs `[label](src/path.py)` в markdown, RepoIndex (.gitignore-aware symbols/imports), DBObjectIndex (FTS5 unified search). 5 stories US-021..US-025 (`accepted`), новый план [observability-and-indexing-task-plan](roadmap/observability-and-indexing-task-plan.md): 5 секций (Metrics/Commits/Code-Refs/Repo-Index/DB-Object-Index), 8 задач OBI-001..OBI-040. Помечено опциональным — не блокирует Phase 1 paperclip-adoption. |

## 7. Соглашения об оформлении

- Проза — на русском; идентификаторы (поля, типы, status, имена сущностей и таблиц) — на английском.
- Заголовки секций — `## N. Title`; ссылки — markdown-relative из location документа.
- Каждый документ имеет frontmatter согласно [standards/frontmatter.md](standards/frontmatter.md).
- При `status: active` любая правка обязана сопровождаться записью в changelog таблицу + revision (после реализации COD-004) — см. [standards/revision-history.md](standards/revision-history.md).
