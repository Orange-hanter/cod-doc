---
type: rfc
---

# 37 — Ассистент на странице: сессия с агентом, который собирает контекст, уточняет и разворачивает

> Категория: 🔵 Архитектура · Риск: средний · Зависимости: proposal 10
> (адаптеры LLM, `stream_chat`), proposal 12 (approvals), RFC 25 (роль
> куратора, `context_get`), RFC 28 (реестр `CURATOR_OPS`, бюджет и
> приватность LLM), RFC 29 §3.8 (роли reader/writer/admin)

## 1. Контекст

Запрос владельца (2026-10-25): на большинстве страниц — вопрос, документ,
задача, план, ADR, история — должна быть интерактивная сессия с агентом.
Человек задаёт вопрос по тому, что открыто; агент сам собирает недостающую
информацию из корпуса, переспрашивает то, чего в корпусе нет, и
разворачивает предмет: на странице вопроса — до полной карточки решения,
на странице документа — до недостающих разделов и деталей.

Сейчас каждая AI-кнопка веба — один запрос и один ответ без памяти:
модель видит то, что ей положил маршрут, не может дочитать соседний
документ и не может спросить. Встроенный оркестратор умеет многошаговый
цикл с тулами и `ask_human`, но он привязан к легаси-задачам YAML и к
тулам записи файлов и git, и в веб не выведен.

Порог RFC пройден по обоим условиям skill `rfc-authoring`: новые сущности
БД (`assist_session`, `assist_message`) и ≥ 5 задач.

## 2. Текущее состояние (проверено по коду 2026-10-25)

