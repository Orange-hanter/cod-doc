# cod-doc — плагин для Claude Code

Ставит в сессию всё, что нужно для работы с проектом, чья документация и
задачи живут в БД cod-doc (`<project>/.cod-doc/state.db`), а markdown —
проекция.

Плагин **project-agnostic**: слаг проекта и путь к бинарю резолвятся на лету
(`scripts/cod-doc-env.sh`), поэтому он одинаково работает в самом cod-doc и в
любом другом подключённом репозитории.

## Что внутри

| Часть | Что даёт |
|---|---|
| MCP-сервер `cod-doc` | тулы `task_*` / `doc_*` / `plan_*` / `adr_*` / `link_*`; профиль `standard` (108 тулов), меняется `COD_DOC_PROFILE` |
| `/cod-doc:status` | снимок проекта: прогресс, очередь, зависшие checkout'ы, дрейф |
| `/cod-doc:task` | протокол задачи: checkout → работа → complete со sha |
| `/cod-doc:drift` | проверка и (по `--fix`) починка `edited_in_place` |
| `/cod-doc:setup` | подключение нового репозитория к cod-doc |
| skill `task-flow` | закон работы с задачами; подхватывается по контексту |
| skill `doc-sync` | markdown ↔ БД, hash-реестр, семантика drift |
| agent `cod-doc-scout` | read-only разведка по базе без вываливания документов в контекст |
| hook PostToolUse | после правки **tracked** `.md` напоминает про `doc import` |
| hook SessionStart | сводка незакрытых задач — **выключена по умолчанию** |

## Установка

```bash
claude plugin marketplace add /Users/dakh/Git/_my/cod-doc   # или URL репозитория
claude plugin install cod-doc@cod-doc
```

Проверка: `claude plugin list`, затем `/cod-doc:status` в подключённом
проекте.

## Требования

- `cod-doc` установлен: бинарь ищется как `$COD_DOC_BIN` → `<project>/.venv/bin/cod-doc`
  → `cod-doc` в PATH; MCP-сервер — `$COD_DOC_MCP_BIN` → `<то же>-mcp` → PATH.
- В проекте есть `.cod-doc/state.db` (иначе хуки молчат, а команды предложат
  `/cod-doc:setup`).
- `sqlite3` в PATH — им хуки резолвят слаг, не поднимая питон.

## Переменные окружения

| Переменная | Значение |
|---|---|
| `COD_DOC_PROJECT` | принудительный слаг проекта (иначе — по `root_path` в БД) |
| `COD_DOC_BIN`, `COD_DOC_MCP_BIN` | явные пути к бинарям |
| `COD_DOC_PROFILE` | профиль MCP: `agent` (6) / `minimal` (20) / `standard` (108) / `full` (112) |
| `COD_DOC_SESSION_BRIEF=1` | включает сводку при старте сессии (по умолчанию выключена) |

## Отношение к `.claude/` самого репозитория cod-doc

Репозиторий cod-doc держит свои `.claude/skills/{task-flow,doc-sync}`,
`.claude/commands/gate.md` и `.claude/hooks/md-drift-reminder.sh` —
привязанные к нему самому (слаг `cod-doc`, `.venv/bin/…`, план
`adoption-2026-08`). Скиллы плагина — обобщённые копии тех же правил и
приходят под префиксом `cod-doc:`, так что конфликта имён нет; при
одновременной работе обоих напоминание о дрейфе придёт дважды. Убирать
репо-локальные версии — отдельное решение, плагин этого не требует.

Команда `/gate` намеренно не переехала в плагин: она гоняет `ruff`/`mypy`/
`pytest` самого cod-doc и в чужом проекте бессмысленна.
