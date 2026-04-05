# Гайд: документация проекта с нуля через COD-DOC

> Пошаговое руководство на реальном примере.
> Время: ~30 минут. Результат: полностью документированный проект.

---

## Что понадобится

- Python 3.11+ с установленным `cod-doc`
- Любой проект с исходным кодом (мы создадим демо-проект)
- Терминал

## Шаг 0. Установка cod-doc

```bash
pip install cod-doc
# или если клонирован репозиторий:
cd /path/to/cod-doc && pip install -e .
```

Проверяем:
```bash
cod-doc --help
```

---

## Шаг 1. Создаём демо-проект

Для этого гайда создадим простой Python CLI — прогноз погоды.

```bash
mkdir ~/weather-cli && cd ~/weather-cli
git init
```

Создаем структуру:
```
weather-cli/
├── weather/
│   ├── __init__.py
│   ├── cli.py          # click-based CLI
│   ├── api.py          # HTTP client to weather API
│   └── formatter.py    # output formatting
├── tests/
│   └── test_api.py
├── pyproject.toml
└── README.md
```

> Подставьте сюда свой проект — шаги одинаковые для любого стека.

---

## Шаг 2. Регистрация проекта в cod-doc

### Вариант A: через CLI (интерактивный wizard)

```bash
cod-doc wizard
```

Wizard задаст вопросы:
1. **API-ключ OpenAI** — нужен для автономного агента (можно пропустить для ручного режима)
2. **Имя проекта** — `weather-cli`
3. **Путь** — `/Users/you/weather-cli`
4. **MASTER.md** — имя файла навигатора (по умолчанию `MASTER.md`)

### Вариант B: через MCP (программно)

```bash
# Запускаем MCP-сервер
cod-doc mcp --transport stdio
```

Или через Python:
```python
import asyncio
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="cod-doc-mcp", args=["--transport", "stdio"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Регистрируем проект
            result = await session.call_tool("add_project", {
                "name": "weather-cli",
                "path": "/Users/you/weather-cli",
            })
            print(result.content[0].text)

asyncio.run(main())
```

### Вариант C: через REST API

```bash
cod-doc serve  # запускает FastAPI на :8765

curl -X POST http://localhost:8765/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "weather-cli", "path": "/Users/you/weather-cli"}'
```

### Что произошло

cod-doc создал в проекте:
```
weather-cli/
├── .cod-doc/           # ← служебная директория
│   ├── tasks.yaml      # очередь задач
│   └── state.yaml      # состояние агента
├── MASTER.md           # ← навигатор документации
└── ... (ваш код)
```

`.cod-doc/` добавлена в `.gitignore`.

---

## Шаг 3. Изучаем сгенерированный MASTER.md

Откройте `MASTER.md` — это главный навигатор проекта. Новый формат v0.2:

- **Верхняя часть** — для людей: обзор, таблица разделов, чеклист, журнал
- **Нижняя часть** (`<details>`) — для LLM: метаданные, хэши, ссылки, протоколы

> Ключевой принцип: MASTER.md — единственная точка входа.
> Агент (и человек) читает сначала его, а потом переходит к нужным файлам.

---

## Шаг 4. Создаём структуру документации

cod-doc использует 4 директории:

| Директория | Назначение | Примеры |
|------------|-----------|---------|
| `specs/` | Что должен делать продукт | Требования, user stories, API-контракты |
| `arch/` | Как устроен код | Архитектура, компоненты, data flow |
| `models/` | Структуры данных | Модели, DTO, схемы БД |
| `docs/` | Всё остальное | Обзор, операции, FAQ, onboarding |

```bash
mkdir -p specs arch models docs
```

---

## Шаг 5. Создаём первый документ (ручной режим)

Создадим `docs/overview.md`:

```markdown
# Weather CLI

## Назначение
CLI-утилита для получения прогноза погоды из терминала.

## Возможности
- Текущая погода по городу
- Прогноз на N дней
- Форматы вывода: таблица, JSON, compact

## Стек
- Python 3.11+
- Click (CLI framework)
- httpx (HTTP client)
- OpenWeatherMap API
```

### Генерируем ссылку для MASTER.md

**Через CLI:**
```bash
cod-doc hash calc docs/overview.md
# → sha:a1b2c3d4e5f6  docs/overview.md
```

**Через MCP:**
```python
result = await session.call_tool("generate_ref", {
    "project_name": "weather-cli",
    "file_path": "docs/overview.md",
})
# → 📁 /docs/overview.md | 🗃️ doc:docs_overview_md | 🔑 sha:a1b2c3d4e5f6
```

### Добавляем в MASTER.md

В таблицу разделов:
```markdown
| Обзор | [docs/overview.md](docs/overview.md) | Назначение, стек, возможности | 🟢 |
```

В секцию LLM-метаданных (`<details>`):
```markdown
#### Overview
- **Ссылка:** `📁 /docs/overview.md | 🗃️ doc:docs_overview_md | 🔑 sha:a1b2c3d4e5f6`
- **Статус:** `🟢 VERIFIED`
```

---

## Шаг 6. Массовое создание через задачи

Вместо ручного создания каждого файла — ставим задачи агенту.

### Через CLI:
```bash
cod-doc task add weather-cli "Описать архитектуру CLI" --priority 1
cod-doc task add weather-cli "Описать API-клиент" --priority 2
cod-doc task add weather-cli "Описать модель WeatherData" --priority 3
```

