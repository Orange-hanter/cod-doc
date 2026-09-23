# 27 — Agent fit: coding-агент закрывает вопросы тулами, а не SQL

> Категория: 🟢 Adoption · Риск: низкий · Зависимости: RFC 25 (роль куратора,
> профиль `agent`), ADO-092 (frontmatter-расхождения — advisory), ADO-177
> (авто-ID в скоупе проекта), RFC 26 (правка графа плана — соседняя дыра в
> write-пути; здесь только чтение и вывод)

## 1. Контекст

2026-09-23 проверено, как инструмент встраивается в работу coding-агента
(Claude Code) в этом репозитории: живые вызовы демона `:8801` (профиль
`standard`) плюс скан 40 транскриптов сессий
`~/.claude/projects/-Users-dakh-Git--my-cod-doc/*.jsonl`.

| Канал | Вызовов | Что через него идёт |
|---|---|---|
| MCP-тулы | 398 (в 20 из 40 сессий) | запись: `task_complete` 91, `task_checkout` 57, `task_create` 52, `task_update` 29 |
| CLI `cod-doc` | ~900 | `doc drift` 123, `doc import` 59, `task list` 41, `hash update` 17 |
| `sqlite3`/python прямо в `state.db` | **407** | чтение задач 155, документов/секций 113, схемы 37, ревизий/событий 36, запись 5 |

Прямых чтений БД больше, чем всех MCP-вызовов. Это не привычка агента, а
дыры поверхности: на типовые вопросы («что закрыто за 10 дней и с каким
sha», «прогресс всех планов по секциям», «ревизии за сегодня по видам»,
«тело одной секции») одного вызова нет, а плагинные инструкции сами
показывают `sqlite3 -readonly` как штатный путь.

## 2. Текущее состояние (проверено по коду 2026-09-23)

| # | Факт | Где |
|---|---|---|
| F1 | `curator_next` на живом `cod-doc` ≈25 КБ при 8 пунктах очереди: 35 из 37 строк `card.drift.issues` имеют `status: in_sync` (попали по frontmatter-расхождению), поле, объясняющее это, в выдачу не попадает; тела 3–4 скиллов инлайнятся всегда | `services/curator_service.py:97-151`; `projection_service/drift.py:132` |
| F2 | Строка дрейфа сериализуется в четырёх местах копипастой, `metadata_mismatch` нет ни в одном | `curator_service.py:140`, `mcp/tools/doc_tools.py:522, 650`, `cli/cmd_ctx.py:379` |
| F3 | `ctx_drift(limit=N)` режет сканируемые документы, а не найденные проблемы | `projection_service/drift.py:113` |
| F4 | MCP `plan_ready` отдаёт полный `task_to_dict` (с description/acceptance), CLI — компактный | `mcp/tools/plan_tools.py:259`; `cli/plan/cmd_ready.py:38` |
| F5 | `agent_capabilities` на любом профиле: `role: doc-curator`, `forbidden: [agent_pick, task_checkout, task_complete]` — на `standard` это противоречит `task-flow` и CLAUDE.md | `mcp/tools/agent_tools.py:120-146`; `tests/test_agent_profile.py:104-126` |
| F6 | Карточка `agent_pick` всегда инлайнит `orchestrator`, который запрещает checkout/complete | `services/agent_service.py:36-80` |
| F7 | `task_list` фильтрует только по status/priority; в строке `plan_id`/`section_id` как row_id — группировать по плану нечем | `mcp/tools/task_tools.py:75`; `services/serializers.py:19` |
| F8 | Нет `plan_list`; `plan_progress`/`plan_sections_list` требуют `plan_scope`; проектный `recalc_for_project` есть, но только в вебе | `mcp/tools/plan_tools.py:13, 127, 229`; `services/plan_service/reads.py:59, 86` |
| F9 | `revision_list` требует `kind`+`ref`; проектная лента `revision_service.list_for_project` — только в вебе; агрегатов событий нет нигде | `mcp/tools/revision_tools.py:89`; `services/revision_service.py:171` |
| F10 | Тела одной секции по якорю через MCP не получить; `doc_get` не отдаёт `head_revision_id`, который требует `doc_patch_section` | `mcp/tools/doc_tools.py:30, 185` |
| F11 | `context_get(task)`: `related.documents` не заполняется никогда, соседи — без `ORDER BY` | `services/context_service.py:373-429` |
| F12 | `task_create` падал в 11/52 вызовов: префикс ID не выводится из плана (веб умеет — `_id_prefix_from_plan_scope`), занятый явный ID выдаёт сырой `sqlite3.IntegrityError`, `task_create_many` глотает его в `committed=False` без записи в `errors` | `mcp/tools/task_tools.py:331, 378, 485`; `api/web/pages/stories.py:76` |
| F13 | `task_next_ready` вернул ADO-071, все `affects_files` которой в `/Users/dakh/Git/_my/ZAIrgRush`; выборка не смотрит на корень проекта | `services/plan_service/reads.py:119-136` |
| F14 | `task-flow`/`doc-sync` живут в двух разошедшихся копиях (`.claude/skills/` и `plugins/cod-doc/skills/`): баннер RFC 25 только в одной, в плагинной статусы `review`/`deferred`; теста синхронизации нет | `plugins/cod-doc/README.md` |
| F15 | `sqlite3 -readonly state.db` — штатный путь в плагинных скиллах, `commands/{status,setup}.md`, `cod-doc-env.sh`; агент `cod-doc-scout` не имеет MCP-тулов вовсе | `plugins/cod-doc/agents/cod-doc-scout.md` |
| F16 | Напоминание `doc import` срабатывает дважды: репо-хук (на любой `.md`) и плагинный | `.claude/settings.json`; `plugins/cod-doc/hooks/hooks.json` |

