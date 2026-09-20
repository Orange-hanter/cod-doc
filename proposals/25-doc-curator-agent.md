# 25 — Агент-куратор документации: доступность и поиск, не исполнение задач

> Категория: 🔵 Архитектура · Риск: средний · Зависимости: proposal 01 (skills), proposal 07 (routines), ADR-004 (Snowball), RFC 22 (`ctx_*`); частично реанимирует полезный зазор RFC 19 (Context-Scout), не возвращая отбракованный vibecoder-скоуп

## 1. Контекст

Cycle-5 (AGT-001…007) сделал дефолтный MCP-профиль `agent` **task-centric**:
`agent_pick` атомарно захватывает следующую готовую задачу, отдаёт task card,
агент пишет код, `agent_complete` закрывает узел. Скилл `orchestrator`
прямо говорит: «ты — автономный агент управления документацией», а алгоритм
ниже — `pick → work → complete`. На практике «work» = любая ADO-/SYM-задача
из очереди, включая баги UI, CLI, ZAIrgRush-метрики.

Это расходится с тем, зачем COD-DOC существует.

VISION §2: ядро — БД документов + markdown-проекции + «агент на LLM, который
использует MCP **для развития документации**». Capability
`context-retrieval` и ADR-004 (Snowball) — про минимальный достаточный
контекст, не про «возьми следующую feature-задачу». Routines
(`doc_drift_daily`, `link_integrity_daily`) уже закрывают санитарный контур
без LLM. CLI `cod-doc ctx docs|drift|search` (RFC 22 / SYM-008) уже отдаёт
контекст внешним потребителям.

Боль, которую фиксирует владелец (2026-09-15): **агент не должен делать
задачи продукта**. Его направление — поддерживать документацию и
оптимизировать её доступность и поиск. Исполнение кода, checkout feature-багов,
закрытие ADO-143…157 — работа человека или отдельного coding-агента на
профиле `standard`/`full`, не роль дефолтного оркестратора.

RFC 19 (Context-Scout) описывал близкий пробел — единая ranked-выдача
docs/ADR/stories/code — и был отбракован 2026-08-29, потому что не закрывал
friction M2. Здесь другой критерий: **роль агента**, не «ещё одна фича для
vibecoder'а». Полезный контракт RFC 19 (агрегатор поверх уже существующего
`search_service.search`) берём; LLM-rerank и «answer paragraph» — нет.

## 2. Текущее состояние (проверено по коду 2026-09-15)

| # | Факт | Где |
|---|---|---|
| T1 | Дефолтный MCP-профиль `agent` — 6 task-тулов: `agent_capabilities`, `agent_pick`, `agent_get`, `agent_report`, `agent_complete`, `agent_release` | `cod_doc/mcp/profiles.py:33-47` |
| T2 | `agent_capabilities().next_action_hint` всегда зовёт `agent_pick` | `cod_doc/mcp/tools/agent_tools.py:117-124` |
| T3 | `agent_pick` = ready-set → checkout → task card со скиллами | `cod_doc/services/agent_service.py:1-6, 36-80` |
| T4 | Скилл `orchestrator` учит цикл pick → complete и называет CRUD «избыточным для agent flow» | `cod_doc/skills/orchestrator/SKILL.md` |
| T5 | Встроенный `Orchestrator` генерирует задачи из MASTER.md и выполняет их LLM-петлёй | `cod_doc/agent/orchestrator.py:1-11, 57-58`; HANDBOOK §9 |
| T6 | `search_service.search` уже группирует хиты `task`/`doc`/`story`/`adr` (FTS5 bm25) | `cod_doc/services/search_service.py:235-292` |
| T7 | CLI `cod-doc ctx search` зовёт тот же сервис; **MCP-тула `ctx_search` нет** | `cod_doc/cli/cmd_ctx.py:435-451`; `mcp/tools/doc_tools.py` — только `ctx_docs` / `ctx_drift` / `ctx_drift_gate` |
| T8 | `ctx_docs` / `ctx_drift` есть в `standard`/`full` и **запрещены** в `agent` | `tests/test_server_profiles.py:133-154` (`SYM_006D_TOOLS`) |
| T9 | Snowball `context_get` (L0/L1) живёт в `standard`, не в `agent` | `cod_doc/mcp/tools/context_tools.py`; `MINIMAL_TOOLS` содержит `context_get`, `AGENT_TOOLS` — нет |
| T10 | Routines уже пишут санитарные задачи (`ADO-081` drift, `ADO-082` links) без LLM | `routine` rows `doc_drift_daily` / `link_integrity_daily` |
| T11 | Счётчики 6/21/121/125 зафиксированы в пяти местах + тест | `profiles.py` docstring, `server.py --profile`, `AGENTS.md` §5.9, `CLAUDE.md`, `docs/mcp-integration.md` |

