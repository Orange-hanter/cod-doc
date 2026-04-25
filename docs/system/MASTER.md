---
type: documentation-master
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
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
│   └── revision-history.md
│
├── capabilities/                   ← целевые возможности, по одной на файл
│   ├── task-creation.md
│   ├── doc-evolution.md
│   ├── auto-linking.md
│   ├── context-retrieval.md
│   ├── plan-management.md
│   └── user-stories-graph.md
│
├── migration/
│   └── from-restate.md             ← как перевезти реальное состояние Restate
│
└── roadmap/
    └── cod-doc-task-plan.md        ← план внедрения (dogfood формата task-plan)
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
| roadmap/cod-doc-task-plan | draft | cod-doc core |

Пока пакет в статусе `draft` — изменения допустимы без revision-истории. После `active` любая правка обязана вести к revision-записи (см. [standards/revision-history.md](standards/revision-history.md)).

---

## 6. Changelog

| Дата | Событие |
|------|---------|
| 2026-04-19 | Начальная версия пакета; базовая структура, стандарты и capabilities. |
