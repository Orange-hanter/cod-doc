---
type: rfc
---

# 39 — Граф связей: одно ребро, словарь отношений, одна точка входа

> Категория: 🔵 Архитектура · Риск: средний · Зависимости: proposal 15, proposal 29, proposal 30, proposal 31, proposal 34

## 1. Контекст

Триггер (2026-11-22). Агент в orakul хотел связать две истории и вызвал:

```text
story_link(project="orakul", story_id="US-064", to_kind="story",
           to_ref="US-007", relation="see_also")
→ Error: 'story' is not a valid StoryLinkKind
```

В вызове две ошибки, `to_kind` и `relation`, а тул назвал только первую и
не показал допустимых значений. Главное же в другом: **связь история ↔
история в cod-doc выразить нечем**. `StoryLinkKind` = `task | document |
module`, и перебором параметров агент её не найдёт.

Это не единичный пробел. Каждая новая пара сущностей получала свою таблицу,
свои enum'ы и свой тул. Сейчас в коде 13 механизмов связей (§2), а в
черновиках заложены ещё два: `adr_document` (RFC 30, Q-003) и `idea_link`
(RFC 31, «копия `open_question_link`»). Каждый новый вид связи сейчас стоит
миграции, сервиса, MCP-тула, CLI-команды, веб-фрагмента, записи в
`sync_registry` и теста паритета поверхностей. Поэтому связи, которые
нужны пользователю (история ↔ история, ADR ↔ история, вопрос ↔ план), не
появляются: под каждую нужна своя мини-фича.

Цель RFC: новая связь между любыми двумя сущностями должна стоить **одной
записи в словаре отношений, без миграции**. Агент должен узнавать
допустимые отношения до вызова, а ошибка должна сама называть правильный
вызов.

## 2. Текущее состояние (проверено по коду 2026-11-22)

### 2.1. Три таблицы одной формы `to_kind / to_ref / relation`

| | `story_link` | `scenario_link` | `open_question_link` |
|---|---|---|---|
| ORM | `infra/models/stories.py:96` | `infra/models/scenarios.py:102` | `infra/models/questions.py:95` |
| Kinds | `StoryLinkKind` (`domain/entities.py:227`): task, document, module | `ScenarioLinkKind` (`:300`): task, story, document, criterion | `QuestionLinkKind` (`:333`): document, section, task, adr, story, scenario, finding, code, url |
| Relations | `StoryRelation` (`:233`): implemented_by, specified_in, owned_by | `ScenarioRelation` (`:314`): verifies, specified_in, exercised_by | `QuestionRelation` (`:353`): about, blocks, addressed_by, resolved_by, see_also |
| UNIQUE | **нет** (только в сервисе) | (scenario, kind, ref, relation) | (question, kind, ref, relation) |
| Проверка цели | при записи (`story_service/links.py:33`) | **нет** | отложенная, `question_verify` (`question_service/verify.py:119`) |
| unlink | **нет** | есть | есть |
| `to_ref` | String(255) | String(255) | String(512) |
| Строк (локальная БД) | 0 | 16 | 45 |
| Событий `*.linked` на Гермесе (оценка сверху, 2026-11-23): orakul / Restate / cod-doc | 508 / 423 / 13 | 1 / 11 / 16 | 211−7 / 389−4 / 45 |

Все три синхронизируются как authored. `to_ref` у них лежит в
`human_id_refs` с дискриминатором `to_kind` (`infra/sync_registry.py:46-50`),
то есть полиморфную строковую ссылку sync уже поддерживает.

### 2.2. Рёбра со смыслом (FK на обоих концах)

| Таблица | Семантика, которой нет у простой связи | Строк |
|---|---|---|
| `dependency` (`infra/models/plans.py:142`) | запрет циклов, обязательная `note`, ready-множество `plan_ready`, roadmap; `kind` — свободная строка, используется только `blocks`, MCP его не выставляет | 276 |
| `adr_supersedes` (`infra/models/adrs.py:86`) | меняет статус старого ADR, проверяет циклы (`adr_service.py:688`) | 1 |
| `adr_relation` (`adrs.py:124`) | CHECK `amends|depends_on`, проверка циклов | 3 |
| `module_dependency` (`infra/models/modules.py:50`) | писателя нет | 0 |

