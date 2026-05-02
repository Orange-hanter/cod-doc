---
type: documentation-master
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-05-02
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
│   ├── decisions-and-questions.md  ← ADR + Open Questions
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
│   └── 2026-05-02-section-web-frontend.md  ← аудит web-секции (после WEB-001..011)
│
├── migration/
│   └── from-restate.md             ← как перевезти реальное состояние Restate
│
└── roadmap/
    ├── cod-doc-task-plan.md             ← план внедрения (dogfood формата task-plan)
    ├── web-frontend-task-plan.md        ← план Web UI поверх FastAPI
    ├── web-frontend-kickoff-2026-05-02.md ← brief на старт Section F (после аудита)
    └── audit-followups-task-plan.md     ← фиксы пакета по аудиту
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
| audit/2026-04-28-section-c-capabilities | active | cod-doc core |
| audit/2026-05-01-section-g-hardening | resolved | cod-doc core |
| audit/2026-05-02-section-web-frontend | active | cod-doc core |
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

---

## 7. Соглашения об оформлении

- Проза — на русском; идентификаторы (поля, типы, status, имена сущностей и таблиц) — на английском.
- Заголовки секций — `## N. Title`; ссылки — markdown-relative из location документа.
- Каждый документ имеет frontmatter согласно [standards/frontmatter.md](standards/frontmatter.md).
- При `status: active` любая правка обязана сопровождаться записью в changelog таблицу + revision (после реализации COD-004) — см. [standards/revision-history.md](standards/revision-history.md).
