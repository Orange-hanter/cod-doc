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

MCP (Model Context Protocol) — стандартный протокол для подключения LLM к внешним инструментам. cod-doc реализует MCP server с 23 инструментами.

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

После подключения в Copilot Chat доступны все 23 инструмента. Примеры запросов:

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

Те же 23 инструмента: управление проектами, задачами, хэшами, поиск, агент. Claude Desktop хорошо работает с инструментами — можно вести диалог о документации:

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

## Каталог MCP-инструментов (23 шт.)

### Управление проектами
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `list_projects` | Список проектов | — |
| `get_project_status` | Детальный статус | `project_name` |
| `add_project` | Регистрация проекта | `name`, `path` |
| `remove_project` | Удаление проекта | `name` |

### Управление задачами
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `list_tasks` | Список задач | `project_name`, `status?` |
| `add_task` | Создать задачу | `project_name`, `title`, `priority?` |
| `update_task` | Обновить задачу | `project_name`, `task_id`, `status?`, `title?` |
| `next_pending_task` | Следующая задача | `project_name` |

### MASTER.md и документация
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `get_master` | Прочитать MASTER.md | `project_name` |
| `update_master_hashes` | Пересчитать хэши | `project_name` |
| `check_stale_refs` | Найти устаревшие ссылки | `project_name` |
| `generate_ref` | Создать гибридную ссылку | `project_name`, `file_path` |

### Контекст и файлы
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `read_context` | Чтение по гибридной ссылке | `project_name`, `hybrid_ref` |
| `read_file` | Чтение файла по пути | `project_name`, `file_path` |
| `list_files` | Список файлов (с glob) | `project_name`, `pattern?` |

### Хэши
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `hash_file` | Вычислить хэш файла | `project_name`, `file_path` |
| `verify_hash` | Проверить хэш | `project_name`, `file_path`, `expected_hash` |

### Семантический поиск
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `search_docs` | Поиск по смыслу | `project_name`, `query`, `n_results?` |
| `reindex` | Переиндексация | `project_name` |

### Агент
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `run_agent_once` | Одна итерация агента | `project_name`, `autonomous?` |
| `get_agent_context` | Контекст агента | `project_name` |
| `clear_agent_context` | Сброс контекста | `project_name` |

### Конфигурация
| Инструмент | Действие | Параметры |
|-----------|---------|-----------|
| `check_config` | Статус конфигурации | — |

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
| Кол-во инструментов | 23 | 23 | ~8 | 0 |
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