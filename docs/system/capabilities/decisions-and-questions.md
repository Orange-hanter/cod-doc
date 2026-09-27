---
type: capability
scope: decisions-and-questions
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-09-27
related_docs:
  - adr-system.md
  - ../audit/2026-04-19-initial-audit.md
  - ../standards/frontmatter.md
---

# Capability — Decisions & Open Questions

> Реестр архитектурных решений и нерешённых вопросов. Часть «Decisions»
> теперь реализуется как [ADR System](adr-system.md); этот документ
> сохраняется для «Open Questions» и истории.

## 0. As implemented (2026-09-27)

Decisions = `adr_*` / `cod-doc adr` / таблица ADR (`ADR-NNN`).

Open Questions = сущность БД `open_question` (`Q-NNN`, миграция 0042) с
вариантами ответа (`open_question_option`) и ссылками
(`open_question_link`). **В markdown не проецируется**: у вопроса нет
документа, файла и секции — смотреть и править его через
`cod-doc question`, MCP `question_*` (13 тулов, профили standard/full) и
веб `/p/<slug>/questions`. Сервис — `cod_doc/services/question_service/`.

`DocumentType.open-question` оставлен ради легаси-данных; такие документы
переносятся в сущность `question_import` / `cod-doc question import` и
удаляются. `context_get.hints.open_questions` заполнен (§4).

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

`OpenQuestion` — параллельная ADR мелкая сущность: «формулировка
вопроса без решения». Когда вопрос закрывается, он ссылается на ADR-id.

| Поле | Смысл |
|------|-------|
| `question_id` | `Q-NNN`, max+1 в проекте |
| `title`, `question` | заголовок и сама формулировка (markdown) |
| `context` | предыстория, ограничения, матрица — markdown |
| `status` | `open` → `resolved` / `dropped`; `reopen` возвращает в `open` и стирает ответ (он остаётся в ревизиях) |
| `priority`, `owner` | срочность и кто отвечает за ответ |
| `options[]` | варианты `{position, title, body, chosen}`; позиция — стабильный id, удаление оставляет дырку |
| `resolution`, `resolved_by_adr` | ответ текстом и/или ADR; при `by_adr` пишется ссылка `resolved_by` |
| `source_doc_key` | из какого документа импортирован |

Ссылки (`open_question_link`): `to_kind` ∈ `document | section | task |
adr | story | scenario | finding | code | url`, `relation` ∈ `about |
blocks | addressed_by | resolved_by | see_also`. Форма `to_ref`
проверяется при записи, существование цели — `question_verify`
(результат `resolved` / `broken_reason` / `last_checked` на ребре; `url`
не проверяется). Код адресуется `path`, `path#symbol` или `path#L10-L20`
и проверяется тем же правилом, что `link` вида `code` в документах; веб
показывает фрагмент кода рядом со ссылкой.

Каждая мутация пишет ревизию (`EntityKind.QUESTION`) и событие
`question.*`; в FTS индексируются только открытые вопросы.

### 2.1 Операции

| Операция | CLI | MCP |
|----------|-----|-----|
| Открыть вопрос | `cod-doc question new -t … -q … [--option …] [--link kind:ref[:relation]]` | `question_create` |
| Карточка / список | `cod-doc question show Q-021` / `list [--status all] [--linked-to kind:ref]` | `question_get` / `question_list` |
| Правка | `cod-doc question edit` | `question_update` |
| Варианты | `cod-doc question option add\|edit\|rm` | `question_option_add` / `_update` / `_remove` |
| Ссылки | `cod-doc question link` / `unlink` | `question_link` (`detach=true`) |
| Проверить ссылки | `cod-doc question verify [Q-021]` (exit 1 при битых) | `question_verify` |
| Закрыть | `cod-doc question resolve Q-021 --by ADR-014 [--option N] [-r текст]` | `question_resolve` |
| Снять / вернуть | `cod-doc question drop --why …` / `reopen` | `question_drop` / `question_reopen` |
| Перенести документ | `cod-doc question import <doc_key> [--dry-run] [--keep-doc]` | `question_import` (`dry_run=true` по умолчанию) |
| Задача под вопрос | `cod-doc task create … --addresses Q-021` | `task_create(addresses=[…])` |

### 2.2 Импорт легаси-документов

`question_import` понимает две формы: документ-на-вопрос (секция
*Question*, варианты `### Option …` / `### Вариант …`, ссылки из
*Navigation* и преамбулы, задачи из `blocking` и строки «Блокирует»;
остальные секции — в `context`) и реестр `### OQ-NNN` (вопрос на пункт,
статус по разделу *Open Items* / *Resolved Archive* и колонке «Статус»
сводной таблицы). Секция, похожая на решение, попадает в `warnings`: вопрос
остаётся открытым, закрывает его человек. После импорта документ удаляется
из БД, файл — с диска; повторный импорт того же документа отказывается.

## 3. Связи

- ADR ↔ task / document / module — через [adr-system](adr-system.md)
  (таблицы `adr_task`, `adr_supersedes`; будущая `adr_link` для doc/module).
- Auto-link `[ADR-NNN]` в любом markdown — через [auto-linking](auto-linking.md)
  (`LinkKind.ADR`).
- `OpenQuestion` → что угодно — через `open_question_link` (§2); обратно
  вопросы видны на страницах задачи и документа и в `question list
  --linked-to`.

## 4. Поверхность для агентов

- `context_get` на глубине L1+ кладёт в `hints.open_questions` до 3
  открытых вопросов: сначала связанные с целью (документ — вместе с его
  секциями, задача), затем добор `critical`/`high` по проекту
  (`relation: null`). См. [context-retrieval](context-retrieval.md).
- `curator_next`: раздел `card.questions` — битые ссылки открытых вопросов
  (`broken_links`, ранг как у LINK-BROKEN); открытые вопросы, у которых все
  задачи `addressed_by` уже `done` (`answered`, с готовой командой
  `question resolve`), и вопросы, открытые > 30 дней без правок (`stale`) —
  оба ниже находок.
- Битость ссылок — по штампам последней проверки. Их обновляет рутина
  `question_links` (по умолчанию `question_links_daily`, каждый день в
  01:15; её заводит `project init`), вручную — `question verify`.
- `ctx_search` находит открытые вопросы (`kind=question`).
- При создании задачи `--addresses Q-021` пишет ссылку `addressed_by`.

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