### 2.3. Смешанные и одноколоночные ссылки

- `adr_task` (`adrs.py:179`): существование задачи не проверяется, unlink
  нет, в CLI не выставлен. 8 строк.
- `approval_task_link` / `approval_doc_revision_link`
  (`infra/models/approvals.py:67/85`): UNIQUE нет. 0 строк.
- `commit_link` (`infra/models/commits.py:21`): строится из git log, 330
  строк. Это производное ребро, его не трогаем.
- Колонки-ссылки: `finding.promoted_task_id`,
  `open_question.resolved_by_adr`, `routine_run.created_task_id` и иерархия
  (`task.plan_id`, `document.node_id`, …).

### 2.4. Ссылки из прозы (производные)

Таблица `link` (`infra/models/documents.py:198`) содержит то, что парсер
нашёл в теле секций (968 строк). Колонки отношения у неё нет. Каскады
переименований и якорей приходят событиями `doc_events` (ADR-018). Таблица
DERIVED и не синхронизируется. Её модель правильная, RFC её не меняет, а
только читает (§3.6).

### 2.5. Что уже готово для общего механизма

- `EntityKind` (`domain/entities.py:375`) — единый словарь видов сущностей.
- `services/entity_ref_service.py` переводит `(kind, row_id)` в публичные
  ключи (task_id, doc_key, story_id, adr_id).
- Грамматика `kind:ref` уже есть в прозе: парсер ловит `[[task:ID]]`,
  `[[story:ID]]`, `[[doc:KEY#anchor]]` (`link_service/parser.py`).
- Sync поддерживает полиморфный `human_id_refs` с дискриминатором.

### 2.6. Расхождения, которые даёт разнобой

- Одно понятие называется по-разному: `dependency.blocks` и
  `open_question_link.blocks`, `specified_in` есть у историй и у сценариев,
  но это разные enum'ы.
- Во всех MCP-тулах параметры `str`, допустимые значения есть только в
  docstring (`mcp/tools/story_tools.py:188`), а CLI использует
  `click.Choice`. Агент видит меньше, чем человек.
- Ошибка — голый `ValueError` от `StrEnum`: в нём нет списка допустимых
  значений и проверяется только первый невалидный параметр.

## 3. Предложение

Три слоя:

1. **Ссылка на сущность**: строка `kind:ref`, один парсер и один резолвер.
2. **Словарь отношений**: данные, а не enum. Встроенные типы лежат в коде,
   проектные — в таблице.
3. **Хранилище**: одна authored-таблица `relation` для простых связей.
   Рёбра со смыслом (§2.2) остаются в своих таблицах, но пишутся и читаются
   через тот же вход.

### 3.1. Ссылка на сущность

```text
ref       := kind ":" key
kind      := task | story | criterion | document | section | adr | question
           | scenario | plan | plan_section | finding | module | idea | code | url
key       := task_id | story_id | doc_key | doc_key "#" anchor | adr_id | Q-NNN
           | SC-NNN | plan.scope | <story_id> "#" <position> | path "::" symbol | URL
```

Примеры: `story:US-007`, `doc:docs/system/MASTER.md#registry`,
`criterion:US-064#2`, `code:cod_doc/services/task_service.py::complete`.

Ключ — публичный id, а не `row_id`. Причины:

- он стабилен между репликами, и sync уже умеет его переписывать
  (`human_id_refs`);
- он совпадает с синтаксисом прозы, так что ребро и `[[story:US-007]]` в
  тексте адресуют одно и то же;
- `row_id` не переживает экспорт и импорт.

Цена — переименования. `doc_key` и якорь меняются, а `task_id`, `story_id`
и `adr_id` нет. Поэтому подписчик на `DocumentRenamed` и
`SectionAnchorChanged` переписывает ссылки в `relation` так же, как
`rename_cascade` делает это для `link` (§3.5).

Модуль `services/entity_ref.py` (лист, без импортов сервисов):

