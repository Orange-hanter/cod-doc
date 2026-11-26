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

`kind:ref` — это **формат ввода и вывода**. В хранилище конец ребра
адресуется `uid` строки-цели (`SyncUidMixin`, `infra/models/base.py:15`).
Он есть у всех authored-сущностей, включая `section`
(`documents.py:173`) и критерий истории (`stories.py:81`). Публичный ключ
для хранения не годится, потому что он меняется:

- `doc_key` при `doc_rename`, якорь при переименовании заголовка;
- `task_id`/`story_id`/`adr_id` при закреплении провизорных ID на push
  (`sync_service/leases.py:20`, `pin_provisional`). На провизорную задачу
  ссылаться можно уже сейчас, а через минуту у неё другой ключ.

`uid` не меняется ни при одном из этих событий. Значит, не нужны ни
каскад переименований, ни переписывание `human_id_refs` при sync: `uid`
глобален по построению (RFC 29 §3.3). Ключ для вывода вычисляется при
чтении пачкой (`resolve_many`, один запрос на kind), как это уже делает
`entity_ref_service` для журналов.

У концов, которые не являются строками БД (`code:`, `url:`), `uid` нет.
Они хранятся литералом в `*_key` и проверяются отложенно (§3.5).

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
по ключу он отдаёт `uid`, `row_id` и заголовок, а для `code`/`url`
возвращает `None` без ошибки (их проверяет отдельный верификатор).
Обратная функция `display_refs(uids)` переводит `uid` в текущий `kind:ref`
для вывода.

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
    allow_forward: bool = False    # можно сослаться на ещё не созданную сущность
    backend: str = "relation"      # "relation" | "dependency" | "adr_relation" | "adr_supersedes"
    aliases: frozenset[str] = frozenset()   # старые имена: "see_also" → "relates_to"
    consumers: frozenset[str] = frozenset() # кто читает тип; пусто — тип не встроенный
    description: str = ""
```

Строгость проверки цели зависит от **вида цели**, а не от типа:

- цель-сущность должна существовать на момент записи, и в ребро ложится
  её `uid`;
- `code:`/`url:` записываются всегда, а `relation_verify` потом ставит
  `resolved` и `broken_reason`, как сейчас `question_verify`;
- тип с `allow_forward=True` (например `about` у вопроса) принимает
  ссылку на несуществующую сущность. Она хранится ключом с
  `to_uid IS NULL`, `resolved=0`, а `relation_verify` дописывает `uid`,
  когда сущность появится.

Флаг на типе не годится. `relates_to` допускает и `story:`, и `url:`, и
ему пришлось бы быть одновременно строгим и отложенным.

`consumers` — обязательное поле встроенного типа: экран, тул или правило,
которое читает этот тип и по-разному ведёт себя в зависимости от него
(`context_service` L1, веб-панель, проверка куратора, `plan_ready`). Тип,
который никто не читает, — это метка. Для метки есть `relates_to` с
`note`, а встроенный словарь пополняется только типами с потребителем.
Гейт `test_relation_types_have_consumers` сверяет `consumers` с
регистрацией потребителей.

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

**Неоднозначность разрешается до записи, а не во время вызова.** Словарь
выше уже содержит столкновения. `blocks`/`blocked_by` есть и у вопроса
(«вопрос → любой»), и у `dependency` (task → task). Запрос
`relate(task:A, "blocked_by", task:B)` должен попасть в `dependency`.
Запрос `relate(task:A, "blocked_by", question:Q)` должен попасть в
обратную сторону вопросного `blocks`. Правила:

1. «Любой» в `from_kinds`/`to_kinds` раскрывается в явный список kinds при
   загрузке словаря. Словарь — конечная таблица троек
   `(токен, from_kind, to_kind) → (тип, направление)`.
2. Тип с конкретной парой kinds побеждает тип с раскрытым «любой».
3. Две записи одной специфичности на одну тройку — ошибка загрузки
   словаря. Встроенный словарь проверяет гейт
   `test_relation_vocabulary_unambiguous`, проектный тип — `define_type`
   при регистрации.

Ответ `relate` всегда содержит канонический тип и направление, в котором
ребро записано. Агент видит, что `blocked_by` у задач стал `dependency`.

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
  from_uid      TEXT NOT NULL,                 -- источник всегда сущность
  type          VARCHAR(48) NOT NULL,          -- каноническое name, не алиас и не inverse
  to_kind       VARCHAR(16) NOT NULL,
  to_uid        TEXT,                          -- NULL: code/url или ссылка вперёд
  to_key        VARCHAR(512),                  -- литерал для code/url/вперёд; иначе NULL
  note          TEXT,
  resolved      BOOLEAN NOT NULL DEFAULT 1,    -- local: пересчитывает relation_verify
  broken_reason VARCHAR(32),                   -- local
  last_checked  DATETIME,                      -- local
  author        VARCHAR(128) NOT NULL,
  created_at    DATETIME NOT NULL,
  CHECK (to_uid IS NOT NULL OR to_key IS NOT NULL),
  UNIQUE (project_id, from_uid, type, to_kind, to_uid, to_key)
);
CREATE INDEX ix_relation_to   ON relation (project_id, to_uid);
CREATE INDEX ix_relation_key  ON relation (project_id, to_kind, to_key);
CREATE INDEX ix_relation_from ON relation (project_id, from_uid);

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
  allow_forward BOOLEAN NOT NULL DEFAULT 0,
  description TEXT,
  deprecated  BOOLEAN NOT NULL DEFAULT 0,
  UNIQUE (project_id, name)
);
```