## 3. Предложение

Шесть секций плана `agent-fit-2026-09`, задачи `AFT-001…016`; полные
описания и acceptance — в БД (`cod-doc task show AFT-NNN -p cod-doc`).

| Секция | Задачи | Суть |
|---|---|---|
| A. Вывод по бюджету | AFT-001…003 | единый сериализатор строки дрейфа с `metadata_mismatch`; `curator_next` ≤ 8 КБ: advisory отдельно, скиллы по имени (`include_skill_bodies`); компактный `plan_ready`, честный `limit` у `ctx_drift` |
| B. Роль по профилю | AFT-004…005 | `agent_capabilities` на `standard`/`full` → `role: coder`, без запретов; карточка `agent_pick` без `orchestrator` |
| C. Чтение без SQL | AFT-006…010 | фильтры `task_list` (plan/section/type/`completed_since`/…); `plan_list` и проектный `plan_progress`; проектная лента ревизий и `activity_summary`; `doc_section_get`; `context_get(task)` с документами |
| D. Надёжный `task_create` | AFT-011 | префикс из плана; занятый ID → структурная ошибка с `next_free_id`; ретрай гонки авто-ID; ошибки батча по элементам |
| E. Релевантность | AFT-012 | `local_only` в `task_next_ready`/`plan_ready`: задачи с файлами вне корня проекта пропускаются |
| F. Скиллы и интеграция | AFT-013…016 | канон `task-flow`/`doc-sync` — плагин; `sqlite3` из инструкций и scout → тулы; один хук; скрипт-метрика `scripts/agent_usage_report.py` |

Порядок: B → A → D → C → F → E; AFT-016 — через две недели использования.
B первым: противоречие инструкций дешёвое и бьёт по каждой сессии. F после C:
скиллам нужно на что ссылаться вместо SQL.

Новые тулы — `plan_list`, `doc_section_get`, `activity_summary`: `standard`
142→145, `full` 146→149 (пять мест дублирования счётчиков — CLAUDE.md §MCP).
Каждый — с CLI-зеркалом.

## 4. Решения

- **Канон скиллов — плагин.** Он проектно-агностичен и работает во всех
  репо; локальные копии в `.claude/skills/` основного чекаута (`.claude/` в
  `.gitignore`, в репо их нет) удаляются, репо-специфика — в CLAUDE.md.
- **ADO-071…074 не переносим.** Задачи с файлами в ZAIrgRush остаются в плане
  `adoption-2026-08`; достаточно фильтра `local_only`.
- **Роль задаётся профилем, а не константой.** Профиль `agent` остаётся
  куратором (RFC 25 не пересматривается); `standard`/`full` — для
  coding-агента, и payload перестаёт это отрицать.
- **Прямое чтение `state.db` — только отладка схемы.** Для остального
  инструкции указывают тул; если тула нет — это задача, а не повод для SQL.

## 5. Критерий успеха

- `curator_next(project="cod-doc")` ≤ 8 КБ при той же очереди `priority`.
- `agent_capabilities` на `:8801` → `role: coder`, на `:8802` → `doc-curator`.
- Три вопроса из замера — по одному вызову MCP и одной команде CLI.
- Через две недели `scripts/agent_usage_report.py`: прямых чтений `state.db`
  меньше, чем MCP-чтений; ошибок `task_create` ≤ 5%.

## 6. Риски

- **Смена формы выдачи ломает потребителей.** `ctx next --json` и
  `ctx_drift` читают внешние гейты (RFC 22). `ctx_drift`/`doc_drift_all`
  сохраняют полные хэши и прежний состав `issues`, получая только новое поле
  `metadata_mismatch`; короткие хэши и вынос advisory — лишь в карточке
  `curator_next`, у которой внешних потребителей формы `issues` нет.
- **Роль `coder` на `standard` открывает запрещённые куратору тулы.** Это
  уже так: тулы видны на `standard` с ADO-171; меняется только текст payload.
