# COD-DOC: интеграция с LLM и Copilot

> Как подключить cod-doc к VS Code Copilot, Claude Desktop, Claude Code
> и другим LLM-системам для работы с документацией проектов.

---

## Обзор интерфейсов

cod-doc предоставляет 4 слоя доступа:

| Слой | Для кого | Когда использовать |
|------|----------|-------------------|
| CLI | Разработчик в терминале | Ручная работа, скрипты, CI/CD |
| TUI | Разработчик интерактивно | Первичная настройка, wizard |
| REST API | Внешние системы | Dashboards, CI, веб-интерфейсы |
| **MCP** | **LLM-клиенты** | **Copilot, Claude, агенты** |

MCP (Model Context Protocol) — стандартный протокол для подключения LLM
к внешним инструментам. cod-doc реализует MCP server с **104 инструментами**
(точная цифра валидируется тестом `tests/test_mcp_integration_doc.py`).
Полный live-каталог — `skill_list` + `tools/list` через любого MCP-клиента;
карта по семействам — раздел [Каталог MCP-инструментов](#каталог-mcp-инструментов).

---

## Вариант 1. VS Code Copilot Chat

**Самый удобный способ** — Copilot получает полный доступ к cod-doc прямо в IDE.

### Настройка

Создайте `.vscode/mcp.json` в корне проекта:

```json
{
  "servers": {
    "cod-doc": {
      "command": "cod-doc-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

> Если cod-doc установлен в venv, укажите полный путь:
> `"command": "/path/to/cod-doc/.venv/bin/cod-doc-mcp"`

### Что можно делать

После подключения в Copilot Chat доступна вся MCP-поверхность (104 тула на текущий релиз). Примеры запросов:

- "Покажи статус проекта weather-cli"
- "Какие задачи не закрыты?"
- "Есть ли устаревшие ссылки в MASTER.md?"
- "Добавь задачу: написать документацию для модуля auth"
- "Обнови хэши"
- "Найди в документации всё про обработку ошибок"
- "Запусти агента на одну итерацию"

Copilot сам выбирает нужные инструменты и вызывает их.

### Дополнение: copilot-instructions.md

Для лучшей работы Copilot создайте `.github/copilot-instructions.md`:

```markdown
## Документация проекта

Этот проект документирован через cod-doc.
- Навигатор документации: MASTER.md (читай его первым)
- Структура: specs/ (требования), arch/ (архитектура), models/ (данные), docs/ (прочее)
- Если нужно найти что-то в доках — используй MCP tool `search_docs`
- Перед изменением доков — проверь хэши через `check_stale_refs`
```

Это даёт Copilot контекст о том, как организована документация, даже без MCP.

---

## Вариант 2. Claude Desktop

### Настройка

Откройте `Settings → Developer → Edit Config` и добавьте:

```json
{
  "mcpServers": {
    "cod-doc": {
      "command": "cod-doc-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

Перезапустите Claude Desktop. В интерфейсе появится иконка 🔧 с доступными инструментами.

### Что можно делать

Та же MCP-поверхность: docs, tasks, plans, stories, links, revisions, runs, approvals, routines, activity, skills. Claude Desktop хорошо работает с инструментами — можно вести диалог о документации:

```
Ты: Покажи список проектов
Claude: [вызывает list_projects] → У тебя 2 проекта: weather-cli и proinstall...

Ты: Какой статус у proinstall?
Claude: [вызывает get_project_status] → 9 документов, все хэши валидны, 6 открытых задач...

Ты: Покажи содержимое MASTER.md
Claude: [вызывает get_master] → ...
```

---

## Вариант 3. Claude Code (CLI)

### Настройка

```bash
claude mcp add cod-doc cod-doc-mcp -- --transport stdio
```

### Или через конфиг `.claude/settings.json`:

```json
{
  "mcpServers": {
    "cod-doc": {
      "command": "cod-doc-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

### Использование

```bash
claude "Покажи статус документации проекта proinstall"
claude "Найди в документации всё про CSS-переменные"
claude "Добавь задачу: обновить docs/overview.md после рефакторинга"
```

---

## Вариант 4. Streamable HTTP (для удалённых клиентов)

Если MCP-клиент не поддерживает stdio или нужен удалённый доступ:

```bash
cod-doc mcp --transport streamable-http --host 127.0.0.1 --port 8001
# endpoint: http://127.0.0.1:8001/mcp
```

Подключение в любом MCP-клиенте:
```json
{
  "mcpServers": {
    "cod-doc": {
      "url": "http://127.0.0.1:8001/mcp"
    }
  }
}
```

Когда использовать:
- Сервер на одной машине, клиент на другой
- Docker / remote development
- Несколько клиентов к одному серверу

---

## Вариант 5. REST API (без MCP)

Для систем, не поддерживающих MCP:

```bash
cod-doc serve  # → http://localhost:8765
```

Доступные endpoints:
```
GET  /api/config
GET  /api/projects
POST /api/projects
GET  /api/projects/{name}/status
GET  /api/projects/{name}/tasks
POST /api/projects/{name}/tasks
WS   /ws/projects/{name}/run     # запуск агента через WebSocket
```

### Пример: GitHub Actions

```yaml
- name: Check documentation freshness
  run: |
    cod-doc serve &
    sleep 2
    STATUS=$(curl -s http://localhost:8765/api/projects/myproject/status)
    STALE=$(echo $STATUS | jq '.stale_refs')
    if [ "$STALE" -gt 0 ]; then
      echo "::warning::Documentation has $STALE stale references"
    fi
```

---

## Вариант 6. Только MASTER.md (без сервера)

Даже без запущенного MCP-сервера, MASTER.md полезен для LLM:

1. **Copilot instructions** → указать "читай MASTER.md первым"
2. **Контекстное окно** → скопировать MASTER.md в чат с любым LLM
3. **@workspace в Copilot** → Copilot найдёт MASTER.md через поиск по файлам

MASTER.md v0.2 спроектирован как двухслойный:
- Верхняя часть — таблицы, диаграммы, чеклист (понятно человеку)
- `<details>` блок — метаданные, хэши, протоколы (понятно LLM)

LLM может разобрать MASTER.md и выстроить карту проекта даже без MCP.

---

## Каталог MCP-инструментов

> Источник истины — `tools/list` MCP-клиента и `skill_list` для гайдов.
> Эта таблица — навигатор «что в каком семействе» на текущий релиз.
> Числа сверяются с реальным каталогом через
> [`tests/test_mcp_integration_doc.py`](../tests/test_mcp_integration_doc.py).

| Семейство | Кол-во | Назначение | Ключевые тулы |
|-----------|-------:|------------|---------------|
| **doc.\*** | 7 | DB-backed документы | `doc_list`, `doc_body`, `doc_create`, `doc_rename`, `doc_export`, `doc_drift`, `doc_get` |
| **task.\*** | 15 | DB-backed задачи (lifecycle) | `task_create`, `task_create_many`, `task_get`, `task_list`, `task_next_ready`, `task_update_status`, `task_complete`, `task_set_blocker`, `task_find_duplicate`, `task_log_progress`, … |
| **task_doc.\*** | 5 | Артефакты, связанные с задачей | `task_doc_put`, `task_doc_get`, `task_doc_list`, `task_doc_revisions`, `task_doc_revert` |
| **task_checkout / task_release** | 2 | Атомарный захват задачи (PCA-200) | `task_checkout`, `task_release` |
| **plan.\*** | 10 | Планы исполнения и графы зависимостей | `plan_create`, `plan_section_create`, `plan_sections_list`, `plan_ready`, `plan_progress`, `plan_critical_path`, `plan_forward_chain`, `plan_reverse_chain`, `plan_audit`, `plan_export` |
| **story.\*** | 7 | User stories + acceptance criteria | `story_create`, `story_list`, `story_get`, `story_link`, `story_add_criterion`, `story_update_status`, `story_coverage` |
| **link.\*** | 4 | Гибридные ссылки между документами | `link_list`, `link_sync`, `link_verify`, `link_suggest_for_section` |
| **revision.\*** | 3 | История изменений сущностей | `revision_list`, `revision_get`, `revision_revert` |
| **run.\*** | 3 | Идентифицированные run-scope мутации | `run_list`, `run_get`, `run_revert` |
| **approval.\*** | 5 | Human-in-the-loop одобрения | `approval_request`, `approval_list`, `approval_get`, `approval_resolve`, `approval_cancel` |
| **activity.\*** | 2 | Единый audit-таймлайн | `activity_list`, `activity_for_run` |
| **routine.\*** | 7 | Cron-style health checks | `routine_create`, `routine_list`, `routine_get`, `routine_update_status`, `routine_delete`, `routine_run_now`, `routine_history` |
| **skill.\*** | 2 | Каталог skill-инструкций для агента | `skill_list`, `skill_get` |
| **context / capabilities / session** | 9 | Snowball-сборка контекста, L0 bootstrap, tool discovery + per-tool describe, change-log, safe-call envelope, workspace defaults | `context_get`, `capabilities`, `tool_search`, `tool_describe`, `tools_diff`, `tool_call_safe`, `set_default_project`, `get_default_project`, `clear_default_project` |
| **hash / verify** | 2 | Контроль целостности файлов | `hash_file`, `verify_hash` |
| **check_config** | 1 | Самодиагностика сервера | `check_config` |
| **Legacy (YAML)** | 20 | Проекты / задачи / MASTER / поиск / агент — depending on `tasks_yaml` стора | `list_projects`, `add_project`, `remove_project`, `get_project_status`, `list_tasks`, `add_task`, `update_task`, `next_pending_task`, `get_master`, `update_master_hashes`, `check_stale_refs`, `generate_ref`, `read_file`, `read_context`, `list_files`, `search_docs`, `reindex`, `run_agent_once`, `get_agent_context`, `clear_agent_context` |
| **ИТОГО** | **104** | | |

Legacy-семейство дублирует часть DB-поверхности (например `add_task` ↔
`task_create`, `list_tasks` ↔ `task_list`) и помечено `DEPRECATED` в
docstring соответствующих тулов. Для новых интеграций — игнорируй legacy
и опирайся на DB-поверхность; будущий профиль `--profile standard` (PCA-951)
скроет legacy полностью.

## MCP Resources

| URI | Тип | Описание |
|-----|-----|---------|
| `cod-doc://config` | Статический | Текущая конфигурация |
| `cod-doc://projects` | Статический | Список проектов |
| `cod-doc://project/{name}/master` | Шаблон | MASTER.md проекта |
| `cod-doc://project/{name}/tasks` | Шаблон | Задачи проекта |

## MCP Prompts

| Промпт | Назначение | Параметры |
|--------|-----------|-----------|
| `doc_review` | Ревью документации | `project_name`, `focus?` |
| `doc_plan` | План документирования | `project_name` |
| `onboard_project` | Онбординг нового проекта | `project_name` |

---

## Сравнение вариантов

| Критерий | MCP (stdio) | MCP (HTTP) | REST API | Только MASTER.md |
|----------|-------------|-----------|----------|-------------------|
| Настройка | Простая | Средняя | Простая | Никакой |
| Copilot Chat | ✅ | ✅ | ❌ | Частично |
| Claude Desktop | ✅ | ✅ | ❌ | Через copy-paste |
| CI/CD | ❌ | ✅ | ✅ | ❌ |
| Кол-во инструментов | 104 | 104 | ~8 | 0 |
| Семантический поиск | ✅ | ✅ | ❌ | ❌ |
| Запуск агента | ✅ | ✅ | ✅ (WS) | ❌ |

---

## Рекомендации

**Для одного разработчика:** MCP (stdio) + VS Code Copilot Chat. Минимум настройки, максимум возможностей.

**Для команды:** MCP (HTTP) + copilot-instructions.md. Сервер на общей машине, каждый подключается из своего IDE.

**Для CI/CD:** REST API. Проверка свежести документации, автоматическое создание задач при обнаружении stale refs.

**Для быстрого старта:** Только MASTER.md + copilot-instructions.md. Нулевая настройка, Copilot находит MASTER.md через @workspace.

## Как я предлагаю учиться дальше

Хороший следующий цикл обучения:

1. Я показываю тебе живой smoke test MCP-клиентом.
2. Потом мы вместе добавляем ещё один tool.
3. Потом ты сам формулируешь, чего не хватает документационному workflow.
4. После этого уже решаем, оставлять ли нативный MCP server или делать bridge поверх REST.