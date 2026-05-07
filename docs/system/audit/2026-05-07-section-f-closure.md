---
type: audit-report
scope: paperclip-adoption / Section F (Tooling fixes)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-doc-consolidation-cycle-2.md
  - 2026-05-07-doc-consolidation-cycle-4.md
  - ../roadmap/paperclip-adoption-task-plan.md
---

# Section F — Closure Report (Tooling Fixes)

> **Назначение.** Зафиксировать закрытие 3 из 4 задач Section F плана
> `paperclip-adoption-task-plan` (PCA-901, PCA-902, PCA-903). PCA-911
> остаётся открытой как low-priority cleanup.

## 1. TL;DR

- **PCA-902** (`critical`) и **PCA-903** (`high`) закрыты совместным фиксом —
  единая семья правок в `task_service.create` и `_db.task_to_dict`.
- **PCA-901** (`high`) закрыт добавлением MCP-тулов `plan.create` и
  `plan.section_create` в `cod_doc/mcp/tools/plan_tools.py`.
- 5 новых тестов в `tests/services/test_task_create.py`; smoke-assertions
  на новые tool names в `tests/test_mcp.py`.
- Регрессия: services suite **407/407**, MCP smoke **2/2**, API web suite
  **251/251**.

## 2. Изменения

### 2.1 PCA-902 + PCA-903 — task_create persistence

**Файлы:**
- `cod_doc/services/task_service.py` — `create()` принимает `blocked_by:
  list[str] | None` и `story_id: str | None`. После insert'а task'а:
  - Для каждого `blocked_by` task_id строкой — lookup по `TaskModel.task_id`
    и insert `DependencyModel(from_task_id=new, to_task_id=blocker,
    kind='blocks')`. Unknown task_id → `ValueError`.
  - Для `story_id` — lookup по `(project_id, story_id)` и insert
    `StoryLinkModel(story_id=story.row_id, to_kind='task',
    to_ref=task_id, relation='implemented_by')`. Unknown story_id → `ValueError`.
- `cod_doc/mcp/tools/_db.py` — `task_to_dict(t, session=None)` опционально
  принимает session. При наличии session — три SELECT'а, заполняющие
  `blocked_by` (через dependency join), `affects_files` (через affected_file)
  и `story_id` (через story_link reverse-lookup).
- `cod_doc/mcp/tools/task_tools.py` — `task.create` пробрасывает
  `blocked_by` и `story_id` в сервис; `task_to_dict(t, session=session)`
  для `task.create / task.get / task.list`. Удалена «echo back»-заглушка.

**Тесты:**
- `tests/services/test_task_create.py`:
  - `test_create_with_blocked_by_persists_dependency_rows` — два блокера
    создают два корректных DependencyModel rows.
  - `test_create_with_unknown_blocked_by_raises` — ValueError + сохранён
    оригинальный `task_id` в сообщении.
  - `test_create_with_story_id_creates_story_link` — story_link с
    `relation='implemented_by'`.
  - `test_create_with_unknown_story_id_raises` — ValueError.
  - `test_plan_ready_excludes_tasks_with_unfinished_blockers` — поведение
    `plan_service.ready()` теперь корректно отражает blocked_by-edges.

### 2.2 PCA-901 — plan.create / plan.section_create MCP tools

**Файл:** `cod_doc/mcp/tools/plan_tools.py`.

```python
@mcp.tool(name="plan.create")
def plan_create(project, scope, principle="from-rfc", sections=None) -> dict:
    ...

@mcp.tool(name="plan.section_create")
def plan_section_create(project, plan_scope, letter, title, slug=None, position=None) -> dict:
    ...
```

`plan.create` опционально принимает inline-список `sections=[{letter,title,
slug,position}]` для bootstrap'а нового направления одним вызовом.
Идемпотентности нет — повторный create с тем же scope даёт `ValueError`
(как `existing.scope`-конфликт).

**Тесты:**
- `tests/test_mcp.py::test_mcp_lists_tools` дополнен smoke-assertion'ами на
  `plan.create` и `plan.section_create`.
- Логика напрямую покрывается существующими PlanRepository-тестами
  (репо-слой стабилен с COD-002).

## 3. Метрики

| Метрика | До | После | Δ |
|---------|-----|-------|---|
| `tests/services/` | 402 | 407 | +5 |
| `tests/test_mcp.py` smoke assertions | 18 | 20 | +2 |
| MCP plan-tools surface | 7 | 9 | +2 |
| Section F open tasks | 4 | 1 (PCA-911 only) | -3 |

## 4. Acceptance per task

- [x] **PCA-902 (critical)** — `task_create(blocked_by=['X'])` создаёт
      `dependency`-row; `task.get` показывает persisted blocked_by;
      `plan_ready` исключает блокированные tasks.
- [x] **PCA-903 (high)** — `task_create(story_id='US-X')` создаёт
      `story_link`; `task.get` показывает persisted story_id; affects_files
      теперь видны в response через session-aware task_to_dict.
- [x] **PCA-901 (high)** — `plan.create` и `plan.section_create` MCP-тулы
      зарегистрированы и работают; bootstrap нового плана возможен без
      обхода через PlanRepository.

## 5. Что осталось / not-in-scope

- **PCA-911 (low)** — уборка фикстуры `arch/arch/architecture.md` —
  отложено как low-priority (требует git rm + dedup doc-record).
- **Реальный resync DB body** для root `MASTER`/`docs/system/MASTER` —
  накопившийся drift после edit-in-place циклов 1-3 не исправляется
  Section F'ом (drift accepted as known state в Cycle 4).

## 6. Следующий шаг

Базовая ergonomics плана теперь корректна: `plan_ready` / `plan_audit` /
`critical_path_length` отражают реальную графовую структуру беклога.
Можно стартовать Phase 1 paperclip-плана (PCA-001 — Skills layer) либо
ADR-плана (ADR-001 — Migration). Оба — независимы от Section F и имеют
`pending` статусы.

Рекомендация: **PCA-001** как первая задача Phase 1, она small-scope
и валидирует, что новый dependency-flow реально ломает race-конкретные
сценарии (PCA-002 имеет blocked_by=PCA-001).
