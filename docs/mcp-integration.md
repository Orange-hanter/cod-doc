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
к внешним инструментам. cod-doc реализует MCP server с **115 инструментами**
(точная цифра валидируется тестом `tests/test_mcp_integration_doc.py`),
сгруппированных в 4 профиля.

---

## Agent profile — 6-tool surface (cycle-5, по умолчанию)

> AI-агент работает task-centric, не CRUD-centric. Agent profile —
> 6 тулов, где каждый возвращает self-sufficient payload. Один вызов
> заменяет 5-10 round-trips.

| Тул | Что делает |
|-----|------------|
| `agent_capabilities()` | L0 entry-point: server version, доступные skills, валидные TaskStatus, default_project, рекомендованный next-action. <4KB. |
| `agent_pick(project, agent_id, plan_scope?)` | Атомарно: ready-set → checkout → assemble **task card** = `{task, context{plan, story, related_docs, siblings, affected_files, recent_history}, navigation{applicable_skills (с ТЕЛАМИ), next_actions, success_criteria, legal_status_transitions}}`. Идемпотентен. |
| `agent_get(project, task_id, what, ref?)` | Opt-in deep fetch. `what ∈ {full_doc_body, related_task, story_full, plan_export}`. |
| `agent_report(project, task_id, kind, message, agent_id?, payload?)` | Dispatcher. `kind ∈ {progress, blocker, approval_request, needs_context}`. |
| `agent_complete(project, task_id, agent_id, commit_sha?, summary?)` | Guarded done + release lock в одной транзакции. |
| `agent_release(project, task_id, agent_id, reason?)` | Drop lock без done; status → todo. |

### Жизненный цикл задачи (canonical 3-step)

```text
1. agent_capabilities()              # кто я / какие skills / какой профиль
2. agent_pick(project, agent_id)     # task + context + navigation card
   ↓ (выполнить работу)
3a. agent_complete(...)              # успех
3b. agent_report(kind='blocker',..)  # застрял
3c. agent_release(reason=...)        # отказ без done
```

### Запуск под agent-профилем

```bash
cod-doc-mcp                              # agent (default cycle-5)
cod-doc-mcp --profile minimal            # 21 cold-start tools
cod-doc-mcp --profile standard           # 111 CRUD tools (без legacy)
cod-doc-mcp --profile full               # все 115 (включая legacy)
COD_DOC_PROFILE=full cod-doc-mcp         # через env
```

### Migration guide (cycle-3/4 → cycle-5)

Если ваша интеграция уже зовёт `task_checkout` / `context_get` /
`task_complete` напрямую — она продолжит работать под `--profile standard`
или `--profile full`. Никаких deprecation на самих CRUD-тулах нет.

Для **новых** агентских интеграций рекомендуется agent profile:

| Cycle-3/4 паттерн (6 calls) | Cycle-5 эквивалент (3 calls) |
|---|---|
| `capabilities()` → `skill_list()` → `skill_get('orchestrator')` → `list_projects()` → ... | `agent_capabilities()` |
| `task_next_ready()` → `task_checkout()` → `context_get('task',id)` → `skill_get('task-standard')` | `agent_pick(project, agent_id)` |
| `task_complete()` → `task_release()` → `activity_emit()` | `agent_complete(project, task_id, agent_id)` |
| `task_set_blocker()` + `task_update_status(blocked)` + `activity_emit()` | `agent_report(kind='blocker', message=...)` |

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