> **Закрыто 2026-09-19 (CUR-009).** T1, T2, T8 описывали состояние **до**
> свопа и больше не актуальны: T1/T2 сняты CUR-008 (`AGENT_TOOLS` — набор
> куратора, `next_action_hint` зовёт `ctx_drift` → `ctx_search`, не
> `agent_pick`); T8 инвертирован CUR-008 (`ctx_docs`/`ctx_drift`/`ctx_search`
> теперь **в** `agent`, а не запрещены в нём — запрещён остаётся только
> `ctx_drift_gate`). T3–T7, T9–T13 остаются верными как исторический снимок
> кода на 2026-09-15. Актуальные счётчики (T11) — 6/21/129/133.

> **Закрыто 2026-09-20 (CUR-016/018, секция D).** `curator_next` заменил
> `ctx_docs` в `AGENT_TOOLS` (профиль `agent` остаётся 6 тулов); `standard`
> 129 → 130, `full` 133 → 134. Код `agent_capabilities().next_action_hint`
> (`cod_doc/mcp/tools/agent_tools.py`) всё ещё зовёт `ctx_drift` →
> `ctx_search`, а не `curator_next` — CUR-016 не тронул эту строку; см.
> finding в `docs/system/audit/2026-09-20-doc-curator-section-d.md`.
| T12 | Живая очередь `todo` — Stories-UI / MCP crash / CLI checkout, не документация | `ADO-140`, `ADO-143`…`ADO-146`, `ADO-157` |
| T13 | Read-only сабагент `cod-doc-scout` отвечает на вопросы из БД, ничего не меняя; это ближе к целевой роли, чем `agent_pick` | описан вне репозитория, в машинно-локальном конфиге агентов |

Вывод: поисковый и санитарный контур **уже есть** (T6–T10). Дефолтный агент
к нему не подключён и принудительно становится исполнителем задач (T1–T4).

## 3. Предложение

### 3.1. Политика роли (обязательна сразу, до смены тулов)

Дефолтный ИИ-агент COD-DOC — **куратор документации**:

- держит корпус в синхроне (import, hashes, MASTER, STALE/BROKEN);
- чинит доступность (битые ссылки, дрейф проекций, неклассифицированный import);
- оптимизирует поиск и выдачу контекста (FTS-индекс, Snowball-пакеты,
  token budget, кросс-проектный поиск как продолжение SYM-011);
- отвечает на «где у нас X» ranked evidence, не прозой без ссылок.

Дефолтный агент **не**:

- не делает `task_checkout` / `agent_pick` по feature/bug/refactor
  продукта;
- не пишет прикладной код «потому что в задаче написано Implement»;
- не закрывает ADO-/SYM-/STO- узлы, чей `type` не `docs` и чей
  `affects_files` не документация/индекс/скиллы.

Исполнение продукта остаётся на профилях `standard`/`full` (человек,
coding-агент в IDE). Задачи, планы, checkout **не удаляются**.

### 3.2. Новый состав `AGENT_TOOLS` (count остаётся 6)