```python
@dataclass(frozen=True)
class EntityRef:
    kind: RefKind          # StrEnum, надмножество EntityKind + code/url/criterion
    key: str
    def __str__(self) -> str: ...          # "story:US-007"

def parse_ref(raw: str) -> EntityRef: ...  # RefSyntaxError с примером формата
def resolve(session, project_id, ref: EntityRef) -> ResolvedRef | None: ...
def resolve_many(session, project_id, refs) -> dict[EntityRef, ResolvedRef]: ...
```

`resolve` расширяет существующий `entity_ref_service` в обратную сторону:
он отдаёт `row_id` и заголовок, а для `code`/`url` возвращает `None` без
ошибки (их проверяет отдельный верификатор).

### 3.2. Словарь отношений

Тип отношения — запись, а не член enum:

```python
@dataclass(frozen=True)
class RelationType:
    name: str                      # "depends_on" — читается от источника
    inverse: str                   # "required_by" — читается от цели
    from_kinds: frozenset[RefKind]
    to_kinds: frozenset[RefKind]
    symmetric: bool = False        # relates_to: A→B ≡ B→A, хранится одна строка
    acyclic: bool = False          # проверка пути до записи
    target_check: Literal["strict", "deferred"] = "strict"
    backend: str = "relation"      # "relation" | "dependency" | "adr_relation" | "adr_supersedes"
    aliases: frozenset[str] = frozenset()   # старые имена: "see_also" → "relates_to"
    description: str = ""
```

`strict` означает, что цель обязана существовать на момент записи.
`deferred` разрешает запись, а `relation_verify` потом ставит `resolved` и
`broken_reason`, как это сейчас делает `question_verify`. `deferred` нужен
для `code`/`url` и для ссылок вперёд на ещё не созданный документ.

**Встроенный словарь** живёт в `domain/relation_types.py`. Его стартовый
состав покрывает весь §2 и пробел из триггера:

| name / inverse | from → to | флаги | откуда |
|---|---|---|---|
| `relates_to` / `relates_to` | любой → любой | symmetric; алиас `see_also` | общий запасной вариант, вызов из триггера |
| `depends_on` / `required_by` | story → story | acyclic | новое |
| `duplicates` / `duplicated_by` | story, task, question → тот же kind | | новое |
| `splits_into` / `split_from` | story → story | acyclic | новое |
| `implemented_by` / `implements` | story → task | | `StoryRelation` |
| `specified_in` / `specifies` | story, scenario → document, section | | `StoryRelation`, `ScenarioRelation` |
| `owned_by` / `owns` | story → module | | `StoryRelation` |
| `verifies` / `verified_by` | scenario → task, story, criterion, document | | `ScenarioRelation` |
| `exercised_by` / `exercises` | scenario → task, code | | `ScenarioRelation` |
| `about` / `discussed_in` | question → любой | deferred | `QuestionRelation` |
| `blocks` / `blocked_by` | question → любой | deferred | `QuestionRelation` |
| `addressed_by`, `resolved_by` | question → task, adr, document | | `QuestionRelation` |
| `implemented_by` / `implements`, `invalidated_by` / `invalidates`, `discovered_by` / `discovers` | adr → task | | `adr_task` (`implements|invalidates|discovers`; `relates` → `relates_to`) |
| `decided_in` / `decides` | adr → document | | `adr_document` из RFC 30 (Q-003); таблица не создаётся |
| `promoted_to` / `promoted_from` | idea → task, story, document | | `idea_link` из RFC 31; таблица не создаётся |
| `blocked_by` / `blocks` | task → task | acyclic, backend=`dependency` | `dependency` |
| `amends`, `depends_on` | adr → adr | acyclic, backend=`adr_relation` | `adr_relation` |
| `supersedes` / `superseded_by` | adr → adr | acyclic, backend=`adr_supersedes` | `adr_supersedes` |

Имена уникальны в паре `(from_kind, to_kind)`, а не глобально:
`depends_on` у истории и у ADR — разные записи с разными бэкендами. Поиск
типа идёт по `(name | alias | inverse, from_kind, to_kind)`. Если указать
`inverse` с переставленными концами, получится то же ребро:
`relate(task:X, story:Y, "implements")` ≡
`relate(story:Y, task:X, "implemented_by")`.