После подключения в Copilot Chat доступна вся MCP-поверхность (120 тулов на текущий релиз). Примеры запросов:

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
| **doc.\*** | 10 | DB-backed документы | `doc_list`, `doc_body`, `doc_create`, `doc_rename`, `doc_export`, `doc_drift`, `doc_drift_all`, `doc_get`, `doc_accept`, `doc_backfill_projection` |
| **task.\*** | 17 | DB-backed задачи (lifecycle) | `task_create`, `task_create_many`, `task_get`, `task_list`, `task_next_ready`, `task_update_status`, `task_update`, `task_complete`, `task_set_blocker`, `task_find_duplicate`, `task_log_progress`, … |
| **task_doc.\*** | 5 | Артефакты, связанные с задачей | `task_doc_put`, `task_doc_get`, `task_doc_list`, `task_doc_revisions`, `task_doc_revert` |
| **task_checkout / task_release** | 2 | Атомарный захват задачи (PCA-200) | `task_checkout`, `task_release` |
| **plan.\*** | 11 | Планы исполнения и графы зависимостей | `plan_create`, `plan_freeze`, `plan_section_create`, `plan_sections_list`, `plan_ready`, `plan_progress`, `plan_critical_path`, `plan_forward_chain`, `plan_reverse_chain`, `plan_audit`, `plan_export` |
| **story.\*** | 7 | User stories + acceptance criteria | `story_create`, `story_list`, `story_get`, `story_link`, `story_add_criterion`, `story_update_status`, `story_coverage` |
| **link.\*** | 4 | Гибридные ссылки между документами | `link_list`, `link_sync`, `link_verify`, `link_suggest_for_section` |
| **revision.\*** | 3 | История изменений сущностей | `revision_list`, `revision_get`, `revision_revert` |
| **run.\*** | 1 | Инспекция прогонов встроенного оркестратора (ADR-012) | `run_get` |
| **approval.\*** | 5 | Human-in-the-loop одобрения | `approval_request`, `approval_list`, `approval_get`, `approval_resolve`, `approval_cancel` |
| **activity.\*** | 1 | Единый audit-таймлайн | `activity_list` |
| **routine.\*** | 7 | Cron-style health checks | `routine_create`, `routine_list`, `routine_get`, `routine_update_status`, `routine_delete`, `routine_run_now`, `routine_history` |
| **skill.\*** | 2 | Каталог skill-инструкций для агента | `skill_list`, `skill_get` |
| **agent.\* (cycle-5)** | 6 | Task-centric surface для AI-агентов: pick → work → complete за 3 вызова | `agent_capabilities`, `agent_pick`, `agent_get`, `agent_report`, `agent_complete`, `agent_release` |
| **adr.\* (ADR-002)** | 9 | Architecture Decision Records: CRUD + supersede DAG + task links + Mermaid diagrams + deprecate | `adr_create`, `adr_get`, `adr_list`, `adr_update`, `adr_add_diagram`, `adr_supersede`, `adr_deprecate`, `adr_link_task`, `adr_graph` |
| **structure.\*** | 5 | Pinned code-structure snapshots, drift, scenarios and BFS context (not projection drift; not ai_review findings) | `structure_get`, `structure_context`, `structure_drift`, `structure_scenarios`, `structure_diff` |
| **context / capabilities / session** | 9 | Admin: snowball-сборка контекста, L0 bootstrap, tool discovery + per-tool describe, change-log, safe-call envelope, workspace defaults | `context_get`, `capabilities`, `tool_search`, `tool_describe`, `tools_diff`, `tool_call_safe`, `set_default_project`, `get_default_project`, `clear_default_project` |
| **check_config** | 1 | Самодиагностика сервера | `check_config` |
| **Legacy (YAML агент)** | 3 | Остаток legacy-surface после STB-002 (2026-06-08): resume-вход + context-хелперы. YAML CRUD (проекты/задачи/MASTER/поиск + hash/verify) удалён — БД источник истины. | `run_agent_once`, `get_agent_context`, `clear_agent_context` |
| **finding.\* (RFC 22)** | 4 | Внешние находки (ai-review / ZAIrgRush / routines): triage и промоушен в задачи. Только профили standard/full | `finding_list`, `finding_get`, `finding_promote`, `finding_dismiss` |
| **ctx.\* (RFC 22)** | 3 | Контекст для внешних потребителей: `ctx_docs` = `doc_list`, `ctx_drift` = `doc_drift_all` (SYM-006D), `ctx_drift_gate` — детерминированный гейт документации по файлам PR с идемпотентным PR-комментарием (SYM-010). Только профили standard/full | `ctx_docs`, `ctx_drift`, `ctx_drift_gate` |
| **ИТОГО** | **115** | | |

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
| Кол-во инструментов | 120 | 120 | ~8 | 0 |
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
