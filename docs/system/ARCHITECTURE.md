---
type: architecture
scope: cod-doc-system
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_code:
  - cod_doc/core/
  - cod_doc/agent/
  - cod_doc/api/
  - cod_doc/mcp/
  - cod_doc/cli/
---

# COD-DOC — Architecture

Многослойная модульная архитектура. Ни один слой не ссылается на слой выше себя.

```text
┌─────────────────────────────────────────────────────────────┐
│  Presentation                                               │
│   CLI (click)   TUI (textual)   REST API (FastAPI)   MCP    │
└──────────────┬──────────────┬────────────┬────────────┬─────┘
               │              │            │            │
               ▼              ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────┐
│  Application / Services                                     │
│   DocService    TaskService   PlanService   GraphService    │
│   ContextService  RevisionService  LinkService  StoryService│
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Domain                                                     │
│   Document  Task  Link  Revision  UserStory  Dependency     │
│   Plan  Section  Tag  Project  Module                       │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Infrastructure                                             │
│   SQLite/Postgres  FS (markdown projections)   Git hooks    │
│   Embeddings store (sqlite-vss / pgvector)  LLM provider    │
└─────────────────────────────────────────────────────────────┘
```

## 1. Presentation layer

| Поверхность | Назначение | Ограничения |
|-------------|-----------|-------------|
| CLI `cod-doc` | Человек-оператор, скрипты, CI | Нет прямых SQL-запросов; только через сервисы |
| TUI `cod-doc wizard`/`dashboard` | Интерактивная работа, онбординг | Использует тот же CLI-слой через in-process вызовы |
| REST API (`cod_doc/api`) | Веб-клиенты, внешние интеграции | Стейтлесс, аутентификация через токен проекта |
| MCP (`cod_doc/mcp`) | LLM-агенты | Пара tools на каждое сервисное действие; без intermediate shell |

Все четыре поверхности — **равные**: если функциональность добавлена в сервис, она обязана появиться в CLI и MCP минимум через пол-часа, чтобы агент и человек имели тождественный интерфейс (правило, взятое из Restate: `task-plan-ecosystem.md §6.4` — «CLI остаётся канонической альтернативой MCP»).

## 2. Application layer (сервисы)

| Сервис | Ответственность |
|--------|----------------|
| `DocService` | CRUD документов, генерация skeleton, импорт/экспорт markdown |
| `TaskService` | CRUD задач, валидация формата (ID, type, title-verb-pattern) |
| `PlanService` | Пересчёт `tasks_done`/`tasks_total`, Progress Overview, Next Batch |
| `GraphService` | Зависимости, критический путь, циклы, reverse chain |
| `ContextService` | Сборка «концентрированного контекста» по запросу (L0/L1/L2) |
| `RevisionService` | Запись ревизий, diff-генерация, rollback |
| `LinkService` | Резолвинг внутренних ссылок, обнаружение битых, обновление при переименовании |
| `StoryService` | User stories, связывание историй с tasks и модулями |

Сервисы транзакционны: любая операция либо целиком коммитится, либо откатывается. Каждое write-действие сопровождается записью в `Revision`.

## 3. Domain layer

Чистые сущности — см. [DATA_MODEL.md](DATA_MODEL.md). Не имеют внешних зависимостей (никаких SQLAlchemy-моделей в домене; репозитории живут на уровне инфраструктуры и возвращают dataclass-сущности).

## 4. Infrastructure layer

### 4.1 Хранилище

Два профиля:

| Профиль | СУБД | Назначение |
|---------|------|-----------|
| `embedded` | SQLite в `.cod-doc/state.db` | Один локальный проект, без сервера |
| `server` | PostgreSQL | Командная работа, CI, несколько клиентов на один проект |

Схема БД — общая; различаются диалекты (`JSON` vs `JSONB`, `TEXT` vs `VARCHAR`, `INTEGER` vs `BIGINT`). Миграции — Alembic.

### 4.2 Markdown projection

`.cod-doc/mirror/` — дерево markdown-файлов, зеркалирующее БД. Не исходники, а **артефакт**. Правила:

- `export` регенерирует все файлы детерминированно.
- `import` парсит файлы и пытается применить изменения через сервисы (не через прямую запись в БД).
- Hash каждого файла хранится в `Document.projection_hash`. Если на диске hash не совпадает с последним exported-hash — файл считается edited-in-place, запускается reconciliation.

