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

> **Purpose.** Execution metrics, tight integration with git history,
> linking source code to tasks/documents, indexing of the file and
> object base. Surfaces "where things came from and where they lead" as a
> first-class question of the project.

## 1. Why

Today cod-doc has fragments:
- `revision_service` writes mutation history — but without duration/cost.
- `task.completed_commit` stores the SHA, but there is no reverse index commit → task.
- `affects_files` on the task — a list of paths, but without validation/auto-derive.
- `core/reindex.py` indexes only markdown in ChromaDB.
- `link_service` resolves markdown links, but does not understand `[symbol_name](src/file.py)`.

Pain: "which commit changed this task", "which files belong to US-014",
"where else is this class mentioned" require manual grep or git-blame.

## 2. Entities

### 2.1 TaskMetric (US-021)

```python
@dataclass
class TaskMetric:
    task_id: str
    project_id: int
    duration_seconds: float       # completed_at - in_progress_started_at
    run_id: str | None            # from PCA-030 (when it arrives)
    llm_calls: int
    llm_tokens_in: int
    llm_tokens_out: int
    cost_usd: float | None
    iterations: int
    failed_attempts: int          # how many times it fell into FAILED before DONE
    recorded_at: datetime
```

Aggregations: per-priority, per-type, per-section, per-week — for the metrics page.

### 2.2 CommitLink (US-022)

```python
@dataclass
class CommitLink:
    commit_sha: str               # full or 12-hex
    project_id: int
    task_ids: list[str]           # from commit message regex (PCA-XXX, COD-NNN)
    affected_paths: list[str]     # git diff --name-only
    author: str
    committed_at: datetime
    message_first_line: str
```

Parser: on `task.complete(commit_sha=...)` or batch-import of `git log` — extracts
`PCA-NNN` / `COD-NNN` from the commit message via regex, fills `affected_paths`.

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

Sources:
- `task.affects_files` (explicit list on the task)
- `parse_markdown` finds inline-refs `[`code-symbol`](src/path.py)` →
  link_service-ext (US-019 + US-023).

### 2.4 RepoIndex (US-024)

File index of the repository (separate from the ChromaDB markdown index):
- `path` (relative)
- `language` (python|js|ts|md|...)
- `sha` (file hash)
- `symbols` (functions/classes/exports — top-level)
- `imports` (for python — `from X import Y`)
- `last_modified`

`.gitignore`-aware. Rebuilt on git-hooks (pre-commit) or manual
`cod-doc reindex --files`.

### 2.5 DBObjectIndex (US-025)

Internal search index for DB content:
- doc bodies (sections)
- task description / acceptance / blocked_reason
- story narrative / acceptance criteria
- ADR context / decision / consequences (after ADR-001)
- revision diffs (compact)

Implementation — sqlite FTS5 virtual table or ChromaDB-embedding over short
chunks. Goal — `cod-doc search "phrase"` returns a unified ranked result
(docs + tasks + stories + ADR) with scope highlighting.

## 3. Integration with existing capabilities

| Capability | Addition |
|------------|------------|
| `decisions-and-questions` | TaskMetric enriches "why so much time was spent" |
| `auto-linking` | CodeRef expands the list of parseable forms |
| `audit-and-ci` | metrics dashboard as a new section |
| `web-frontend` | new pages /metrics, /index, /commits |

## 4. UI

- `/p/<slug>/metrics` — aggregates: tasks per week, p50/p95/p99 duration,
  cost by status sections, sparkline by weeks.
- `/p/<slug>/commits` — git-history with filters by task/section/author.
- On the task page `/p/<slug>/tasks/<id>` — panels:
  - Recent commits affecting `affects_files`.
  - Code refs (file:lines with link to /repo/<path>).
- `/p/<slug>/search?q=...` — unified search via DBObjectIndex.

## 5. CLI

```
cod-doc metrics --since=7d --by=priority
cod-doc commits link --task PCA-001
cod-doc reindex --files
cod-doc reindex --db
cod-doc search "validation pattern"
```

## 6. Acceptance (capability-level)

- TaskMetric is recorded on every `task.complete`; aggregation API in Web UI.
- CommitLink is populated by batch-import + automatically on `task.complete(commit_sha)`.
- CodeRefs are created automatically when parsing markdown with code-link forms.
- RepoIndex covers 100% of non-gitignored files; reindex runtime ≤ 5s per 1000 files.
- DBObjectIndex answers a `search` query in ≤ 200ms on a typical-sized project.

## 7. Out of scope

- Live performance profiling (`py-spy` / flame graphs) — a separate capability.
- Cross-project search (multi-tenant) — single-user system.
- AST-based deep code analysis (extracting call-graphs) — overkill for
  "where is X mentioned"; symbol-name + path is enough.

## 8. Roadmap

Implementation — plan [observability-and-indexing-task-plan.md](../roadmap/observability-and-indexing-task-plan.md),
5 sections (A-E, one per entity), 8 tasks OBI-001..OBI-040. Everything is marked
**optional** — enabled on user request, does not block Phase 1
paperclip-adoption.
