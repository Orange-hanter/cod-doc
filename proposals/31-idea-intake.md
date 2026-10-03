---
type: rfc
---

# 31 — Приём идей и точка обязательства

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: RFC 30 (жизненный цикл
> RFC — только для трека L), RFC 26 (правка графа плана), OQM (открытые
> вопросы — образец сущности), STL-004 (`story_task`)

## 1. Контекст

Исследование «Конвейер артефактов»
(`docs/system/research/2026-09-30-artifact-pipeline.md`) показало: у cod-doc
нет верхней зоны. Всё, что попадает в БД, сразу становится задачей `todo` в
каком-то плане, а мысль «можно бы сделать X» живёт в прозе чатов, аудитов и
ROADMAP. Kanban называет границу между «вариантами» и «обещанным» точкой
обязательства (commitment point); Shape Up — ставкой. Выше неё агенты
свободно предлагают и выбрасывают, ниже — всё обещано и измеряется.

Решения владельца (2026-09-30), на которые RFC опирается:

1. Идея — **своя сущность** с метаданными, а не вид открытого вопроса.
2. Невзятые идеи **не протухают автоматически** — их судьба на человеке.
3. Работы без RFC живут в **одном потоковом плане** на проект, секция на
   capability (исследование §7.3).
4. Треки по размеру S / M / L / expedite (исследование §4.3).

## 2. Текущее состояние (проверено по коду 2026-09-30)

| # | Факт | Где |
|---|---|---|
| F1 | Сущности «идея» нет; полей size/track/appetite/goal нет ни в одной модели | grep `cod_doc/infra/models/`, `domain/entities.py` |
| F2 | `task_service.create` не принимает статус и жёстко пишет `todo` | `services/task_service.py:371`, `:443` (ADO-156) |
| F3 | На живой БД `backlog` — 1 задача, `todo` — 144: зона вариантов не используется | `task` group by status |
| F4 | Машина состояний уже разрешает `backlog → todo` и `todo → backlog` | `services/task_status_machine.py:64-77` |
| F5 | Вьюха `ready_tasks` берёт только `todo`/`pending` — backlog не всплывает в готовых | миграция `20260927_0041_ready_tasks_blocked_reason.py` |
| F6 | Доска сливает backlog в колонку Todo | `api/web/pages/tasks.py:32-39` `_KANBAN_COLS` |
| F7 | `plan_ready(project, plan_scope, limit, include_body, local_only)` без фильтра секции; у `task_list` он есть | `mcp/tools/plan_tools.py:540`; `services/task_service.py:1080` `_resolve_scope_filters` |
| F8 | Единственная операция «артефакт A порождает B» — `promote_finding` (finding → bug-задача, событие `finding.promoted`) | `services/finding_service/promote.py:29` |
| F9 | Порог «RFC или задача» записан только прозой в скилле | `cod_doc/skills/rfc-authoring/SKILL.md:19-22` |
| F10 | Образец сущности с вариантами, ссылками и проверкой ссылок — `open_question`: таблицы `open_question`/`_option`/`_link`, ID `Q-NNN` (max+1 в проекте), пакет сервисов, 13 MCP-тулов, CLI-группа, веб-страница, parity-тест | `infra/models/questions.py:30-121`; `services/question_service/_internals.py:21-57`; `mcp/tools/question_tools.py`; `cli/question/`; `api/web/pages/questions.py`; `tests/services/test_question_mutation_surface_parity.py` |
| F11 | Очередь куратора собирается из карточек; новый вид пункта — это 5 мест: ранг, карточка, ключ в `card`, `ranked.extend`, счётчик | `services/curator_service.py:59-78, 441-465, 529-558` |
| F12 | Профили: `standard` 164, `full` 168; тулы вне `AGENT_TOOLS`/`MINIMAL_TOOLS` попадают в оба | `mcp/profiles.py:54-145`; `tests/test_server_profiles.py:114` |

## 3. Предложение

### 3.1. Сущность `idea`

По образцу `open_question` (F10) — та же раскладка пакета, аудита и
поверхностей.

