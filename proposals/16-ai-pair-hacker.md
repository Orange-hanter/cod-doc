# 16 — AI-Pair-Hacker: cod-doc в петле vibecoder'а

> Категория: 🔵 Архитектура · Риск: средний · Зависимости: 06 atomic-checkout, 09 activity-log, OBI (code-ref)

## Контекст: vibecoder-боль

Vibecoding (Claude Code / OpenCode / Cursor) радикально ускоряет написание кода, но создаёт системную боль:

- **Дрейф документации.** Код обгоняет доки. Через неделю автор не помнит, зачем менял `task_service.update_status` и какие edge cases покрывал.
- **Потерянный контекст.** «Где у нас считается food cost?» — вопрос, на который grep отвечает плохо, а `git log --all -S "food cost"` — ещё хуже.
- **Нет post-hoc аудита.** «Что натворил агент на прогоне X?» — сейчас нельзя ответить без ручного просмотра `git log`.

Существующие предложения cod-doc уже закрывают **часть** проблемы:
- `09-activity-log` пишет каждую мутацию в `activity_service.emit(...)`.
- `06-atomic-checkout` защищает от race в UI/CLI/MCP.
- OBI (code-ref parser, `22f7e45`, `01ba3ab`) линкует коммиты ↔ файлы ↔ сущности.

**Не закрыто:** никто не **связывает** конкретный edit vibecoder'а с конкретной задачей / документом в cod-doc в реальном времени.

## Текущее состояние cod-doc

- `MCP-сервер` экспонирует 119 тулов, в т.ч. `task_*`, `doc_*`, `plan_*`, `activity_*`, `commit_link_*` (`OBI-010/011`).
- `activity_service` эмитит события на каждую мутацию (proposal 09 / PCA-912).
- `commit_link_service` (OBI-010) уже умеет линковать коммиты ↔ задачи.
- `task_checkout` (PCA-200) — атомарный захват задачи.
- **Нет:** плагина / обёртки, который бы дёргал cod-doc **из** vibecoder-loop'а.

## Предложение

Создать **отдельный sub-project** `cod-doc-pair/` (или модуль внутри `cod_doc/agent/`) — лёгкий Python-клиент + CLI, который интегрируется с vibecoder-инструментами и cod-doc MCP:

### 4.1. Pre-edit hook: «а это задокументировано?»

Перед `git commit` или перед крупным `Edit` плагин спрашивает у cod-doc:
```
cod-doc pre-edit --project=mozarella --files=cod_doc/services/food_cost.py
  → возвращает: релевантные tasks (status: in_progress), связанные docs, последние activity events
  → MCP-тулы: task_search, doc_search, activity_list, commit_link_service.search_by_files
```

Если найдена активная задача — плагин предлагает разработчику:
1. «Этот edit продолжает task COD-123 (in_progress). Продолжить?»
2. «Этот edit не относится ни к одной открытой задаче. Создать новую?»

### 4.2. Post-commit hook: авто-документирование

После `git commit` плагин:
1. Парсит commit message → ищет `COD-XXX` / `PCA-XXX` / `(#PR)`.
2. Через `commit_link_service` линкует SHA ↔ task.
3. Если в коммите затронуты `docs/**` — не трогает (документация обновляется явно).
4. Если затронут только код — генерит **черновик** обновления связанного `task_doc` (через `task_doc_put`) и помечает `proposed: true`.

### 4.3. Скилл `cod-doc/pair-hacker/SKILL.md`

Активируется, когда vibecoder-агент (OpenCode / Claude Code) работает в проекте с активным cod-doc. Содержит:
- Когда делать `agent_pick` (перед началом работы).
- Когда делать `task_checkout` (перед edit).
- Когда делать `agent_report` (если застрял).
- Когда делать `agent_complete` (после commit).
- Формат `commit_link` (tag-pattern в commit message).

### 4.4. CLI-команда

```bash
cod-doc pair-hook install       # ставит git hooks (pre-commit, post-commit, commit-msg)
cod-doc pair-hook status        # показывает, на каких задачах сейчас работает агент
cod-doc pair-hook sync          # подтягивает activity_log за день → предлагает обновления task_doc
cod-doc pair-hook checkout TASK-123  # атомарный захват + уведомление других агентов
```

