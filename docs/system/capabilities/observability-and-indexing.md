---
type: module-spec
scope: observability-and-indexing
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, agents]
related_code:
  - cod_doc/services/task_service.py
  - cod_doc/services/revision_service.py
  - cod_doc/services/link_service/
  - cod_doc/core/reindex.py
---

# Capability — Observability & Indexing

> **Назначение.** Метрики выполнения, тесная интеграция с git-историей,
> связывание исходного кода с задачами/документами, индексация файловой и
> объектной базы. Открывает «откуда что взялось и куда ведёт» как первоклассный
> вопрос проекта.

## 1. Зачем

Сейчас в cod-doc есть фрагменты:
- `revision_service` пишет историю мутаций — но без duration/cost.
- `task.completed_commit` хранит SHA, но нет обратного индекса коммит → задача.
- `affects_files` на задаче — список путей, но без validation/auto-derive.
- `core/reindex.py` индексирует только markdown в ChromaDB.
- `link_service` резолвит markdown-ссылки, но не понимает `[symbol_name](src/file.py)`.

Боль: «что за коммит изменил эту задачу», «какие файлы относятся к US-014»,
«где ещё упоминается этот класс» требуют ручного grep'а или git-blame.

## 2. Сущности

### 2.1 TaskMetric (US-021)

```python
@dataclass
class TaskMetric:
    task_id: str
    project_id: int
    duration_seconds: float       # completed_at - in_progress_started_at
    run_id: str | None            # из PCA-030 (когда будет)
    llm_calls: int
    llm_tokens_in: int
    llm_tokens_out: int
    cost_usd: float | None
    iterations: int
    failed_attempts: int          # сколько раз падал в FAILED перед DONE
    recorded_at: datetime
```

Aggregations: per-priority, per-type, per-section, per-week — для метрик-страницы.

### 2.2 CommitLink (US-022)

```python
@dataclass
class CommitLink:
    commit_sha: str               # full or 12-hex
    project_id: int
    task_ids: list[str]           # из commit message regex (PCA-XXX, COD-NNN)
    affected_paths: list[str]     # git diff --name-only
    author: str
    committed_at: datetime
    message_first_line: str
```

Парсер: при `task.complete(commit_sha=...)` или batch-импорт `git log` — извлекает
`PCA-NNN` / `COD-NNN` из commit message regex'ом, заполняет `affected_paths`.

### 2.3 CodeRef (US-023)

```python
@dataclass
class CodeRef:
    from_kind: str       # 'task' | 'document' | 'story'
    from_id: str
    to_path: str         # cod_doc/services/task_service.py
    to_anchor: str | None  # function/class name optionally (e.g. 'TaskService.create')
    line_start: int | None
    line_end: int | None
    discovered_at: datetime
```

Источники:
- `task.affects_files` (явный список на задаче)
- `parse_markdown` находит inline-refs `[`code-symbol`](src/path.py)` →
  link_service-ext (US-019 + US-023).

### 2.4 RepoIndex (US-024)

Файловый индекс репозитория (отдельно от ChromaDB markdown-индекса):
- `path` (relative)
- `language` (python|js|ts|md|...)
- `sha` (file hash)
- `symbols` (functions/classes/exports — top-level)
- `imports` (для python — `from X import Y`)
- `last_modified`

`.gitignore`-aware. Перестраивается на git-hooks (pre-commit) или manual
`cod-doc reindex --files`.

### 2.5 DBObjectIndex (US-025)

Внутренний search index для DB-content:
- doc bodies (sections)
- task description / acceptance / blocked_reason
- story narrative / acceptance criteria
- ADR context / decision / consequences (после ADR-001)
- revision diffs (compact)

Реализация — sqlite FTS5 виртуальная таблица или ChromaDB-embedding по коротким
chunks. Цель — `cod-doc search "phrase"` возвращает unified ranked результат
(docs + tasks + stories + ADR) с подсветкой scope.

## 3. Интеграция с существующими capability

| Capability | Дополнение |
|------------|------------|
| `decisions-and-questions` | TaskMetric обогащает «почему столько времени ушло» |
| `auto-linking` | CodeRef расширяет список парсимых форм |
| `audit-and-ci` | metrics dashboard как новая section |
| `web-frontend` | новые страницы /metrics, /index, /commits |

## 4. UI

- `/p/<slug>/metrics` — агрегаты: задач за неделю, p50/p95/p99 длительности,
  cost по статус-секциям, sparkline по неделям.
- `/p/<slug>/commits` — git-history с фильтрами по задаче/секции/автору.
- На странице задачи `/p/<slug>/tasks/<id>` — панели:
  - Recent commits affecting `affects_files`.
  - Code refs (file:lines с link to /repo/<path>).
- `/p/<slug>/search?q=...` — unified search через DBObjectIndex.

## 5. CLI

```
cod-doc metrics --since=7d --by=priority
cod-doc commits link --task PCA-001
cod-doc reindex --files
cod-doc reindex --db
cod-doc search "validation pattern"
```

## 6. Acceptance (capability-level)

- TaskMetric записывается на каждое `task.complete`; aggregation API в Web UI.
- CommitLink populated batch-import'ом + автоматически на `task.complete(commit_sha)`.
- CodeRef'ы создаются auto при parse markdown с code-link форм.
- RepoIndex покрывает 100% не-gitignored файлов; reindex runtime ≤ 5s на 1000 файлов.
- DBObjectIndex отвечает на `search` query за ≤ 200ms на typical-sized проекте.

## 7. Не в scope

- Live performance profiling (`py-spy` / flame graphs) — отдельная capability.
- Cross-project search (multi-tenant) — однопользовательская система.
- AST-based deep code analysis (вытаскивание call-graphs) — overkill для
  «где упоминается X»; достаточно symbol-name + path.

## 8. Roadmap

Реализация — план [observability-and-indexing-task-plan.md](../roadmap/observability-and-indexing-task-plan.md),
5 секций (A-E, по одной на сущность), 8 задач OBI-001..OBI-040. Всё помечено
**опциональным** — включается по запросу пользователя, не блокирует Phase 1
paperclip-adoption.