> **Реализовано: CUR-007 (`ctx_search` с lazy reindex пустого FTS-индекса),
> CUR-008 (своп `AGENT_TOOLS` на набор ниже).** Живой allowlist —
> `cod_doc/mcp/profiles.py::AGENT_TOOLS`; проверка —
> `tests/test_server_profiles.py::test_sym006d_agent_profile_excludes_new_tools`.

```python
AGENT_TOOLS: frozenset[str] = frozenset(
    {
        "agent_capabilities",  # role=doc-curator; next_action_hint → ctx_search / ctx_drift
        "ctx_search",          # NEW MCP: тонкая обёртка search_service.search
        "ctx_docs",
        "ctx_drift",
        "context_get",
        "agent_report",        # approval_request, если политика доков требует человека
    }
)
```

`agent_pick` / `agent_get` / `agent_complete` / `agent_release` остаются
зарегистрированными и видны в `standard`/`full`. Тест
`test_sym006d_agent_profile_excludes_new_tools` инвертируется: `ctx_docs`
и `ctx_drift` **должны** входить в `agent`.

Сигнатура нового тула:

```
ctx_search(project: str, query: str, scope: str | None = None, limit: int = 20) -> dict
# shape = search_service.search: {query, total, by_kind: {doc, adr, story, task: [{ref, title, snippet, score}]}}
```

`task` в выдаче — **индекс** (что уже задокументировано/запланировано), не
приглашение взять задачу в работу.

`agent_capabilities` добавляет поля (обратно совместимый overlay):

```
"role": "doc-curator",
"forbidden": ["agent_pick", "task_checkout", "task_complete"],
"next_action_hint": "Call ctx_drift(project=...) then ctx_search(project=..., query=...) — do not pick implementation tasks."
```

Канонический цикл куратора:

```
1. agent_capabilities()
2. ctx_drift(project)            # санитарный срез
3. ctx_search / context_get      # доступность
4. починить док (import, hashes, body, links)
5. agent_report(kind='approval_request') — только если нужна политика человека
```

### 3.3. Скилл `orchestrator`

Переписывается под §3.1. `agent_pick(` в прозе скилла либо исчезает, либо
помечен как запрещённый на профиле `agent`. Проверки модуля
`tests/test_orchestrator_skill_refs.py`
(`test_orchestrator_skill_tool_refs_resolve` и
`test_orchestrator_skill_actually_references_some_mcp_tools`) обязаны
резолвить только живые тулы; `ctx_search(` появляется **после**
регистрации MCP-тула (порядок: тул → скилл, либо скилл без backtick-вызова
до CUR-B).

Новый скилл `doc-curator` не плодим, пока не исчерпан `orchestrator` +
существующие `drift-handling` / `doc-style` / `ground-truth-reconcile`.

### 3.4. Встроенный daemon `cod-doc agent run` — **реализовано (CUR-017)**

MVP не переписывает `Orchestrator`. Политика: команда не является путём
исполнения продуктовых задач. Follow-up сделан секцией D: `_generate_tasks_from_master`
удалён (CUR-017) — `run_autonomous()` на пустой очереди отдаёт idle-событие
без побочных эффектов вместо автогенерации задач из MASTER.md; тик рутин
(`tick_project_routines`) остаётся отдельным вызовом до `run_autonomous`, как
и раньше. `docs/HANDBOOK.md` §9 получил legacy-баннер (CUR-018): контур
куратора — MCP профиль `agent` + routines, не daemon.

### 3.5. `curator_next` — **реализовано (CUR-016)**

Аналог task card для санитарии: один вызов собирает top drift + broken
links + stale MASTER hashes в «doc card» с применимыми скиллами. Не входил
в MVP 6-тул свопа; сделан секцией D после стабилизации `ctx_search`.

Как получилось:

```
curator_next(project: str, limit: int = 10) -> dict
# {"card": {"drift": {...ctx_drift-shape...}, "links": [...],
#           "master": [...], "findings": [...]},
#  "priority": [{"kind": "drift|link|master|finding", "ref", "reason",
#                "suggested_action"}],
#  "navigation": {"applicable_skills" (с телами), "next_actions",
#                 "success_criteria"},
#  "meta": {"generated_at", "truncated", "counts"}}
```

- Тело — `cod_doc/services/curator_service.py::next` (read-only,
  `transactional(..., commit=False)`); обёртка —
  `cod_doc/mcp/tools/curator_tools.py`; зеркало в CLI — `cod-doc ctx next`.
- Четыре источника: `projection_service.detect_project_drift`,
  `drift_gate_service.link_findings`, `core.hash_calc.check_stale_refs`
  (обе — публичные точки входа, вынесенные из приватных функций этой же
  задачей), `finding_service.list_findings(status="open")`.
- Порядок очереди фиксирован: `missing` → `edited_in_place` →
  `LINK-BROKEN` → hash `BROKEN` → hash `STALE` → `stale_export` →
  finding. Сначала то, что делает документ недоступным, потом то, что
  делает его неточным.
- Скиллы в карточке — `orchestrator`, `drift-handling`,
  `ground-truth-reconcile`, `doc-style` (cap 4, тела инлайном).
- Место в профиле `agent` уступил `ctx_docs`: голый листинг документов —
  строго более слабый ответ на «что мне делать», а тот же срез корпуса
  лежит внутри drift-половины карточки. Count остался 6; `standard`
  129 → 130, `full` 133 → 134.

## 4. Миграция / обратная совместимость

- Клиенты с `--profile standard` (этот репозиторий, `.mcp.json`) **не
  ломаются**: pick/complete остаются.
- Клиенты на дефолтном `agent` теряют `agent_pick` в одном релизе — это
  цель RFC, не регресс. Миграция в `docs/mcp-integration.md`: coding-агент
  → `standard`; куратор → `agent`.
- Счётчики профилей: `agent` остаётся 6; `standard` +1 (`ctx_search`) →
  122; `full` +1 → 126; `minimal` без изменений, пока не решим тащить
  `ctx_search` в cold-start. Все пять мест из T11 правятся одним коммитом
  со свопом `AGENT_TOOLS`.
- Живые `todo` ADO-140…157 **не отменяются** этим RFC. Их забирает человек
  или coding-агент на `standard`, не дефолтный оркестратор.
- RFC 22 / SYM-011 (кросс-проектный поиск) остаётся, но становится
  частью направления куратора, а не «ещё одна agent_pick фича».
- RFC 23 (cloud workers) не стартуем под старой ролью «воркер закрывает
  задачи»; если когда-нибудь поднимем — воркер = куратор, не executor.

## 5. Риски и что не делаем

**Риски**

- Слом существующих интеграций на `agent_pick`. Митигация: тулы не
  удаляются, только уходят из дефолтного allowlist; changelog + hint в
  `agent_capabilities`.
- Тесты профиля и SYM_006D жёстко ждут, что `ctx_*` нет в `agent`. Это
  ожидаемый перекрас, не сюрприз.
- Искушение «куратор сам починит код, в доке же ошибка». Запрет в скилле
  и в `forbidden` capabilities; code-fix только если файл в
  `docs/**`, `cod_doc/skills/**`, `proposals/**`, `MASTER.md`, либо это
  индекс/поиск (`search_service`, `ctx_*`).

**Non-goals**

- Удалить task/plan/checkout/activity.
- LLM-rerank и синтез абзаца-ответа (отбракованная часть RFC 19).
- Переписать ZAIrgRush executor, чтобы он перестал писать код.
- Cloud agent plane (RFC 23).
- Автономный daemon как единственный runtime куратора.
- Новая таблица БД под «doc jobs» в MVP — достаточно drift findings +
  routines.