```sql
CREATE TABLE idea (
  row_id        INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  idea_id       VARCHAR(16) NOT NULL,          -- I-NNN, max+1 в проекте
  title         TEXT NOT NULL,
  problem       TEXT NOT NULL,                 -- какая боль; не решение
  context       TEXT,
  size          VARCHAR(16),                   -- S | M | L | expedite; NULL = не оценена
  goal          TEXT,                          -- какую цель vision двигает (обязательна для L)
  capability    VARCHAR(255),                  -- doc_key capability; адрес секции потокового плана
  status        VARCHAR(16) NOT NULL DEFAULT 'new',
  source_kind   VARCHAR(16) NOT NULL DEFAULT 'human',  -- human | agent | finding | question | audit
  source_ref    VARCHAR(255),                  -- FND-…, Q-…, doc_key аудита
  outcome_kind  VARCHAR(16),                   -- task | story | rfc
  outcome_ref   VARCHAR(255),                  -- BPR-001 | US-031 | proposals/NN-slug
  decision_note TEXT,                          -- причина park/drop, комментарий promote
  author        VARCHAR(64) NOT NULL,
  created       DATETIME NOT NULL,
  last_updated  DATETIME NOT NULL,
  UNIQUE (project_id, idea_id),
  CHECK (status IN ('new','shaped','promoted','parked','dropped')),
  CHECK (size IS NULL OR size IN ('S','M','L','expedite'))
);
CREATE TABLE idea_link (…)  -- копия open_question_link: to_kind/to_ref/relation/note
```

Цикл:

| Статус | Смысл | Переход в него |
|---|---|---|
| `new` | записана | создание |
| `shaped` | есть problem, size, capability; для L — goal | `idea_update`, когда поля заполнены (проверка сервисом) |
| `promoted` | стала задачей / историей / RFC — терминальный | `idea_promote` |
| `parked` | отложена человеком, без срока | `idea_park` |
| `dropped` | отклонена с причиной — терминальный | `idea_drop` |

`parked → new` и `dropped → new` — через `idea_reopen`. Автоматического
протухания нет (решение владельца): куратор показывает идеи, но не закрывает
и не напоминает о возрасте.

Источники без новых тулов: `idea_create(source_kind="question",
source_ref="Q-021")` для вопроса, `source_kind="finding"` для находки —
вместо `question_to_idea` / `finding_to_idea`.

### 3.2. `idea_promote` — порождение по треку

```python
def promote(session, *, project_id, idea_id, to: Literal["task", "story", "rfc"],
            author, target_ref: str | None = None, commit: bool = False,
            reason: str | None = None) -> PromoteResult
```

| `to` | Что делает | Требования |
|---|---|---|
| `task` (S, expedite) | создаёт задачу в секции capability потокового плана (§3.3), `description` из problem+context, ребро `idea_link relation=promoted_to`; статус `backlog`, при `commit=True` — `todo`; expedite → `priority=critical` | `size ∈ {S, expedite}`, capability задана |
| `story` (M) | создаёт `user_story` status `draft` под story_section capability | `size=M` |
| `rfc` (L) | **не пишет RFC**: связывает идею с уже существующим документом `target_ref` (агент пишет RFC по скиллу `rfc-authoring`); после RFC 30 — проверяет `type=rfc` | `size=L`, goal задан, документ существует |

Несоответствие `size` и `to` — ошибка с подсказкой, а не молчаливое
создание. Одно событие `idea.promoted` + revision (ADO-040), как у
`finding.promoted` (F8).

### 3.3. Точка обязательства на задачах

- `task_create(..., status: Literal["backlog","todo"] = "todo")` — дефолт
  прежний, обратная совместимость (F2).
- Переход `backlog → todo` — «обязательство»: помимо `task.status_changed`
  пишется событие `task.committed` с `actor_kind`. Отсюда lead time от
  обязательства (RFC 33).
- Колонка Backlog на доске отдельно от Todo (F6).
- Жёсткого запрета «только человек обязует» в v1 **нет**: сначала замер,
  кто и как часто это делает (`task.committed` по `actor_kind`); решение —
  по данным (вопрос §7.1).

### 3.4. Потоковый план

- Plan scope `stream`, `principle="stream: работы без RFC"` — создаётся
  лениво первым `idea_promote(to="task")` или `cod-doc plan stream ensure`.