**Проектные типы** — это «гибкость» RFC. Таблица `relation_type`
(project-scoped, authored, синхронизируется) имеет те же поля, кроме
`backend`: проектный тип всегда хранится в `relation`. Встроенный тип
переопределить нельзя, имя проверяется на столкновение в каждой паре kinds.
Новая связь вроде «история ↔ персона из документа», «вопрос ↔ план» или
«риск ↔ ADR» заводится вызовом `relation_type_define`, без миграции и без
нового тула.

### 3.3. Хранилище

```sql
CREATE TABLE relation (
  row_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  uid           VARCHAR(26) NOT NULL UNIQUE,   -- deterministic: hash(project, from, type, to)
  project_id    INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  from_kind     VARCHAR(16) NOT NULL,
  from_ref      VARCHAR(512) NOT NULL,
  type          VARCHAR(48) NOT NULL,          -- каноническое name, не алиас и не inverse
  to_kind       VARCHAR(16) NOT NULL,
  to_ref        VARCHAR(512) NOT NULL,
  note          TEXT,
  resolved      BOOLEAN NOT NULL DEFAULT 1,
  broken_reason VARCHAR(32),
  last_checked  DATETIME,
  author        VARCHAR(128) NOT NULL,
  created_at    DATETIME NOT NULL,
  UNIQUE (project_id, from_kind, from_ref, type, to_kind, to_ref)
);
CREATE INDEX ix_relation_to   ON relation (project_id, to_kind, to_ref);
CREATE INDEX ix_relation_from ON relation (project_id, from_kind, from_ref);

CREATE TABLE relation_type (
  row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  uid         VARCHAR(26) NOT NULL UNIQUE,
  project_id  INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  name        VARCHAR(48) NOT NULL,
  inverse     VARCHAR(48) NOT NULL,
  from_kinds  JSON NOT NULL,
  to_kinds    JSON NOT NULL,
  symmetric   BOOLEAN NOT NULL DEFAULT 0,
  acyclic     BOOLEAN NOT NULL DEFAULT 0,
  target_check VARCHAR(8) NOT NULL DEFAULT 'strict',
  description TEXT,
  deprecated  BOOLEAN NOT NULL DEFAULT 0,
  UNIQUE (project_id, name)
);
```

- У симметричного типа концы нормализуются, `(min, max)` по строке `ref`.
  Так `A relates_to B` и `B relates_to A` попадают в одну строку UNIQUE.
- FK на концах нет. Поэтому удаление сущности обрабатывает сервис (§3.5), а
  не CASCADE.
- `uid` детерминированный, как у `dependency`: одно и то же ребро,
  созданное на двух репликах, совпадает при sync и не дублируется.
- В `sync_registry`: authored, `human_id_refs={"from_ref": "from_kind",
  "to_ref": "to_kind"}`. Механизм дискриминатора тот же, что у трёх таблиц
  из §2.1. Строку синхронизируют, только если синхронизированы оба конца
  (gated, как сейчас).

### 3.4. Сервис и контракт ошибок

`services/relation_service/` — один вход для записи и чтения:

```python
def relate(session, *, project_id, from_ref: str, type: str, to_ref: str,
           author: str, note: str | None = None) -> RelationEdge
def unrelate(session, *, project_id, from_ref: str, type: str, to_ref: str,
             author: str) -> bool
def relations(session, *, project_id, ref: str,
              direction: Literal["out", "in", "both"] = "both",
              types: Sequence[str] | None = None,
              include_derived: bool = True) -> list[RelationView]
def relation_types(session, *, project_id, from_kind: str | None = None,
                   to_kind: str | None = None) -> list[RelationType]
def define_type(session, *, project_id, spec: RelationTypeSpec, author: str) -> RelationType
def verify(session, *, project_id, refs: Sequence[str] | None = None) -> VerifyReport
```

`relate` по шагам:

1. `parse_ref` обоих концов.
2. Поиск типа по паре kinds с учётом алиасов и `inverse`.
3. Проверка `target_check`.
4. Для `acyclic` — проверка пути: обход по `relation`, а для бэкендов —
   существующие `_has_path`.
5. Диспетчер `backend`: `relation` пишет строку, `dependency` вызывает
   `task_service.add_dependency` (с его `note` и проверкой циклов),
   `adr_*` вызывает `adr_service.relate` / `supersede`.