### Через MCP:
```python
for title, prio in [
    ("Описать архитектуру CLI", 1),
    ("Описать API-клиент weather/api.py", 2),
    ("Описать модель WeatherData", 3),
    ("Описать форматирование вывода", 4),
    ("Написать operations runbook", 5),
]:
    await session.call_tool("add_task", {
        "project_name": "weather-cli",
        "title": title,
        "priority": prio,
    })
```

### Смотрим очередь:
```python
result = await session.call_tool("list_tasks", {
    "project_name": "weather-cli",
    "status": "pending",
})
```

---

## Шаг 7. Автономный агент (если есть API-ключ)

```bash
cod-doc agent weather-cli --autonomous
```

Агент:
1. Прочитает MASTER.md
2. Возьмёт следующую задачу из очереди
3. Прочитает исходный код через инструменты
4. Создаст/обновит файл документации
5. Пересчитает хэши
6. Обновит MASTER.md
7. Повторит для следующей задачи

### Через MCP:
```python
events = await session.call_tool("run_agent_once", {
    "project_name": "weather-cli",
    "autonomous": True,
})
```

> Без API-ключа — создавайте документы вручную (шаг 5) или через Copilot (шаг 10).

---

## Шаг 8. Проверка целостности

### Проверить хэши:
```bash
cod-doc hash update MASTER.md
```

### Через MCP:
```python
# Найти устаревшие ссылки
result = await session.call_tool("check_stale_refs", {
    "project_name": "weather-cli",
})
# → {"summary": {"total": 5, "valid": 4, "stale": 1, "broken": 0}}

# Обновить хэши
result = await session.call_tool("update_master_hashes", {
    "project_name": "weather-cli",
})
# → {"updated": 1, "warnings": []}
```

**Статусы:**
- `VALID` — файл не менялся, хэш совпадает
- `STALE` — файл изменён, хэш устарел → нужно обновить
- `BROKEN` — файл удалён → нужно убрать из MASTER.md

---

## Шаг 9. Семантический поиск

cod-doc индексирует документы в ChromaDB для поиска по смыслу.

### Индексация:
```python
await session.call_tool("reindex", {"project_name": "weather-cli"})
# → {"indexed": 5, "errors": []}
```

### Поиск:
```python
await session.call_tool("search_docs", {
    "project_name": "weather-cli",
    "query": "как обрабатываются ошибки API",
    "n_results": 3,
})
# → [{"path": "arch/api-client.md", "score": 0.87, "snippet": "..."}]
```

---

## Шаг 10. Интеграция с Copilot / LLM

Полный раздел — в [docs/llm-integration.md](docs/llm-integration.md).

Кратко:

### VS Code + Copilot Chat
1. Добавить MCP-сервер в `.vscode/mcp.json`
2. Copilot получает доступ к 23 инструмента cod-doc
3. Можно спросить: "покажи статус проекта", "какие задачи не закрыты", "обнови хэши"

### Claude Desktop / любой MCP-клиент
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

### REST API для любых систем
```bash
cod-doc serve  # → http://localhost:8765/api/...
```

---

## Шаг 11. Повседневная работа

### Код изменился → обновляем доку:
```
1. cod-doc hash update MASTER.md        # находим устаревшее
2. Обновляем затронутые файлы
3. cod-doc hash update MASTER.md        # фиксируем новые хэши
4. git commit
```

### Добавляем новый модуль:
```
1. Создаём файл в нужной директории (specs/, arch/, models/, docs/)
2. cod-doc hash calc path/to/file.md    # получаем хэш
3. Добавляем строку в MASTER.md (таблица разделов + LLM-секция)
4. git commit
```

### Ревью документации:
```python
# Проверяем покрытие
status = await session.call_tool("get_project_status", {"project_name": "weather-cli"})

# Ищем проблемы
stale = await session.call_tool("check_stale_refs", {"project_name": "weather-cli"})
```

---

## Итого: что мы использовали

| Возможность | CLI | MCP | REST |
|-------------|-----|-----|------|
| Регистрация проекта | `cod-doc wizard` | `add_project` | `POST /api/projects` |
| Создание задач | `cod-doc task add` | `add_task` | `POST /api/projects/{name}/tasks` |
| Просмотр задач | `cod-doc task list` | `list_tasks` | `GET /api/projects/{name}/tasks` |
| Расчёт хэшей | `cod-doc hash calc` | `hash_file` | — |
| Обновление хэшей | `cod-doc hash update` | `update_master_hashes` | — |
| Проверка целостности | — | `check_stale_refs` | — |
| Генерация ссылки | — | `generate_ref` | — |
| Чтение файла | — | `read_file` | — |
| Список файлов | — | `list_files` | — |
| Семантический поиск | — | `search_docs` | — |
| Индексация | — | `reindex` | — |
| Запуск агента | `cod-doc agent` | `run_agent_once` | `WS /ws/projects/{name}/run` |
| Конфигурация | `cod-doc wizard` | `check_config` | `GET /api/config` |

### MCP-only инструменты (23 штуки):
`list_projects` · `get_project_status` · `add_project` · `remove_project` ·
`list_tasks` · `add_task` · `update_task` · `next_pending_task` ·
`get_master` · `update_master_hashes` · `check_stale_refs` · `generate_ref` ·
`read_context` · `read_file` · `list_files` ·
`hash_file` · `verify_hash` ·
`search_docs` · `reindex` ·
`run_agent_once` · `get_agent_context` · `clear_agent_context` ·
`check_config`