| Факт | Где | Следствие |
|---|---|---|
| Все вызовы LLM в `services/` — синхронный `openai.OpenAI(...)` на вызов, один `chat.completions.create`, без тулов | `services/ai_text.py:89,383`, `ai_generate.py:350-401`, `comment_service.py:268-300` | многошагового чтения в сервисах нет |
| `call_lite` выключает рассуждения, при 400 «Reasoning is mandatory» откатывается на `effort: minimal` | `ai_text.py:370-417` | лёгкий ярус уже работает на моделях Гермеса (PR #255) |
| Протокол `LLMAdapter.chat(messages, tools, …)`; `OpenAICompatAdapter` передаёт `tools` и `tool_choice="auto"` | `agent/adapters/base.py:118`, `openai_compat.py:58-80` | вызов тулов на уровне адаптера есть |
| Стриминг объявлен (`ChatChunk`, `supports_streaming`), реализован только в `MockAdapter` | `base.py:92,149`; `openai_compat.py:40`, `anthropic.py:48` — `streaming=False` | потокового ответа нет ни у одного боевого адаптера |
| Многошаговый цикл с тулами и `ask_human` — только `orchestrator._agent_loop` | `agent/orchestrator.py:466-625` | завязан на `core.project.Task` и тулы `write_file`/`git_commit`; для страниц не годится |
| Сущности «сессия / сообщение / диалог» нет; ближайшее — `agent_run` (`run_scope`) и `TraceCall` | `services/run_context.py:60`, `domain/entities.py:800` | историю разговора хранить негде |
| Веб — Jinja + HTMX; WebSocket `/ws/projects/{slug}` только сервер → клиент, шина в памяти, без повтора | `api/websocket.py:90`, `services/event_bus.py:1-20` | канал для шагов агента есть; SSE нет |
| AI-кнопки: `docs/suggest`, `docs/generate`, `docs/{key}/expand`, улучшение поля задачи, генерация историй и задач, `navigator/analyze`, применение комментариев — все одношаговые | `pages/docs.py:471,592,1321`, `fragments/tasks_fields.py:130`, `pages/stories.py:543,881`, `pages/navigator.py:104`, `pages/comments.py:168` | диалога нет нигде |
| На страницах вопроса, плана и ADR AI нет вовсе | `templates/web/project/question_show.html`, `pages/plans.py:60`, `pages/adr.py` | |
| `docs/{key}/expand` пишет секции сразу, с `author="ai:expand"` | `pages/docs.py:1369` | `actor_kind_for_author` относит этот префикс к `human`: правка модели в журнале выглядит как правка человека |
| `context_get` знает цели `document`/`task`/`plan`/`module`, глубина L0–L3, бюджет токенов | `services/context_service.py:836-862,935` | нет `question`, `adr`, `story` |
| `search_service.search` — FTS5/bm25 по task/doc/story/adr/finding/question | `search_service.py:691` | поиск для агента готов |
| `approval_service.request_doc_patch` + `resolve`: отпечаток, проверка устаревания по `base_revision_id`, исполнение в транзакции решения | `approval_service.py:291-349,410-510` | путь «модель предлагает, человек применяет» уже есть |
| `CURATOR_OPS` — три операции: `link_retarget`, `doc_set_node`, `question_link_retarget` | `curator_ops.py:188-192` | правку текста секции или вопроса через approval провести нельзя; RFC 28 §3.6 планирует `section_patch`, `section_add`, `question_create`, `question_link`, `task_create` |
| Вопрос: `context`, `options[]` (`position`, `title`, `body`, `chosen`), ссылки на document/section/task/adr/story/scenario/finding/code/url | `domain/entities.py:718-770` | у вопроса есть все поля, куда разворачивать |
| Комментарии — только к документам и секциям, без ревизий | `comment_service.py:5,44-55` | |
| `request_identity.check_write` требует writer+ и свой `author`; `READER_TOOLS` — allowlist чтения для reader | `services/request_identity.py:177-192`, `mcp/access_policy.py:23-99` | роли для сессии готовы |
| Ни в одном RFC 01–36 нет чата или ассистента в странице | `proposals/*.md` (grep «чат/chat/ассистент/диалог») | пересечения нет; RFC 10 §stream_chat реализован частично |

## 3. Предложение

### 3.1. Модель: сессия привязана к предмету

Сессия всегда открыта **на предмете** — вопросе, документе, секции,
задаче, плане, ADR, истории, находке — или на проекте целиком (страница
поиска). Предмет определяет **линзу**: какой контекст собрать до первого
вызова модели, какие пробелы проверить, какие правки агенту разрешено
предлагать.

Цикл одного хода:

1. **Сбор** — детерминированный пакет контекста линзы (§3.3) плюс
   карточка пробелов (§3.4). Модель ещё не вызвана.
2. **Чтение** — агент добирает недостающее read-тулами (§3.5), не больше
   `assist.max_steps` (по умолчанию 8) вызовов на ход.
3. **Уточнение** — если ответа нет в корпусе, агент задаёт вопросы
   человеку: не больше трёх за ход, у каждого — варианты быстрого ответа и
   «почему спрашиваю». Не может ждать — продолжает и выписывает
   допущения отдельным блоком.
4. **Разворот** — ответ в markdown со ссылками на прочитанное и пакет
   **предложений**: типизированные операции с diff (§3.6).
5. **Применение** — человек применяет предложения по одному или пакетом.
   Агент сам ничего не пишет.

Сессия не исполнитель. Код она не правит, задачи в работу не берёт,
статусы не двигает: это граница ADR-014 и RFC 27 (роль `doc-curator` на
профиле `agent`).

### 3.2. Данные

Миграция `0054_assist_session`:

```sql
CREATE TABLE assist_session (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  session_id    TEXT    NOT NULL,           -- AS-NNN, max+1 в проекте
  target_kind   TEXT    NOT NULL,           -- question|document|section|task|plan|adr|story|finding|project
  target_ref    TEXT    NOT NULL,           -- Q-021, doc_key, doc_key#anchor, ADO-070, …; '' для project
  lens          TEXT    NOT NULL,           -- имя линзы из реестра §3.3
  status        TEXT    NOT NULL,           -- open|closed|abandoned
  author        TEXT    NOT NULL,           -- кто открыл: human:dakh
  title         TEXT    NOT NULL,           -- первая реплика, обрезанная до 120 символов
  summary       TEXT,                       -- итог при закрытии (§3.7)
  tokens_in     INTEGER NOT NULL DEFAULT 0,
  tokens_out    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL,
  UNIQUE (project_id, session_id)
);

CREATE TABLE assist_message (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL REFERENCES assist_session(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,           -- append-счётчик, адрес реплики
  role          TEXT    NOT NULL,           -- user|agent|tool
  kind          TEXT    NOT NULL,           -- text|clarify|evidence|proposal|step|error
  body          TEXT    NOT NULL,           -- markdown реплики
  payload_json  TEXT,                       -- clarify: [{ask, why, options[]}]; evidence: [{kind, ref, tokens}];
                                            -- proposal: {approval_id}; step: {tool, args, result_tokens}
  model         TEXT,
  tokens_in     INTEGER, tokens_out INTEGER,
  created_at    TEXT    NOT NULL,
  UNIQUE (session_id, seq)
);
```

- `seq` — адрес, а не порядок отображения: append-счётчик, как позиция
  критерия истории (§«Порядок — ранг» в CLAUDE.md). Ранг не нужен —
  реплики не переставляют.
- Расход пишется и в `agent_run` через `run_scope(kind="assist")`: одна
  запись на ход, `llm_calls`/`tokens_in`/`tokens_out` как у
  `curator_sweep`.
- Сессия — не содержимое проекта: ревизий у реплик нет. Ревизии и
  activity-события пишут только применённые предложения — через
  сервисы-владельцы. Сама сессия эмитит `assist.opened` /
  `assist.closed` через `activity_service.emit_for_write` (ADO-040).
- В FTS индексируется только `summary` закрытых сессий (вид `assist`):
  итог разговора находится поиском, сырой транскрипт — нет.

### 3.3. Линзы

Реестр `services/assist_service/lenses.py`: `Lens(name, target_kind,
context, gaps, ops, prompt)`. Линза — данные плюс функция сборки
контекста, не отдельный агент.

| Линза | Страница | Пакет контекста до вызова модели | Что разворачивает | Разрешённые операции |
|---|---|---|---|---|
| `question` | `/p/{slug}/questions/{qid}` | вопрос, варианты, разрешённые ссылки (`question_verify` + `code_excerpt`), связанные ADR и задачи, FTS по `title + question` (top 8) | `context` (предыстория, ограничения с цитатами), недостающие варианты, плюсы/минусы и цена каждого, что неизвестно и у кого спросить, кандидаты в ADR | `question_update`, `question_option_add`, `question_option_update`, `question_link`, `adr_create(status=proposed)` |
| `document` | `/p/{slug}/docs/{key}` | `context_get(document, L1)` + `doc_node_health` раздела + `doc_type_guides` для типа документа + открытые вопросы и findings на документ | недостающие по типу документа разделы, тонкие разделы, утверждения без ссылок, противоречия с соседями | `section_patch`, `section_add`, `question_create` |
| `section` | секция документа (кнопка у заголовка) | секция + соседние секции + входящие и исходящие ссылки + `link_suggest_for_section` | детали секции, примеры, ссылки на код (`path#symbol`) | `section_patch`, `question_create` |
| `task` | `/p/{slug}/tasks/{id}` | задача, acceptance, `task_doc`, блокеры, `addresses`-вопросы, секция плана, `context_get(task, L2)` | acceptance-критерии, что не определено, зависимости | `task_update(description\|acceptance)`, `task_add_dependency`, `question_create` |
| `plan` | `/p/{slug}/plans/{id}` | секции, `plan_progress`, `plan_critical_path`, `plan_ready`, родительский RFC (`plan.parent_doc_id`, RFC 30) | дыры в декомпозиции, задачи без acceptance, рёбра, которых не хватает | `task_create`, `task_add_dependency`, `question_create` |
| `adr` | `/p/{slug}/adr/{id}` | ADR, `adr_graph` на глубину 1, связанные задачи и вопросы, секции со ссылками на ADR | альтернативы, последствия, критерии пересмотра | `adr_update` (поля `proposed`-ADR), `adr_relate`, `question_create` |
| `story` | `/p/{slug}/stories/{id}` | история, критерии, `story_coverage`, сценарии | критерии приёмки, сценарии | `story_add_criterion`, `scenario_create`, `question_create` |
| `finding` | `/p/{slug}/proposals`, карточка находки | находка, предмет находки, история `times_seen` | причина и вариант починки | `question_create`, `task_create` |
| `project` | `/p/{slug}/search` | MASTER L0, `curator_next` (top 5), FTS по реплике | ответ по корпусу со ссылками | `question_create` |

Каждая операция линзы обязана быть в реестре `CURATOR_OPS` (§3.6); линза,
ссылающаяся на незарегистрированную операцию, роняет тест реестра.

### 3.4. Карточка пробелов: «собрать всё необходимое» детерминированно

«Агент собирает всю нужную информацию» сводится к двум вещам, и первая
от модели не зависит: **список того, чего у предмета не хватает**,
считается правилами до вызова модели и передаётся ей как задание хода.
Модель отвечает за вторую — найти недостающее в корпусе или спросить.

Правила `gaps` на линзу (примеры, пороги калибруются замером на живом
корпусе, как у `doc_node_health`):

| Линза | Пробел |
|---|---|
| `question` | `context` пуст или короче 200 символов; меньше двух вариантов; вариант без `body`; нет ни одной ссылки `about`; нет `owner`; есть битые ссылки; открыт дольше 30 дней без правок; есть принятый ADR, на который ссылается FTS-кандидат, но не сам вопрос |
| `document` | раздел, обязательный для типа (`doc_type_guides`), отсутствует; секция короче порога; утверждение-число без ссылки; открытые findings на документ; `drift != clean` |
| `task` | нет acceptance; acceptance без проверяемого критерия; `addresses`-вопрос закрыт, а задача открыта |

Карточка показывается человеку над полем ввода до первой реплики
(«Не хватает: варианта B, владельца, ссылки на код») — кнопка «Разобрать»
открывает сессию с репликой по умолчанию «закрой эти пробелы». Пустая
карточка — нормальное состояние: сессия всё равно доступна для вопроса.

### 3.5. Цикл чтения

Сервис `assist_service.turn(session, project_id, assist_session_id,
user_text, *, cfg) -> TurnResult` исполняет ход:

```python
@dataclass(frozen=True)
class TurnResult:
    messages: list[AssistMessage]      # step…, затем clarify | text, затем proposal…
    proposals: list[str]               # approval_id
    tokens_in: int
    tokens_out: int
    stopped: Literal["answered", "clarify", "max_steps", "budget", "error"]
```

- Тулы чтения вызываются **в процессе**, напрямую в сервисы, не через
  MCP: `context_get`, `ctx_search`, `doc_section_get`, `question_get`,
  `adr_get`, `task_get`, `story_get`, `structure_context`,
  `code_excerpt`. Набор — подмножество `READER_TOOLS`; тест сверяет, что
  каждый тул сессии там есть (иначе reader через сессию увидел бы то, что
  ему не выдано через MCP).
- Два служебных тула не читают корпус: `ask_user(items[])` завершает ход
  со `stopped="clarify"`; `propose(op, args, rationale)` кладёт
  предложение (§3.6) и продолжает ход.
- Модель — `cfg.model` для ответа, `lite_model` для ходов, где агент
  только отбирает, что читать (тот же двухъярусный подход, что RFC 28
  §3.9). Вызов — `LLMAdapter.chat(messages, tools)`; модель без вызова
  тулов (проверка по `model_catalog`) получает JSON-протокол
  `{"action": "read"|"ask"|"propose"|"answer", …}` с валидацией по схеме,
  как `curator_proposers._choose`.
- История в промпт идёт не целиком: последние `assist.history_turns` (6)
  реплик плюс пакет линзы плюс выжимка прочитанного; сырые результаты
  тулов прошлых ходов заменяются строкой `evidence` (ссылка + размер).
- Каждый вызов тула — реплика `role=tool, kind=step` и событие шины
  `assist.step` (§3.8).

Легаси `orchestrator._agent_loop` не переиспользуется: он несёт тулы
записи и модель задач YAML. Общий — только адаптер.

### 3.6. Предложения: approval, а не прямая запись

Агент пишет только предложения. Предложение — approval `doc_patch` с
`requested_by="agent:assist"` и `payload.session_id`, той же формы, что у
куратора (RFC 28 §3.6): `op`, `args`, `diff`, `rationale`,
`base_revision_id`, отпечаток. Отсюда бесплатно:

- проверка устаревания: человек поправил вопрос, пока агент думал, —
  предложение уходит в `expired(stale_base)`, а не затирает правку;
- дедупликация по отпечатку и подавление отклонённого на 30 дней;
- аудит: решение пишет `resolved_by=human:<кто>`, правка — ревизию и
  событие от сервиса-владельца с `author="agent:assist"`.

Чего не хватает — операций. Секция расширяет `CURATOR_OPS` теми, что RFC
28 §3.6 уже назвал (`section_patch`, `section_add`, `question_create`,
`question_link`, `task_create`), и добавляет `question_update`,
`question_option_add`, `question_option_update`,
`task_update`, `story_add_criterion`, `adr_update`, `adr_relate`. Каждая
— `CuratorOp(head, apply)` поверх существующей сервисной функции; новой
логики записи нет. Отдельной операции «добавить ссылку» нет и не
будет: ссылки документа выводятся парсером из тела секции, поэтому
предложить ссылку — это `section_patch`. Какой RFC добавит операцию первым — тот и добавляет,
второй переиспользует.

В вебе предложение рендерится в ленте сессии карточкой с diff и кнопками
«Применить» / «Отклонить» / «Править» (открывает diff в форме). Применить
— `approval_service.resolve(decision="approve", resolved_by=<человек>)`.
Те же предложения видны на странице «Предложения» с фильтром по
источнику. Потолок `assist.max_pending` (по умолчанию 10) на сессию: на
потолке агент отвечает текстом и не создаёт новых.

Применять может writer+; reader видит предложения и ответы, но кнопки
применения у него нет (`request_identity.check_write`).

### 3.7. Итог сессии

Закрытие сессии (кнопка или 24 ч без реплик) просит у `lite_model`
`summary` — три–пять строк: что спросили, что нашли, что применили,
что осталось открытым. Итог:

- пишется в `assist_session.summary` и в FTS;
- показывается на странице предмета блоком «Сессии» (последние 5);
- для линзы `question` — если человек отметил «добавить в контекст»,
  уходит предложением `question_update(context += …)`, а не тихо.

Неприменённые предложения закрытой сессии остаются `pending` до TTL
approval (48 ч) и истекают штатно.

### 3.8. Транспорт и веб

V1 — без потокового вывода токенов: у боевых адаптеров `streaming=False`.

- `POST /p/{slug}/assist` — открыть сессию (`target_kind`, `target_ref`),
  ответ — HTMX-фрагмент панели.
- `POST /p/{slug}/assist/{sid}/turn` — реплика; ход исполняется фоновой
  задачей, ответ сразу — фрагмент «агент думает» с id хода.
- Шаги приходят по существующему `/ws/projects/{slug}` событиями
  `assist.step` / `assist.turn_done` (`{session_id, seq, kind, summary}`);
  на `assist.turn_done` панель запрашивает `GET …/assist/{sid}/messages?after=<seq>`.
  Без WebSocket (шина в памяти, переподключение теряет события) панель
  опрашивает тот же GET раз в 2 с — источник истины всегда БД, не шина.
- Панель — правая колонка на страницах из §3.3, свёрнута по умолчанию,
  над полем ввода — карточка пробелов. Уточняющие вопросы — кнопки
  быстрых ответов плюс поле «свой ответ». Блок «Прочитано» сворачивает
  `evidence` со ссылками на источники.
- Прогрессивное улучшение (`web-frontend.md` §non-goals «не SPA»): без JS
  форма реплики работает обычным POST с редиректом на страницу сессии
  `/p/{slug}/assist/{sid}`.

Потоковый вывод (`stream_chat` в `OpenAICompatAdapter` + SSE) — V2,
отдельной задачей после замера: ход из 3–5 вызовов тулов на моделях
Гермеса и есть основная задержка, токены ответа — меньшая её часть.

### 3.9. Поверхности

Правило четырёх поверхностей — сервис `assist_service` выставлен и в CLI,
и в MCP; паритет стережёт `_surface_parity` (новый
`test_assist_mutation_surface_parity.py`).

| Действие | CLI | MCP | Веб |
|---|---|---|---|
| Открыть | `cod-doc ask <kind>:<ref> "реплика" -p <slug>` | `assist_open(project, target_kind, target_ref, text)` | панель на странице |
| Реплика | `cod-doc ask --session AS-012 "…"`; без реплики — интерактивный режим (REPL, `ask_user` — prompt с вариантами) | `assist_turn(project, session_id, text)` | поле ввода |
| Карточка пробелов | `cod-doc ask gaps <kind>:<ref>` | `assist_gaps(project, target_kind, target_ref)` | над полем |
| История | `cod-doc ask show AS-012` / `list [--target …]` | `assist_get` / `assist_list` | блок «Сессии» |
| Закрыть | `cod-doc ask close AS-012` | `assist_close` | кнопка |
| Применить предложение | `cod-doc approval approve <id>` (есть) | `approval_resolve` (есть) | кнопка в ленте |

MCP-тулы — на профилях `standard`/`full`; на `agent` — только
`assist_gaps` (детерминированный, без LLM-расхода): внешнему агенту
полезна карточка пробелов, а вложенный LLM-вызов внутри его собственного
цикла — двойная оплата. `assist_gaps`, `assist_get`, `assist_list` — в
`READER_TOOLS`; `assist_open`/`assist_turn` — нет (расходуют бюджет, §3.10).

### 3.10. Бюджет, приватность, доступ

- **Бюджет** — `assist.budget.tokens_per_turn` (по умолчанию 60k) и
  `assist.budget.tokens_per_day` на проект (400k), учёт — `agent_run` с
  `kind="assist"`. Отдельно от бюджета куратора RFC 28: ручная сессия не
  должна съедать ночной обход и наоборот. Исчерпан — ход завершается
  `stopped="budget"`, карточка пробелов продолжает работать.
- **Приватность** — тот же `.cod-doc/llm-ignore` и маскировка секретов,
  что RFC 28 §3.9; `code_excerpt` для файла из списка отдаёт отказ, а не
  фрагмент. Проект с `assist.llm: off` показывает только карточку пробелов.
- **Доступ** — открыть сессию и сделать ход может writer+ (ход стоит
  денег владельца узла); reader — только читать сессии и карточку. Сессия
  видна всем участникам проекта: транскрипт — рабочий материал проекта,
  а не личный чат.
- **Автор** — `agent:assist` для предложений и `assist.*`-событий,
  `actor_kind_for_author` выводит `agent` без правок. Под токеном RFC 29
  автор применения — владелец токена, как сейчас.

### 3.11. Попутная починка: `ai:expand` пишется как человек

`pages/docs.py:1369` пишет секции от `author="ai:expand"`, а
`actor_kind_for_author` относит неизвестный префикс к `human`. Это
отдельная задача, не ждущая RFC: автор → `agent:expand`, плюс
регрессионный тест на `actor_kind` записи. Кнопка «Развернуть документ»
после фазы C становится входом в сессию линзы `document` с предложениями
вместо прямой записи.

## 4. Фазы

| Фаза | Содержание | Выход |
|---|---|---|
| A. Ядро | миграция 0054; `assist_service` (`open`, `turn`, `close`, `get`, `list`, `gaps`); реестр линз с `question` и `document`; `context_get` для целей `question`/`adr`/`story`; цикл чтения с JSON-фолбэком; бюджет и приватность; CLI `cod-doc ask`; MCP `assist_*` | сессия по вопросу из терминала, предложений ещё нет |
| B. Предложения | операции §3.6 в `CURATOR_OPS`; `propose` → `request_doc_patch`; применение из CLI | правка вопроса и секции через approval |
| C. Веб | панель на страницах вопроса и документа, карточка пробелов, WS-шаги с опросом, карточки предложений, блок «Сессии»; `docs/{key}/expand` → сессия | сценарий владельца целиком в браузере |
| D. Остальные линзы | `section`, `task`, `plan`, `adr`, `story`, `finding`, `project` | панель на всех страницах §3.3 |
| E. Поток (V2) | `stream_chat` в `OpenAICompatAdapter`, SSE-маршрут хода | только после замера задержек фаз A–C |

## 5. Миграция / обратная совместимость

- Две новые таблицы, существующие не трогаются; `batch_alter_table` не
  нужен (правило про `document` в CLAUDE.md не задето).
- RFC 29: таблицы `local` — не реплицируются. Сессии живут на узле, где их
  открыли (на Гермесе — общий узел, видят все участники); реплицируются
  только последствия — правки через сервисы-владельцы. Причина: журнал
  синхронизации для реплик-ноутбуков не должен везти транскрипты.
- Существующие одношаговые AI-кнопки остаются до фазы C; `expand` меняет
  поведение (предложения вместо записи) — заметка в release notes.
- `agent_report` не меняется: он task-scoped и про coding-агента.

## 6. Риски и что не делаем

**Риски.**

- *Модель Гермеса не вызывает тулы надёжно* (`glm-5.3-flash`,
  `minimax-m3.1-flash`, обязательные рассуждения). Смягчение —
  JSON-протокол с валидацией по схеме и потолок шагов; замер на 20
  вопросах живого корпуса до фазы C.
- *Агент выдумывает факты в развороте.* Требование промпта: каждое
  утверждение о корпусе — со ссылкой на `evidence` этого хода; ссылка на
  непрочитанный источник вырезается при рендере и помечается.
- *Шум предложений.* Потолок на сессию, отпечатки, подавление
  отклонённого — механика approval, а не новая.
- *Стоимость.* Ход — до `max_steps + 1` вызовов; суточный лимит и
  writer-only на ходы.

**Не делаем.**

- Агент не пишет в БД мимо approval и не правит код — ни в одной линзе.
- Не чат-бот без предмета: даже `project` — линза с пакетом контекста и
  единственной операцией `question_create`.
- Не берёт задачи в работу, не двигает статусы, не зовёт
  `task_checkout`/`task_complete` (граница ADR-014).
- Нет памяти между сессиями помимо `summary` и применённых правок; нет
  персонализации под пользователя.
- Нет совместной сессии в реальном времени (двое печатают в одну ленту) —
  сессия последовательна, второй участник видит её после хода.
- Нет потокового вывода в V1 и нет SSE до фазы E.
- Не заменяет куратора RFC 28: тот работает в фоне по событиям, сессия —
  по запросу человека на одном предмете. Общие у них только операции и
  approval.
- Не переиспользует и не чинит легаси-оркестратор.

## 7. Открытые вопросы

| # | Вопрос | Варианты |
|---|---|---|
| Q1 | Сколько жить транскрипту | бессрочно / TTL 90 дней с сохранением `summary` / удалять при закрытии |
| Q2 | Ход для reader | запрещён (как в §3.10) / разрешён в пределах отдельной суточной квоты на участника |
| Q3 | Где панель на узких экранах | нижний лист / отдельная страница сессии |
| Q4 | Выносить ли линзы в скиллы `cod_doc/skills/` | промпт линзы в коде / промпт линзы — SKILL.md, правка поведения без релиза (CLAUDE.md: «новое поведение агента → скилл») |

## 8. Оценка

| Фаза | Задач | Недель |
|---|---|---|
| A | 6 | 1 |
| B | 3 | 0,5 |
| C | 4 | 1 |
| D | 4 | 1 |
| E | 2 | 0,5 |
| Итого | 19 | ~4 (V1 = A–D, ~3,5) |

План — `assist-2026-11`, префикс задач `ASI`, по секции на фазу; после
принятия — skill `plan-to-tasks`. Плюс одна задача вне плана — §3.11.