6. Revision и activity через
   `activity_service.write_revision_and_emit_event` (ADO-040). Событие
   `relation.added` / `relation.removed` с `entity_kind=RELATION` (новый
   член `EntityKind`). В payload лежат оба `ref`, чтобы журнал можно было
   фильтровать по любому концу.

Бэкенды сохраняют собственную семантику: ready-множество, смену статуса
ADR, обязательную `note`. Единым становится вход, а не хранение.

**Ошибка — это подсказка** (`RelationError(ValueError)`, сообщение +
структурное поле `hint`). Все параметры валидируются за один проход, ошибки
отдаются списком:

Вызов из триггера в новой модели проходит: `see_also` — алиас
`relates_to`, и запись ложится под каноническим именем. Ошибка при опечатке
выглядит так:

```text
relation 'depend_on' не определена для story → story. Похоже: depends_on.
Допустимо для story → story: relates_to (алиас see_also), depends_on,
duplicates, splits_into; обратные: required_by, duplicated_by, split_from.
Пример: relate(from_ref="story:US-064", type="depends_on", to_ref="story:US-007")
```

Ближайшее имя ищется по расстоянию Левенштейна ≤ 2 и по алиасам. Если пара
kinds не допускает ни одного типа, кроме `relates_to`, ошибка так и
говорит и называет `relation_type_define` для своего типа.

### 3.5. Жизненный цикл концов

| Событие | Реакция |
|---|---|
| `DocumentRenamed`, `SectionAnchorChanged` | подписчик `relation_service.doc_event_handlers` переписывает `from_ref`/`to_ref` в той же транзакции; ставится в `event_wiring.install()` рядом с `link_service` (ADR-018) |
| удаление сущности (task, story, document, …) | рёбра `strict`-типов удаляются тем же сервисом с событием `relation.removed`; у `deferred` ставится `resolved=0`, `broken_reason='target_deleted'` |
| переименование `task_id` при sync (provisional → final) | уже покрыто `human_id_refs` |
| `relation_verify` (рутина, как `question_verify`) | пересчитывает `resolved` для `deferred`; `code:` проверяется через `link_service.resolve_code_ref` |

### 3.6. Единое чтение

`relations(ref)` возвращает одну ленту `RelationView`:

```python
@dataclass(frozen=True)
class RelationView:
    label: str            # name со стороны источника, inverse со стороны цели
    other: EntityRef
    other_title: str | None
    direction: Literal["out", "in"]
    source: Literal["relation", "dependency", "adr_relation", "adr_supersedes",
                    "prose", "commit"]
    resolved: bool
    note: str | None
```

Источники:

- `relation` и бэкенды;
- при `include_derived` — входящие и исходящие ссылки из прозы (`link`,
  метка `mentions` / `mentioned_in`) и `commit_link` (`committed_in`).

Ссылки из прозы только читаются. Писать их через `relate` нельзя: их
источник — текст секции.

Это закрывает чтение для `context_service` L1 («прямые связи»): сейчас там
отдельные join'ы по `StoryLinkModel` (`context_service.py:249`) и другим
таблицам, после RFC будет один вызов.

### 3.7. Поверхности

Мутации `relation_service` попадают под гейт паритета поверхностей, новая
спека `test_relation_mutation_surface_parity.py`.

| Операция | MCP | CLI |
|---|---|---|
| связать / развязать | `relate`, `unrelate` | `cod-doc rel add <from> <type> <to> [--note]`, `rel rm` |
| прочитать | `relations` | `cod-doc rel ls <ref> [--in/--out] [--type]` |
| словарь | `relation_types` | `cod-doc rel types [--from story] [--to story]` |
| свой тип | `relation_type_define`, `relation_type_deprecate` | `cod-doc rel type define …`, `rel type deprecate` |
| проверка | `relation_verify` | `cod-doc rel verify` |

- `relations`, `relation_types` входят в `READER_TOOLS`
  (`mcp/access_policy.py:29`). `relate`/`unrelate` требуют writer+, их
  проверяет `emit`. `relation_type_define` требует writer+.