- У симметричного типа концы нормализуются, `(min, max)` по `uid`. Так
  `A relates_to B` и `B relates_to A` попадают в одну строку UNIQUE.
  Симметричное ребро к `code:`/`url:` запрещено: у такого конца нет `uid`,
  и он не может быть источником.
- В SQLite `NULL` в UNIQUE не равен `NULL`, поэтому дубли с `to_uid IS NULL`
  ловит сервис, а частичный индекс `UNIQUE (project_id, from_uid, type,
  to_kind, to_key) WHERE to_uid IS NULL` страхует его.
- FK на концах нет. Удаление сущности обрабатывается событием (§3.5), а не
  CASCADE.
- `uid` ребра детерминированный, как у `dependency`: одно и то же ребро,
  созданное на двух репликах, совпадает при sync и не дублируется. `uid`
  проектного типа — `hash(project, name)`. Два разных определения одного
  имени на двух репликах дают конфликт sync, и он идёт в существующую
  очередь `sync_conflicts`, а не перезаписывается молча.
- В `sync_registry`: authored; `from_uid`/`to_uid` — полиморфные логические
  FK с дискриминатором `*_kind` (механизм `POLYMORPHIC_FKS`,
  `sync_registry.py:42`). `human_id_refs` не нужен. `resolved`,
  `broken_reason`, `last_checked` — local-колонки: это результат проверки
  на конкретной реплике, а не авторское решение. Gating: строка уходит,
  когда синхронизированы оба конца; конец без `uid` считается
  присутствующим.

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
3. Проверка цели по её виду (§3.2): сущность резолвится в `uid`, `code:`/`url:` записываются как есть, ссылка вперёд — только при `allow_forward`.
4. Для `acyclic` — проверка пути: обход по `relation`, а для бэкендов —
   существующие `_has_path`.
5. Диспетчер `backend`: `relation` пишет строку, `dependency` вызывает
   `task_service.add_dependency` (с его `note` и проверкой циклов),
   `adr_*` вызывает `adr_service.relate` / `supersede`.
6. Revision и activity через
   `activity_service.write_revision_and_emit_event` (ADO-040). Ревизия
   пишется на **сущность-источник**, как сейчас у `story.linked`: история
   истории продолжает показывать, когда к ней привязали задачу. Событие
   `relation.added` / `relation.removed` с `entity_kind` источника. В
   payload лежат `uid` и `kind:ref` обоих концов, поэтому журнал цели
   находит событие по `to_uid`. `revision_revert` такой ревизии вызывает
   `unrelate`/`relate`, а не правит строку напрямую.

