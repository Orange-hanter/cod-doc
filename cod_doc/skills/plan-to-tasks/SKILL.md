---
name: plan-to-tasks
description: |
  Как разбивать execution-plan на task-узлы: structured fields blocked_by,
  story_id, affects_files, acceptance. Триггеры: plan, decompose, split,
  task_create, blocked_by, story_id, acceptance, section, breakdown.
---

# Skill — Plan → Tasks decomposition

## Когда подгружается

Задачи, где LLM / пользователь **разбивает план** или RFC на конкретные
выполнимые задачи. Триггер-keywords: `plan`, `decompose`, `split`,
`breakdown`, `task_create`, `blocked_by`, `story_id`, `acceptance`,
`section`, `phase`, `roadmap`.

## Канонический task-узел

Каждая задача в БД — узел графа:

```yaml
task_id: PCA-NNN          # PREFIX-NNN, prefix 2-5 заглавных
title: "Implement: <короткое описание>"
type: feature | test | bug | refactor | migration | docs | chore
priority: critical | high | medium | low
story_id: US-NNN          # link to user story (мотивация)
blocked_by: [PCA-MMM]     # IDs prereq-задач — превращаются в dependency-rows
affects_files: [path1, path2]  # код / документы, которые задача трогает
acceptance: "<один абзац>"     # condition of done
description: "<контекст и hint'ы>"
```

## Правила

1. **Заголовок** — императив с двоеточием: `Implement: …`, `Refactor: …`,
   `Test: …`, `Migration: …`, `Bug: …`, `Docs: …`. Никаких
   "make a thing" / "fix it".
2. **type** — кодифицирует характер работы:
   - `feature` — новый функционал.
   - `test` — добавить тест-покрытие.
   - `bug` — исправление дефекта.
   - `refactor` — улучшение без изменения поведения.
   - `migration` — миграция БД / схемы / данных.
   - `docs` — документ или гайд.
   - `chore` — хозяйственная задача (cleanup, deps).
3. **priority** — `critical` для блокеров секции, `high` для основной
   работы, `medium` для нужного-но-не-блокирующего, `low` для chore.
4. **acceptance** — конкретный, проверяемый. «N+ tests pass», «column
   added», «endpoint returns shape X». Не пиши «работает корректно».
5. **blocked_by** — только реальные prereq-зависимости. После закрытия
   PCA-902 (cycle-2 G2) дуги попадают в `dependency`-таблицу и видны в
   `plan.ready` / `plan.audit` / `critical_path`.
6. **affects_files** — путь от корня репо. Помогает агенту находить
   контекст задачи без grep'а (см. capability `observability-and-indexing`
   US-023).

## Алгоритм для большого плана

1. Назвать секцию — буква (A, B, C, ...) + название.
2. Назначить ID-prefix (3 заглавных буквы) — `PCA`, `OBI`, `WEB`, `COD`.
3. Декомпозировать на задачи 1-3 ч каждая. Если задача "1+ день" —
   разбивай дальше.
4. Для каждой задачи:
   - Выбрать `type` и `priority`.
   - Привязать к `story_id` (мотивация — что закрываем).
   - Заполнить `blocked_by` от существующих prereq-задач.
   - Перечислить `affects_files`.
   - Сформулировать `acceptance` в одном абзаце.
5. Записать в БД через `task.create` (MCP) **и** в markdown
   execution-plan (mirror).

## Что НЕ делать

- Не создавай задачи без `acceptance`.
- Не указывай `affects_files=[]` — лучше укажи самые вероятные
  кандидаты, чем оставить пустым.
- Не перегружай `description` — для большого контекста использовать
  attached `task_doc` (proposal 05 → US-009; ждёт реализации).
- Не пиши `blocked_by` с задачами из других проектов / планов.

## Связанное

- [standards/task-plan.md](../../../docs/system/standards/task-plan.md)
- [capabilities/plan-management.md](../../../docs/system/capabilities/plan-management.md)
- [capabilities/user-stories-graph.md](../../../docs/system/capabilities/user-stories-graph.md)