- Тулы видны на профилях `standard`/`full`. На `agent` видны `relations` и
  `relation_types`: куратору связи нужны для навигации.
- Параметры, которые остаются enum-ами в узких тулах (§4), получают
  `Literal[...]` в сигнатуре. Тогда FastMCP отдаёт `enum` в JSON-схеме, и
  агент видит значения до вызова. В `relate` тип — `str`: словарь проектный
  и динамический, а его разведка — `relation_types`.
- Веб: один фрагмент `_frag/relations.html` («Связи», группировка по
  `label`, бейдж источника) на страницах задачи, истории, документа, ADR,
  вопроса и сценария. Он заменяет `linked_questions.html` и панели связей в
  `story_show`/`scenario_show`/`adr_show`. Форма «добавить связь» берёт
  допустимые типы из `relation_types(from_kind=…)`.
- Скилл `task-standard` / `doc-structure` (или новый `relations`) объясняет,
  когда какое отношение брать. Поведение агента меняется скиллом, а не
  промптом.

## 4. Миграция и обратная совместимость

Порядок выбран так, чтобы новое заработало раньше, чем трогаются старые
данные.

1. **Фаза A, ядро.** Миграция `0058_relation` (номер по факту) создаёт
   `relation` и `relation_type`. Существующие таблицы не трогаются,
   `document` тоже, так что риска `batch_alter_table` нет. Добавляются
   `entity_ref`, встроенный словарь и `relation_service` с бэкендом
   `relation`. С этого момента работают story ↔ story и проектные типы.
2. **Фаза B, поверхности.** MCP/CLI/веб из §3.7, `relations()` с бэкендами
   и прозой (только чтение), регенерация `_cod-doc` и счётчиков профилей
   (`scripts/regen.sh`).
3. **Фаза C, поглощение таблиц одной формы.** Миграция переносит
   `story_link`, `scenario_link`, `open_question_link`, `adr_task`,
   `approval_task_link` в `relation`. Отображение типов — таблица §3.2,
   `criterion` переходит как `criterion:<story_id>#<pos>`. Старые таблицы
   остаются и не пишутся один релиз. Затем их удаляет отдельная миграция с
   тестом сохранности по образцу
   `tests/services/test_migration_0035_preserves_data.py`: налить данные на
   старой ревизии, прогнать upgrade, сравнить множества рёбер.
   Старые тулы (`story_link`, `scenario_link`, `question_link`,
   `adr_link_task`) остаются тонкими обёртками над `relate` с тем же
   контрактом ответа и помечаются deprecated в docstring. Удаление тулов —
   отдельное решение после одного цикла `cod-doc update` на Гермесе.
   Объём по событиям на Гермесе (§2.1): около 930 строк `story_link`
   (orakul + Restate) и около 590 `open_question_link`. Событие — оценка
   сверху: `task_create(story_id=)` пишет связь тем же событием, а UNIQUE у
   `story_link` нет. **До миграции** — точный `COUNT(*)` и поиск дублей
   `(story_id, to_kind, to_ref, relation)` в БД узла: дубли схлопываются,
   иначе упадёт UNIQUE новой таблицы.
4. **Фаза D, бэкенды.** Через `relate` начинают писаться `dependency`,
   `adr_relation` и `adr_supersedes`. Их таблицы, тулы и горячий путь
   `plan_ready` не меняются. `task_add_dependency` остаётся основным тулом
   задач.

RFC 30 и RFC 31 при реализации заводят типы `decided_in` и `promoted_to`
вместо таблиц `adr_document` и `idea_link`. Их тексты правятся в той же
ветке, что и фаза A, если RFC 39 принят раньше их декомпозиции.

Ломается:

- формат ошибок старых тулов (текст становится подробнее, тип исключения
  тот же — `ValueError`);
- поиск по `activity_event.kind` в `story.linked` / `scenario.linked`:
  после фазы C новые события называются `relation.added`. Старые события
  остаются в журнале, фильтры веба и `activity_summary` учитывают оба
  имени.

## 5. Риски и что не делаем

- **Не переносим `dependency`** в `relation`. 276 рёбер, ready-множество,
  `plan_critical_path` и roadmap читают её напрямую, а выигрыша от переноса
  нет.