### 4.3 Embeddings

Для concentrated-context retrieval (`ContextService`) хранится индекс эмбеддингов по документам и секциям. Реализации:

- SQLite profile: `sqlite-vss` или faiss-файл рядом с БД.
- Postgres profile: `pgvector`.

Индексация — по событию `RevisionCommitted`, асинхронно, с fallback на полнотекстовый поиск.

### 4.4 LLM provider

Выбор провайдера — конфиг (OpenRouter, Anthropic API, local Ollama). Домен и сервисы не знают о конкретном провайдере; агент/оркестратор — знает. Это наследуется из текущего cod-doc (см. `cod_doc/agent/orchestrator.py`, `cod_doc/config.py`).

## 5. Потоки данных

### 5.1 Создание задачи

```text
human|agent
   │  task create ...
   ▼
CLI/MCP ──► TaskService.create()
               │
               ├─► валидирует формат (task-plan.md §5)
               ├─► вычисляет ID в пределах section range
               ├─► пишет Task в БД
               ├─► пишет Revision
               ├─► триггерит PlanService.recalc(plan_id)
               └─► триггерит LinkService.reindex(doc=section_file)
```

### 5.2 Изменение документа

```text
DocService.apply_patch(doc_id, patch)
   │
   ├─► применяет diff к canonical body (в БД)
   ├─► перерезолвит outgoing links (LinkService)
   ├─► пишет Revision(diff, author, reason)
   ├─► ставит задачу в очередь на re-embedding
   └─► при export — обновляет markdown projection
```

### 5.3 Запрос контекста агентом

```text
MCP: context.get(module="M1-auth", depth="L1")
   │
   ▼
ContextService.build(target, depth)
   │
   ├─► L0: только MASTER + explicit target
   ├─► L1: + прямые связи (module-spec, открытый task-plan, последние 3 открытых stories)
   ├─► L2: + depends_on-цепочки, ближайшие open questions, cross-module dependencies
   │
   └─► возвращает JSON + markdown-excerpts под token budget
```

## 6. Контракты между слоями

- Presentation → Application: typed DTO (pydantic).
- Application → Domain: dataclass-сущности.
- Domain → Infrastructure: абстрактные репозитории (`Protocol`), реализации в инфре.

Нельзя:

- В MCP-сервере писать SQL напрямую.
- В домене зависеть от `sqlite3`/`psycopg`.
- В CLI дублировать бизнес-логику, которой нет в сервисе (если понадобилось — сначала сервис).

## 7. Inversion of dependencies

`cod_doc/core/project.py` уже реализует часть домена (Task, TaskStatus). Миграция к целевой архитектуре:

1. Выделить `cod_doc/domain/` с чистыми сущностями.
2. Оставить `cod_doc/core/` как backward-compat shim, пока не переехали все потребители.
3. Ввести `cod_doc/services/` (Doc, Task, Plan, …).
4. Ввести `cod_doc/infra/repositories/` с адаптерами под SQLite/Postgres.
5. Переписать `cod_doc/cli/`, `cod_doc/mcp/`, `cod_doc/api/` на сервисы.
6. Удалить shim.

Порядок — итеративный, см. [roadmap/cod-doc-task-plan.md](roadmap/cod-doc-task-plan.md).

## 8. Деплой и среды

- **Local embedded** (одиночный разработчик): `.cod-doc/state.db`, нет REST, только CLI + MCP.
- **Shared Postgres** (команда / Restate-scale): docker-compose стек (`docker-compose.yml` уже есть), REST API активен, MCP запускается на машине пользователя и ходит в общий Postgres.
- **CI**: headless режим; используется только CLI (`cod-doc audit`, `cod-doc plan next`, `cod-doc link verify`).

Переключение — через `COD_DOC_DB_URL`.

## 9. Безопасность

- Данные проекта не покидают БД без явного export.
- Встроенный LLM-клиент не видит содержимого документов сверх того, что ContextService положил в сессию.
- Audit-лог всех write-операций через MCP/REST — в таблице `Revision` + отдельном `AuditLog` (см. [DATA_MODEL.md §3.13](DATA_MODEL.md)).

## 10. Error Model

