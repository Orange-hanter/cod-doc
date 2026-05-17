---
type: capability
scope: decisions-and-questions
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-05-17
related_docs:
  - adr-system.md
  - ../audit/2026-04-19-initial-audit.md
  - ../standards/frontmatter.md
---

# Capability — Decisions & Open Questions

> Реестр архитектурных решений и нерешённых вопросов. Часть «Decisions»
> теперь реализуется как [ADR System](adr-system.md); этот документ
> сохраняется для «Open Questions» и истории.

## 1. Decisions = ADR

Решение, принятое в этом видении (см. [adr-vision.html](../adr-vision.html) §9):
**Decision-сущность реализована как ADR**. Один префикс `ADR-NNN`, одна
таблица, одно API. См. [adr-system.md](adr-system.md) для:

- доменной модели (status, supersedes, decided_by, decided_at, …);
- MCP-тулов (`adr_create`, `adr_get`, `adr_list`, `adr_update`,
  `adr_supersede`, `adr_link_task`, `adr_add_diagram`, `adr_graph`);
- CLI (`cod-doc adr new/list/show/supersede/graph`);
- Web UI (`/p/<slug>/adr` — список, форма, detail, supersede-граф).

«Decision» как отдельная сущность с префиксом `DEC-NNN` **не реализуется**.
Если в проектных файлах остался `DEC-NNN`-формат — это исторический
артефакт, его следует мигрировать в ADR через `adr_create` с
`adr_id="ADR-NNN"` (см. [adr_migrator.py](../../../cod_doc/services/adr_migrator.py)).

## 2. Open Questions (отдельная сущность)

`OpenQuestion` остаётся параллельной мелкой сущностью: «формулировка
вопроса без решения». Когда вопрос закрывается — он ссылается на ADR-id.

```yaml
type: open-question
question_id: Q-021
status: open | resolved | dropped
owner: <responsible>
created: YYYY-MM-DD
related: [modules/M1-auth, ADR-014]
resolved_by: ADR-014   # появляется при status=resolved
```

### 2.1 Операции (планируются)

| Операция | CLI | MCP |
|----------|-----|-----|
| Открытый вопрос | `cod-doc question new` | `question_create` |
| Закрыть вопрос | `cod-doc question resolve Q-021 --by ADR-014` | `question_resolve` |
| Список открытых | `cod-doc question list --status open` | `question_list` |

> **Статус реализации.** OpenQuestion-сущность ещё не выкачена; этот
> раздел — спецификация. Приоритет — после стабилизации ADR System.

## 3. Связи

- ADR ↔ task / document / module — через [adr-system](adr-system.md)
  (таблицы `adr_task`, `adr_supersedes`; будущая `adr_link` для doc/module).
- Auto-link `[ADR-NNN]` в любом markdown — через [auto-linking](auto-linking.md)
  (`LinkKind.ADR`).
- `OpenQuestion` будет иметь свой `question_link` по образцу `story_link`.

## 4. Поверхность для агентов

- `context.get(target=module:..., depth=L1)` включает ≤ 3 открытых
  question + список ACCEPTED-ADR проекта (см. [context-retrieval](context-retrieval.md)).
- При создании задачи можно указать `--addresses Q-021` или
  `--implements ADR-014` — связи сохраняются.

## 5. Когда писать ADR vs Open Question

| Ситуация | Что создавать |
|----------|---------------|
| Решено: «использовать X вместо Y», обоснование известно | **ADR** (статус `accepted`) |
| Обсуждается: «X или Y, склоняемся к X но не уверены» | **ADR** (статус `proposed`) |
| Вопрос без вариантов решения: «как мы будем масштабировать?» | **Open Question** |
| Запрос на эксперимент: «попробовать ChromaDB vs Qdrant» | **Open Question** + ADR в финале |

Подробнее про когда писать ADR — см. skill [adr-author](../../../cod_doc/skills/adr-author/SKILL.md).

## 6. Что не делаем

- Не превращаем каждый комментарий в ADR — порог: «решение влияет на
  ≥ 2 модуля или меняет схему БД».
- Не автоматизируем формулировку — только хранение и связи.
- Не делаем voting / quorum — ADR принимает один человек или агент;
  это не RFC-процесс.