- Секция на capability: заголовок — title capability, буква — следующая
  свободная (до двух символов, F7-смежное `validation/_patterns.py:19`).
  Карта `capability doc_key → section` хранится в новом поле
  `plan_section.capability_doc_key` (nullable; миграция не трогает
  `document`).
- `plan_ready(..., section_letter: str | None = None)` — копия фильтра
  `task_list` (F7).

### 3.5. Куратор

Новый пункт `idea_new`: идеи в статусе `new` одной строкой на проект (как
Инбокс — один пункт на весь список), ранг ниже вопросов (`_RANK_QUESTION_STALE
+ 1`). Команда в пункте — `cod-doc idea list --status new`. Идеи `shaped`
в очередь куратора не идут: они ждут решения человека и попадают в очередь
внимания RFC 33.

### 3.6. Поверхности

| Операция | MCP | CLI |
|---|---|---|
| создать / карточка / список / правка | `idea_create` / `idea_get` / `idea_list` / `idea_update` | `cod-doc idea new / show / list / edit` |
| породить | `idea_promote` | `cod-doc idea promote I-007 --to task [--commit]` |
| отложить / отклонить / вернуть | `idea_park` / `idea_drop` / `idea_reopen` | `idea park / drop --why / reopen` |
| ссылки | `idea_link` (`detach=true`) | `idea link / unlink` |

9 тулов: `standard` 164 → 173, `full` 168 → 177; `plan_ready` меняет
сигнатуру без нового тула. Веб: `/p/{slug}/ideas` (список по статусам,
карточка с формой promote), колонка Backlog на доске. Скилл `rfc-authoring`:
порог из F9 ссылается на `idea.size`; новый скилл `idea-triage` — как
оценивать размер (порог RFC, SPIDR для нарезки M).

## 4. Миграция и обратная совместимость

- Миграция `0043_ideas`: таблицы `idea`, `idea_link`; колонка
  `plan_section.capability_doc_key`. `document` не трогается — ограничение
  про `batch_alter_table` не применяется. Номер согласовать с ветками
  RFC 28–30, если они вольются раньше.
- `task_create` без `status` ведёт себя как раньше; существующие 144 `todo`
  не переводятся в backlog — ретроспективно «обязательство» не
  восстановить.
- Бэкфилла идей нет: прошлые идеи живут в прозе; перенос — вручную через
  `idea_create` по мере надобности.
- Счётчики профилей в прозе — по `PROSE_COUNTERS`
  (`tests/test_profile_counts_prose.py`); zsh-дополнение регенерировать.

## 5. Риски и что не делаем

| Не делаем | Почему |
|---|---|
| Автопротухание и напоминания о старых идеях | решение владельца |
| Скоринг (RICE, ICE), голосование | один владелец; размер и цель достаточны для решения |
| Автоматическое написание RFC из идеи | RFC — работа агента по скиллу; сервис только связывает |
| Запрет агенту обязывать (`backlog → todo`) | сначала данные (§3.3) |
| Сущность «цель» (goal) над историями | пока текстовое поле; сущность — когда появятся повторы |
| Перевод текущих `todo` в backlog | обязательство задним числом не восстановить |

Риски: (1) двойной учёт — идея и задача живут параллельно; снимается
терминальностью `promoted`. (2) Агенты продолжат создавать задачи мимо идей —
это допустимо для S, где идея лишняя; метрика «задачи без идеи-источника» не
вводится намеренно.

## 6. Оценка

8 задач, ~1 неделя агентской работы:

1. Модель + миграция `0043_ideas` + домен (`EntityKind.IDEA`).
2. Сервис `idea_service` (crud, park/drop/reopen, links) + событие на каждую мутацию + parity-спека.
3. `idea_promote` для task/story/rfc + потоковый план (`plan_section.capability_doc_key`, ensure).
4. `task_create(status=…)` + событие `task.committed`.
5. `plan_ready(section_letter)`.
6. MCP-тулы и CLI-группа `idea`; счётчики профилей, zsh.
7. Веб: страница идей, колонка Backlog.
8. Куратор `idea_new` + скиллы `idea-triage`, правка `rfc-authoring`.

## 7. Открытые вопросы

1. Нужен ли запрет `backlog → todo` для агентов после замера `task.committed`?
2. `idea_promote(to="story")` — создавать и заготовку критериев в EARS (RFC 32)
   или оставлять пустой до RFC 32?