Зависимости модулей идут в одну сторону: `relation_service` импортирует
`task_service` и `adr_service` (диспетчер бэкендов), но ни один сервис не
импортирует `relation_service`. Удаления приходят к нему событием (§3.5).
Иначе каждый удаляющий сервис стал бы зависеть от `relation_service`, а
`relation_service` от него, и кольцо SVC-006 выросло бы сверх
`MAX_SCC_SIZE`.

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
| переименование документа, якоря; закрепление провизорного ID | ничего: ребро держит `uid` |
| удаление сущности | новое событие `entity_events.EntityDeleted(session, kind, uid)` (по образцу `doc_events`, ADR-018); подписчик удаляет рёбра обоих направлений и пишет `relation.removed` с причиной `endpoint_deleted` |
| tombstone из sync (`sync_service/tombstone.py`) | тот же `EntityDeleted` |
| `relation_verify` (рутина, как `question_verify`) | для `code:` зовёт `link_service.resolve_code_ref`, для `url:` проверяет форму, ссылкам вперёд дописывает `to_uid`; событий не пишет |

Сейчас удаление не публикует ничего: `doc_service.delete`
(`doc_service.py:1287`) сам вычищает `link` своего документа, а
`question_service.delete`, `doc_tree_service.delete_node` и
`plan_service.delete_section` про чужие ссылки не знают. Задача ядра —
публиковать `EntityDeleted` из каждого удаляющего пути. Гейт
`test_entity_deleted_published` находит удаляющие функции сервисов тем же
AST-сканером, что и паритет поверхностей (`tests/services/_surface_parity.py`),
и требует публикации. Задачи и истории не удаляются вовсе (`delete` в
`task_service`/`story_service` нет), поэтому для них путь только один —
tombstone.

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
- Параметры, которые остаются enum-ами в узких тулах, получают
  `Literal[...]` уже в фазе 0 (§4). В `relate` тип — `str`: словарь проектный
  и динамический, а его разведка — `relation_types`.
- Веб: один фрагмент `_frag/relations.html` («Связи», группировка по
  `label`, бейдж источника) на страницах задачи, истории, документа, ADR,
  вопроса и сценария. Он заменяет `linked_questions.html` и панели связей в
  `story_show`/`scenario_show`/`adr_show`. Форма «добавить связь» берёт
  допустимые типы из `relation_types(from_kind=…)`.
- Скилл `task-standard` / `doc-structure` (или новый `relations`) объясняет,
  когда какое отношение брать. Поведение агента меняется скиллом, а не
  промптом.

### 3.8. Запрет на новые таблицы связей

Без запрета RFC добавит четырнадцатый механизм, а не заменит тринадцать:
следующая фича опять заведёт `*_link` «потому что так быстрее». Гейт
`test_no_new_link_tables` проходит по метаданным `Base` и находит модели,
похожие на связь:

- колонки `to_kind`+`to_ref` или `*_kind`+`*_uid`;
- два FK на сущности и ничего, кроме метаданных ребра;
- имя `*_link`, `*_relation`, `*_edge`.

Найденная модель вне allowlist роняет тест. Allowlist — ratchet, как
`per-file-ignores`: может только уменьшаться. Стартовый состав — всё из
§2, с обоснованием каждой строки. Выход фазы C измеряется длиной этого
списка.

## 4. Миграция и обратная совместимость

Порядок выбран так, чтобы новое заработало раньше, чем трогаются старые
данные.

0. **Фаза 0, независимо от решения по RFC.** Чинит то, что сломалось в
   триггере, за один-два дня:
   - `Literal[...]` вместо `str` для `to_kind`/`relation` в `story_link`,
     `scenario_link`, `question_link`, `adr_link_task`, `adr_relate`.
     FastMCP отдаёт `enum` в JSON-схеме, и агент видит допустимые значения
     до вызова;
   - валидация всех параметров за один проход и ошибка со списком
     допустимых значений вместо голого `'story' is not a valid
     StoryLinkKind`.

   Связи история ↔ история фаза 0 не даёт. Она только сокращает агенту
   путь до вывода «так связать нельзя» с нескольких вызовов до одного.

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
   `approval_task_link` в `relation`. Отображение типов — таблица §3.2.
   Строковый `to_ref` старых таблиц переводится в `uid` резолвом на месте.
   Не найденная цель (`question_link` к удалённому документу, `adr_task` к
   несуществующей задаче: там существование не проверялось) переходит
   ссылкой вперёд с `resolved=0`, а не теряется. Число таких строк
   миграция печатает. `criterion:<story_id>#<pos>` резолвится в `uid`
   критерия. Старые таблицы
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
- **Риск: ссылки без FK.** Висячие ссылки возможны, если сущность удалить
  мимо сервиса. От этого страхуют гейты `test_entity_deleted_published`
  (§3.5) и `test_presentation_no_orm_writes.py` (presentation не пишет в
  ORM), а также рутина `relation_verify`.
