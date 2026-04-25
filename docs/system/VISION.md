---
type: vision
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
---

# COD-DOC — Vision

## 1. Почему

`~/Git/Restate` — живой проект, который поддерживался вручную через Obsidian + набор markdown-стандартов (`Docs/standards/task-plan.md`, `frontmatter.md`, `module-spec.md`) и скриптов (`tools/task-plan-audit.mjs`, `tools/generate-workspace-map.mjs`, `tools/lightrag`). Это работает, но требует:

- ручного соблюдения формата frontmatter и вычисления `tasks_done` / `tasks_total`;
- ручного линкования между документами и поиска стейл-ссылок при переименовании;
- ручных changelog-записей и синхронизации с Plane;
- запуска скриптов-валидаторов (`--strict`) перед каждым коммитом;
- непрерывного уплотнения «что где лежит» в голове автора.

Со временем дисциплина деградирует: метаданные устаревают, ссылки рвутся, задачи дрейфуют между `pending` и `in-progress`, user stories теряют связь с модулями. COD-DOC должен заменить ручную дисциплину хранимым состоянием и валидацией «по построению».

## 2. Что такое COD-DOC

**COD-DOC — это документационный движок с БД-бэкендом, Markdown-проекциями и MCP/CLI-интерфейсом для автоматизированной работы с проектной документацией.**

Ядро:

- Нормализованная БД (Documents, Tasks, Links, Revisions, Stories, Dependencies, Tags).
- Markdown — только проекция: генерируется из БД при `export`, парсится обратно при `import`.
- Единый язык запросов к состоянию проекта: через CLI, REST-API, MCP-тулы.
- Агент на LLM, который использует MCP для развития документации, создания задач, поддержания графа знаний.

## 3. Базовые обещания системы

| Обещание | Как поддерживается |
|----------|---------------------|
| Любая ссылка валидна либо известна как битая | автолинковка + периодическая верификация ([auto-linking](capabilities/auto-linking.md)) |
| Любое изменение обратимо и объяснимо | revision-история на каждой сущности ([revision-history](standards/revision-history.md)) |
| Формат задач нарушить нельзя | БД схема + валидация на уровне write-path ([task-plan](standards/task-plan.md)) |
| План согласован сам с собой | `tasks_done` / `tasks_total` — вычисляемые, не хранимые ([plan-management](capabilities/plan-management.md)) |
| Агент может получить «достаточный, но минимальный» контекст | concentrated-context запросы ([context-retrieval](capabilities/context-retrieval.md)) |
| User stories связаны с задачами и модулями | явные ребра в графе ([user-stories-graph](capabilities/user-stories-graph.md)) |

## 4. Целевой пользовательский опыт

### 4.1 Автор документации

```bash
cod-doc doc new --module M1-auth --type spec --title "Auth Module"
# создаёт запись Document, генерирует skeleton-markdown, регистрирует ссылки,
# прописывает owner/created/last_updated автоматически

cod-doc doc edit M1-auth --section "Data Model" --from-file /tmp/section.md
# diff применяется, revision пишется в историю, исходящие ссылки ре-резолвятся
```

### 4.2 Автор плана

```bash
cod-doc task new --plan M1-auth --title "Implement account deactivation" --type feature
# генерирует ID в правильном range (AUTH-025..), проставляет priority, valida­тит title

cod-doc task depend AUTH-025 --on AUTH-020 AUTH-021
# обновляет граф, пересчитывает критический путь

cod-doc plan next --count 5
# возвращает readyTasks без terminal-подтверждения; формат идентичен Restate
```

### 4.3 Агент (LLM через MCP)

```text
→ cod_doc.context.get(module="M1-auth", depth="L1")
← { master, spec, task_plan_progress, open_questions, related_stories }

→ cod_doc.task.update_status(id="AUTH-025", status="in-progress")
← ok, revision=r_abc123

→ cod_doc.doc.propose_edit(doc="M1-auth/overview.md", patch=...)
← pending_approval=r_def456
```

### 4.4 Мигратор с Restate

```bash
cod-doc import restate ~/Git/Restate \
  --docs Docs/obsidian/Modules \
  --plans "Docs/obsidian/Modules/*/*-task-plan.md" \
  --standards Docs/standards
# читает существующий markdown, заполняет БД, валидирует,
# сохраняет исходные файлы как «frozen projection» до первого export
```

## 5. Non-goals (что COD-DOC не делает)

- Не заменяет Plane/Jira — это локальный оркестратор, как и Restate-task-plan.
- Не берёт на себя runtime бизнес-логики пользовательского проекта — только его документацию и план.
- Не форсит конкретный LLM-провайдер — MCP-контракт стабильный, агент сменяемый.
- Не строит собственный граф-инжен — PostgreSQL + recursive CTE / pgrouting покрывают нужды (см. [ARCHITECTURE.md](ARCHITECTURE.md)).

## 6. Успех

COD-DOC считается состоявшимся, когда:

1. Полная документация Restate импортирована и ≥ 1 месяца живёт без регрессий качества.
2. `cod-doc audit` покрывает все правила, которые сейчас даёт `tools/task-plan-audit.mjs --strict` в Restate.
3. Любое структурное изменение (переименование документа, передвижка задачи между секциями) корректно обрабатывается через CLI/MCP без ручных правок markdown.
4. LLM-агент способен выполнить цикл «разобраться в модуле → создать план → довести 3 задачи до done» на новом чистом проекте за одну длинную сессию, опираясь только на COD-DOC-контракт.