## Эффект

| Метрика | До | После |
|---|---|---|
| Дрейф docs vs code | 2-3 недели | <1 день (post-commit hook) |
| Время на «что делал агент X?» | 30+ минут | 1 минута (`activity_for_run` + `commit_link_service`) |
| Onboarding нового vibecoder'а в проект | день | 30 минут (читает `task_summary` + связанные `task_doc`) |

## Структура

```
cod-doc-pair/                     # standalone Python package
├── pyproject.toml
├── src/cod_doc_pair/
│   ├── cli.py                    # click CLI (install/status/sync/checkout)
│   ├── mcp_client.py             # тонкий async MCP-клиент к cod-doc серверу
│   ├── hooks/
│   │   ├── pre_commit.py         # проверка in_progress tasks, связанных с файлами
│   │   ├── post_commit.py        # commit_link + auto task_doc proposal
│   │   └── commit_msg.py         # tag-pattern validator (COD-XXX, PCA-XXX)
│   ├── integrations/
│   │   ├── opencode_hook.py      # адаптер к OpenCode CLI hook API
│   │   ├── claude_code_hook.py   # адаптер к Claude Code settings.json hooks
│   │   └── cursor_rule.py        # Cursor rules (.cursorrules)
│   └── skill_md/                 # → копируется в cod_doc/skills/pair-hacker/SKILL.md
└── tests/
```

## Зависимости

| Proposal | Нужно для |
|---|---|
| `06-atomic-checkout` (PCA-200) | race-protection между парой vibecoder'ов в одном проекте |
| `09-activity-log` (PCA-912) | post-commit hook пишет события |
| `OBI-010/011` (commit_link) | линк SHA ↔ task |
| Cycle-5 agent profile (AGT-001..007) | `--profile minimal` поверхность для быстрого старта |

## Риски и митигация

| Риск | Митигация |
|---|---|
| Post-commit hook создаёт шум в `task_doc` (предлагает нерелевантные обновления) | `proposed: true` + human-in-the-loop; PR review перед merge |
| Git hooks замедляют commit | Только асинхронные операции; pre-commit ≤ 200ms timeout |
| Разные vibecoder-инструменты имеют разные hook API | `integrations/` модуль — по одному адаптеру на инструмент, общая core-логика |
| Пользователь работает в проекте без cod-doc init | `cod-doc pair-hook install` отказывается с понятной ошибкой |

## Acceptance criteria (для RFC-задачи)

1. `cod-doc-pair` устанавливается через `pip install cod-doc-pair` отдельно от cod-doc.
2. `cod-doc pair-hook install` ставит 3 git hook'а (pre-commit, post-commit, commit-msg).
3. После `git commit` с `COD-123` в message — `commit_link_service` показывает связь в течение 1 сек.
4. Pre-commit hook с `in_progress` задачей в стеке — блокирует commit, требуя ack.
5. Интеграция с OpenCode / Claude Code — через 1 файл настройки.
6. `SKILL.md pair-hacker` автоматически подгружается агентом при `agent_pick`.

## Roadmap

- **Phase 1 (1-2 недели):** core + git hooks + commit_link.
- **Phase 2 (1 неделя):** OpenCode + Claude Code адаптеры.
- **Phase 3 (ongoing):** auto task_doc proposal (LLM), Cursor rules, IntelliJ plugin.

## Альтернативы, которые **не** выбрали

- **Doc-gen из кода (Sphinx/MkStrings):** не решает «что делал агент X» и не линкует с задачами.
- **Просто требовать от vibecoder'а писать доки:** не работает на практике (проверено).
- **AI-агент, читающий git log вручную:** работает, но тратит 1-2К токенов на каждый «а что здесь было» — snowball protocol уже умеет лучше.

## Источники

- Реальный workflow: danil@Mozarella + lurkers-dev (3+ проекта под vibecoding).
- Paperclip [`skills/`](https://github.com/paperclipai/paperclip/tree/master/skills) — паттерн «активируемый по триггеру skill».
- Cursor `.cursorrules`, Claude Code `settings.json` hooks — оба поддерживают кастомные pre/post-action скрипты.