## 6. Оценка — **план закрыт 2026-09-20**

Итог вместо прогноза: **18 задач** `doc-curator-2026-09` (CUR-001…018),
все `done`. Секция A (CUR-001…003) — политика, вне таблицы ниже. Секции
B/C/D — 12 задач CUR-007…018:

| Задача | Секция | Содержание | PR |
|---|---|---|---|
| CUR-007 | B | `ctx_search` MCP-тул, lazy reindex пустого FTS-индекса | [#50](https://github.com/Orange-hanter/cod-doc/pull/50) |
| CUR-008 | B | Своп `AGENT_TOOLS` на curator-набор (RFC 25 §3.2) | [#62](https://github.com/Orange-hanter/cod-doc/pull/62) |
| CUR-009 | B | Документация/скилл догоняют CUR-007/008, аудит секции B | [#66](https://github.com/Orange-hanter/cod-doc/pull/66) |
| CUR-010 | C | Понятная ошибка вместо трейса при отсутствии FTS-таблицы | [#71](https://github.com/Orange-hanter/cod-doc/pull/71) |
| CUR-011 | C | per-kind limit, вес заголовка в bm25, `--scope`/`--limit` у `ctx search` | [#65](https://github.com/Orange-hanter/cod-doc/pull/65) |
| CUR-012 | C | Инкрементальный FTS-upsert из write-path task/story/adr/finding | [#73](https://github.com/Orange-hanter/cod-doc/pull/73) |
| CUR-013 | C | Кросс-проектный поиск `project_ids` в hub-БД (SYM-011) | [#72](https://github.com/Orange-hanter/cod-doc/pull/72) |
| CUR-014 | C | `context_get` учитывает `description` в L2 token budget | [#61](https://github.com/Orange-hanter/cod-doc/pull/61) |
| CUR-015 | C | Аудит закрытия секции C | [#75](https://github.com/Orange-hanter/cod-doc/pull/75) |
| CUR-016 | D | `curator_next` — doc card куратора, MCP + CLI, заменил `ctx_docs` в профиле `agent` | [#74](https://github.com/Orange-hanter/cod-doc/pull/74) |
| CUR-017 | D | Daemon без задач переходит в idle, автогенерация из MASTER.md удалена | [#49](https://github.com/Orange-hanter/cod-doc/pull/49) |
| CUR-018 | D | `docs/HANDBOOK.md` §9 legacy, скилл `orchestrator` на `curator_next`, закрытие плана | [#77](https://github.com/Orange-hanter/cod-doc/pull/77) |

> **Секция C закрыта 2026-09-20 (CUR-010…014).** Кросс-проектный поиск
> куратора (последний пункт секции C) реализован частично: MCP
> `ctx_search(projects=[...])` и CLI `search --projects`/`ctx search
> --projects` работают через `search_service.search(project_ids=...)` +
> `resolve_cross_project_ids` (hub-режим, общий `db_url`). REST `GET
> /api/v1/search` в кросс-проектность не заведён — `SearchHit`
> (`cod_doc/api/v1/schemas.py`) не несёт `project`, эндпоинт не принимает
> `projects`. Остаток — `[[doc:slug:key]]`, Chroma-фильтр B12,
> `GET /api/v1/search` с `projects` — задача **STO-015** (план
> `adoption-2026-08`, не этот план); `agent_pick --projects` сознательно не
> делается — `agent_pick` остаётся task-centric инструментом coding-агента
> на `standard`/`full`. Аудит: [audit/2026-09-20-doc-curator-section-c.md](../docs/system/audit/2026-09-20-doc-curator-section-c.md).

Критерий «RFC готов к декомпозиции» был выполнен на входе: контракт
`AGENT_TOOLS` и `ctx_search(...)` заданы; non-goals выписаны; зависимость
от живого `search_service.search` указана с `file:line`. Итог на выходе —
`docs/system/audit/2026-09-20-doc-curator-section-d.md`.