- **Риск: гибкость без спроса.** Сейчас спрос виден на трёх связях: история
  ↔ история (триггер), ADR → RFC (RFC 30), идея → результат (RFC 31).
  Проектные типы — ставка на то, что их станет больше. Критерии в §8
  проверяют эту ставку до фазы C.
- **Риск: полиморфная таблица медленнее узкой.** `relations(ref)` — это
  union до шести источников и резолв `uid` → ключ. На масштабе Restate
  (289 историй, ~1100 задач) это сотни рёбер на проект. Индексы по
  `from_uid`/`to_uid` держат чтение одной сущности в пределах одного
  index lookup на источник. Горячий путь `plan_ready` таблицу `relation`
  не читает.

## 6. Открытые вопросы

| # | Вопрос | Предлагаемый ответ |
|---|---|---|
| R39-Q1 | Кто вправе заводить проектный тип: любой writer или через approval? | writer, но агентский `author` идёт через approval `relation_type` (как `doc_patch` у куратора); человек — напрямую |
| R39-Q2 | Чем адресовать конец ребра? | решено: `uid` строки (§3.1). Публичный ключ меняется при rename и `pin_provisional` |
| R39-Q3 | Удалять ли старые тулы после фазы C? | нет, пока `tools_diff` по журналу Гермеса показывает их вызовы |
| R39-Q4 | `relates_to` разрешён для любой пары — не станет ли он свалкой? | `relation_types` отдаёт число рёбер на тип; доля `relates_to` — метрика §8, а не рутина |

## 7. Оценка

| Секция | Задачи | Объём |
|---|---|---|
| 0. Сейчас | `Literal` в пяти link-тулах; ошибка со всеми невалидными параметрами и допустимыми значениями | 2 задачи |
| A. Ядро | `entity_ref` (ключ ↔ `uid`); словарь + гейты однозначности и потребителей; миграция `relation` + `relation_type` + `sync_registry`; `relate`/`unrelate`/`define_type` с контрактом ошибок; `EntityDeleted` во всех удаляющих путях + гейт; `test_no_new_link_tables` | 6 задач |
| B. Поверхности | MCP-тулы + профили + `READER_TOOLS`; CLI `rel` + completion; `relations()` с прозой и бэкендами; веб-фрагмент «Связи»; скилл | 5 задач |
| C. Поглощение | миграция данных с резолвом в `uid` + тест сохранности; обёртки старых тулов; `context_service` на `relations()` | 3 задачи |
| D. Бэкенды | диспетчер на `dependency`/`adr_*` | 1 задача |

Итого 17 задач, 3–4 недели. Фаза 0 не зависит от решения по RFC. A и B
дают историю ↔ история и проектные типы без поглощения. C и D начинаются
только после проверки по §8. План — `relation-graph-2026-12` (префикс
REL).

## 8. Критерии успеха и точка остановки

Проверка через четыре недели после выкатки фазы B на Гермес, по orakul и
Restate:

| Метрика | Порог продолжения | Источник |
|---|---|---|
| рёбра типов, которых не было до RFC (story ↔ story, проектные) | ≥ 30 на два проекта | `relation_types` со счётчиками |
| доля `relates_to` среди новых рёбер | < 50% | то же |
| неудачные `relate` без последующего успешного вызова того же агента | < 10% | `activity_event` + журнал ошибок MCP |
| новые таблицы связей в коде | 0 | `test_no_new_link_tables` |

Если первые два порога не взяты, проектные типы никому не нужны. Тогда
фазы C и D не делаются, `relation` остаётся таблицей для нескольких
встроенных типов, а RFC 30/31 всё равно получают свои типы вместо таблиц.
Это решение фиксируется в README строкой «остановлен после B» с цифрами.