- **Не переносим ссылки из прозы.** `link` — производная таблица, её
  истина — текст. `relations()` её только читает.
- **Новый тип рёбер не пишет событий на пересчёт.** `relation.added` /
  `relation.removed` — только на авторскую мутацию; `relation_verify`
  меняет `resolved` без события. Причина — `link.*`: на Гермесе
  `link.synced` + `link.resolved` + `link.verified` дают 780 тыс. из ~795 тыс.
  событий orakul, 635 тыс. у Restate, 876 тыс. у cod-doc (2026-11-23).
  Это отдельный долг `link_service`, RFC его не чинит.
- **Не трогаем `code_edge`, `commit_link`, `link_suggestion`.** Они
  производные (граф кода, git log, эмбеддинги). `link_suggestion` при
  accept может создавать ребро `relation` вместо вставки `[label](key)` в
  тело — отдельная задача, не в этом RFC.
- **Нет межпроектных рёбер.** Грамматика резервирует `project/kind:ref`, но
  в V1 это отвергается: нет модели прав на чужой проект (RFC 29).
- **Нет языка запросов к графу и транзитивных обходов в API**, кроме
  проверки ацикличности. Обход L2 делает `context_service`, как сейчас.
- **Нет весов и confidence у рёбер.** Это свойство производных графов, а не
  авторских связей.
- **Нет свободных отношений без словаря.** `relate` с неизвестным типом
  падает с подсказкой, а не создаёт тип молча, иначе словарь разрастётся
  синонимами. Запасной вариант — `relates_to` или явный
  `relation_type_define`.
- **Риск: колонки-ссылки** (`finding.promoted_task_id`,
  `open_question.resolved_by_adr`) остаются колонками. `relations()`
  показывает их как источник `column` только если это понадобится вебу.
  Пока это не цель.
- **Риск: дрейф словаря между репликами.** Проектные типы синхронизируются.
  Ребро с типом, которого реплика не знает, при применении sync не
  отвергается, а получает `resolved=0`, `broken_reason='unknown_type'`;
  видно его через `relation_verify`.
- **Риск: строковые ссылки без FK.** Висячие ссылки возможны, если сущность
  удалить мимо сервиса. От этого страхуют гейт
  `test_presentation_no_orm_writes.py` (presentation не пишет в ORM) и
  рутина `relation_verify`.

## 6. Открытые вопросы

| # | Вопрос | Предлагаемый ответ |
|---|---|---|
| R39-Q1 | Кто вправе заводить проектный тип: любой writer или через approval? | writer, но агентский `author` идёт через approval `relation_type` (как `doc_patch` у куратора); человек — напрямую |
| R39-Q2 | Ключ конца — публичный id или `row_id` + kind? | публичный id (§3.1); цена — подписчик переименований |
| R39-Q3 | Удалять ли старые тулы после фазы C? | нет, пока `tools_diff` по журналу Гермеса показывает их вызовы |
| R39-Q4 | `relates_to` разрешён для любой пары — не станет ли он свалкой? | в веб-форме он последний в списке, и рутина `doc_node_health`-стиля сообщает, если его доля выше 50% по проекту |

## 7. Оценка

| Секция | Задачи | Объём |
|---|---|---|
| A. Ядро | грамматика и резолвер `entity_ref`; встроенный словарь; миграция `relation` + `relation_type`; `relate`/`unrelate`/`define_type` с контрактом ошибок; подписчик переименований и удалений | 5 задач |
| B. Поверхности | MCP-тулы + профили + `READER_TOOLS`; CLI `rel` + completion; `relations()` с прозой и бэкендами; веб-фрагмент «Связи»; скилл | 5 задач |
| C. Поглощение | миграция данных + тест сохранности; обёртки старых тулов; `context_service` на `relations()`; `sync_registry` | 4 задачи |
| D. Бэкенды | диспетчер на `dependency`/`adr_*`; `Literal` в оставшихся узких тулах | 2 задачи |

Итого 16 задач, 3–4 недели. A и B дают ценность сами по себе: история ↔
история и проектные типы без поглощения. C и D можно отложить.
План — `relation-graph-2026-12` (префикс REL).