Единый набор исключений уровня домена/сервисов (`cod_doc.errors`). Все surface'ы (CLI, MCP, REST, TUI) маппят их в свой формат.

### 11.1 Иерархия

```
CodDocError
├── ValidationError       — нарушение формата (frontmatter, task verb-pattern, enum)
├── NotFoundError         — целевая сущность не существует
├── ConflictError         — состояние не позволяет операцию
│   ├── DependencyError   — неудовлетворённые depends_on
│   ├── CycleError        — попытка создать цикл в графе
│   └── OptimisticLockError — parent_revision_id устарел (см. §11)
├── AuthDeniedError       — actor не имеет права на инструмент / sensitivity
├── IntegrityError        — нарушение схемы / FK / уникальности
└── ExternalError         — провал внешнего сервиса (LLM, embeddings, git)
```

Каждое исключение несёт:

```python
class CodDocError(Exception):
    code: str         # 'TP-004', 'FM-001', 'AUTHZ-001' — стабильный для интеграций
    message: str      # человекочитаемое
    details: dict     # структурированные поля (entity_id, suggestion, ...)
```

### 11.2 Маппинг по поверхностям

| Error → | CLI exit | MCP isError + payload | REST status | TUI |
|---------|---------:|------------------------|-------------|-----|
| ValidationError    | 2 | `{"code":"FM-001",...}` | 400 | inline form error |
| NotFoundError      | 3 | ↑ | 404 | toast |
| ConflictError      | 4 | ↑ | 409 | modal |
| AuthDeniedError    | 5 | ↑ | 403 | modal |
| IntegrityError     | 6 | ↑ | 500 | crash screen |
| ExternalError      | 7 | ↑ | 502 | retry-toast |
| Unknown / panic    | 1 | ↑ | 500 | crash screen |

### 11.3 Правило write-path

Любая ошибка в транзакции = полный rollback.
Никаких partial updates: либо весь набор изменений (task + dependency + revision + section_totals refresh) применился, либо ни одно.
`audit_log` пишется **до** commit'а с предварительным `result='pending'` и обновляется на `'ok'` / `'error:<code>'` после.

### 11.4 Идемпотентность

- `task.create` принимает `idempotency_key` (опционально); повторный вызов с тем же ключом возвращает оригинальный результат.
- `doc.patch_section` идемпотентен по `parent_revision_id` — повторный apply того же патча с тем же parent даёт тот же revision_id (детерминированный ULID при флаге `--deterministic`).

## 11. Concurrency & Identity

### 12.1 Optimistic locking

Запись revision требует `parent_revision_id` — последнюю известную revision сущности. Если за это время появилась новая — `OptimisticLockError`. Клиент перечитывает state и повторяет.

### 12.2 Identity

Каждый actor имеет запись в таблице `actor` (отдельно от `agent_definition`):

```sql
CREATE TABLE actor (
  row_id     INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(row_id),
  kind       TEXT    NOT NULL,    -- 'human'|'agent'|'mcp'|'system'
  handle     TEXT    NOT NULL,    -- 'dakh','task-steward','claude-code'
  token_hash TEXT,                -- SHA256 для server-profile; NULL для embedded
  created    TEXT    NOT NULL,
  UNIQUE(project_id, kind, handle)
);
```

Embedded-профиль: единственный неявный actor `human:<os-user>` без токена.
Server-профиль: REST/MCP требуют `Authorization: Bearer <token>`; токен резолвится в `actor.handle`. CLI-локально на сервере — через keyring.

### 12.3 Authz

Перед каждым tool-call:

1. Резолв actor.
2. Проверка allowed_tools/denied_tools (см. [capabilities/agents-and-skills.md §3](capabilities/agents-and-skills.md)).
3. Проверка sensitivity_clearance vs target document (см. [standards/sensitive-data.md §3](standards/sensitive-data.md)).
4. При deny — `AuthDeniedError(code='AUTHZ-001'|'AUTHZ-002')`, audit_log пишется обязательно.

## 12. Ссылки

- [VISION.md](VISION.md)
- [DATA_MODEL.md](DATA_MODEL.md)
- [capabilities/context-retrieval.md](capabilities/context-retrieval.md)
- [roadmap/cod-doc-task-plan.md](roadmap/cod-doc-task-plan.md)
