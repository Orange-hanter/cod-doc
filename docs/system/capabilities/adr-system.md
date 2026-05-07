---
type: module-spec
scope: adr-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, agents]
related_code:
  - cod_doc/services/adr_service.py
  - cod_doc/mcp/tools/adr_tools.py
  - cod_doc/api/web/pages/adr.py
  - cod_doc/templates/web/project/adr_*.html
---

# Capability — ADR System (Architecture Decision Records)

> **Назначение.** First-class система ADR-документов: автонумерация,
> статусы, supersede-цепочки, визуальный редактор в Web UI и Mermaid-граф
> зависимостей решений. ADR — отдельная сущность, не свободный markdown.

## 1. Зачем

Сейчас архитектурные решения в cod-doc живут разбросанно:
- В `arch/architecture.md §5 ADR` — пять ADR в одном файле, без статуса/supersede.
- В `proposals/*.md` — RFC, формально не ADR, но решения по ним принимаются ad-hoc.
- В commit-сообщениях — context теряется через 6 месяцев.
- В audit-отчётах — обоснование разовых решений (например, validation-pattern).

**Боль:**
- Нет единого реестра «какое решение принято когда и кем».
- Невозможно отследить supersede-цепочку («ADR-005 заменён ADR-012, который частично откатывает ADR-003»).
- Нет визуального инструмента для авторов — markdown-таблицы и встройка
  Mermaid-диаграмм требуют ручной разметки.

## 2. Модель

### 2.1 Сущность `Adr`

```python
@dataclass
class Adr:
    id: str                           # ADR-NNN, auto-numbered per project
    project_id: int
    title: str
    status: AdrStatus                 # PROPOSED | ACCEPTED | DEPRECATED | SUPERSEDED
    context: str                      # markdown — что решаем
    decision: str                     # markdown — что выбрали
    consequences: str                 # markdown — позитивные/негативные следствия
    supersedes: list[str] = []        # IDs предыдущих ADR, заменяемых этим
    superseded_by: str | None = None  # ID ADR, который заменил этот
    diagrams: list[Diagram] = []      # 0+ Mermaid-блоков с подписью
    decided_by: str                   # human:<user> | run-id агента
    decided_at: datetime
    related_tasks: list[str] = []     # task_id'ы, чьё выполнение предписано ADR
```

### 2.2 Статусы и переходы

```
PROPOSED  ──accept──▶  ACCEPTED  ──supersede──▶  SUPERSEDED
   │                       │
   └─reject──▶ DEPRECATED   └─deprecate──▶ DEPRECATED
```

- `PROPOSED` — заявка, открыта для дискуссии.
- `ACCEPTED` — действующее решение, обязательное.
- `DEPRECATED` — отменено, но не заменено новым (просто «больше не делаем»).
- `SUPERSEDED` — заменено конкретным ADR (поле `superseded_by`).

### 2.3 Связи

- ADR ⇔ Document — ADR может ссылаться на доки (как rationale).
- ADR ⇔ Task — ADR может предписывать задачи (`related_tasks`).
- ADR ⇔ ADR — supersede-граф (DAG, не цикл).

## 3. API

### 3.1 MCP-тулы

```
adr_create(project, title, context, decision, consequences, supersedes?=[], decided_by) -> Adr
adr_get(project, adr_id) -> Adr
adr_list(project, status?, limit?) -> [Adr]
adr_update(project, adr_id, *fields) -> Adr     # mutates ACCEPTED only с обоснованием
adr_supersede(project, old_adr_id, new_adr_id, reason) -> (old, new)
adr_deprecate(project, adr_id, reason) -> Adr
adr_add_diagram(project, adr_id, mermaid_source, caption) -> Adr
adr_link_task(project, adr_id, task_id) -> None
adr_graph(project, format='mermaid'|'json') -> str | dict
```

### 3.2 CLI

```
cod-doc adr new --title "Use SQLite by default" --context-file ./ctx.md
cod-doc adr list --status accepted
cod-doc adr show ADR-007
cod-doc adr supersede ADR-003 ADR-012 --reason "performance regression"
cod-doc adr graph --format mermaid > docs/adr-graph.mmd
```

### 3.3 Хранение

- Таблица `adr` (id auto-numbered ADR-NNN, project_id FK, fields).
- Таблица `adr_diagram` (adr_id FK, position, mermaid_source, caption).
- Таблица `adr_supersedes` (from_adr_id, to_adr_id, kind='supersedes').
- Таблица `adr_task` (adr_id, task_id, kind='predicates').

Каждое мутирующее действие пишет revision (entity_kind='ADR') как остальные
сервисы — единый паттерн append-only history.

## 4. Web UI (визуальная часть)

### 4.1 Страницы

- `/p/<slug>/adr` — список ADR с фильтрами (status, supersedes-chain, год).
  Карточки: ID, Title, Status badge (цвет по статусу), Decided-at, supersedes/superseded-by.
- `/p/<slug>/adr/new` — визуальный редактор.
  Форма с разделами: Title, Context (textarea + markdown preview), Decision,
  Consequences, Diagrams (multi-Mermaid с превью), Supersedes (multi-select из
  существующих ACCEPTED-ADR).
- `/p/<slug>/adr/<id>` — детальная страница с rendered markdown, Mermaid-блоками,
  supersede-цепочкой (визуально), связанными задачами.
- `/p/<slug>/adr/graph` — полный supersede-граф проекта (Mermaid `graph TD`),
  кликабельные узлы → /adr/<id>.

### 4.2 Status badges

| Status | Color | Icon |
|--------|-------|------|
| PROPOSED | `#facc15` (amber) | 🟡 |
| ACCEPTED | `#22c55e` (green) | 🟢 |
| DEPRECATED | `#737373` (grey) | ⚪ |
| SUPERSEDED | `#94a3b8` (slate) | 🔁 |

### 4.3 Mermaid в редакторе

Live-preview через клиентский Mermaid.js (тот же, что уже использует cod-doc
для plan-graphs). При сохранении — sanitize ввода, штамп `mermaid-version`
для воспроизводимости рендера.

### 4.4 Supersede-flow (визуально)

При создании нового ADR с заполненным `supersedes` — на странице старого ADR
появляется баннер «Заменён: ADR-NNN — <title> (decided <date>)»; statuses
обновляются транзакционно.

## 5. Acceptance (capability-level)

- ADR-NNN автонумеруются per-project; нет коллизий при concurrent create.
- Supersede-граф — DAG (cycle-detection в `adr_supersede`).
- Web UI показывает рендеренный markdown (не source).
- `adr_graph(format='mermaid')` возвращает валидный `graph TD` с кликабельными узлами.
- Каждое изменение ADR пишет revision (восстановимо через `revision_revert`).
- Удаление ADR запрещено; только deprecate/supersede.

## 6. Не входит в scope

- Voting/quorum — ADR в cod-doc принимает один человек или агент; это не RFC-process.
- Comment-threads на ADR — обсуждение идёт в issue-tracker внешнего инструмента.
- Auto-suggest «нужен ADR здесь» — не делаем; ADR создаётся явно.

## 7. Связь с другими capabilities

- [Doc evolution](doc-evolution.md) — общий revision-механизм.
- [User stories graph](user-stories-graph.md) — параллельная сущность для
  product-уровня (ADR — для tech-уровня).
- [Decisions and questions](decisions-and-questions.md) — open questions,
  которые могут перерасти в ADR.

## 8. Roadmap

Реализация — план [adr-system-task-plan.md](../roadmap/adr-system-task-plan.md),
3 секции (Domain & MCP, Web UI, Templates & Migration), 8 задач
ADR-001..ADR-008.
