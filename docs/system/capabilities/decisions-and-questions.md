---
type: capability
scope: decisions-and-questions
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../audit/2026-04-19-initial-audit.md
  - ../standards/frontmatter.md
---

# Capability — Decisions & Open Questions

> First-class реестр архитектурных решений (ADR-style) и нерешённых вопросов.
> Заменяет ручной `Open Questions.md` Restate-стиля.

## 1. Сущности

### 1.1 `Decision` (ADR)

```yaml
type: decision
decision_id: DEC-014
status: proposed | accepted | superseded | rejected
title: "Use ULID for revision_id"
context: "..."
decision: "..."
consequences: "..."
supersedes: DEC-007         # опционально
```

В БД — `decision` row + frontmatter в Document с `type=decision`.

### 1.2 `OpenQuestion`

```yaml
type: open-question
question_id: Q-021
status: open | resolved | dropped
owner: <responsible>
created: YYYY-MM-DD
related: [modules/M1-auth, DEC-014]
```

Резолюция → ссылка на `decision_id`, новая `revision` с `reason: "resolved by DEC-014"`.

## 2. Операции

| Операция | CLI | MCP |
|----------|-----|-----|
| Создать ADR | `cod-doc decision new` | `decision.create` |
| Принять | `cod-doc decision accept DEC-014 --reason "..."` | `decision.accept` |
| Заменить | `cod-doc decision supersede DEC-014 --by DEC-021` | `decision.supersede` |
| Открытый вопрос | `cod-doc question new` | `question.create` |
| Закрыть вопрос | `cod-doc question resolve Q-021 --by DEC-014` | `question.resolve` |
| Список открытых | `cod-doc question list --status open` | `question.list` |

## 3. Связи

- `decision_link(from_decision, to_kind, to_ref)` — связи на task/document/module.
- `question_link` аналогично.
- При `Open Questions` секция модульной спеки — её содержимое генерируется из `question.list(filter=module:M1-auth)`.

## 4. Поверхность для агентов

- `context.get(target=module:..., depth=L1)` включает ≤ 3 открытых question, как описано в [context-retrieval](context-retrieval.md).
- При создании задачи можно указать `--addresses Q-021` — связь сохранится.

## 5. Что не делаем

- Не превращаем каждый комментарий в ADR — порог: «решение влияет на ≥ 2 модуля или меняет схему БД».
- Не автоматизируем формулировку — только хранение и связи.
