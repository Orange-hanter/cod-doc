"""
Определения инструментов агента (OpenAI function calling формат).
"""

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать файл из проекта. Используй для загрузки MASTER.md, спецификаций, моделей.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь от корня проекта. Пример: MASTER.md, specs/auth.md",
                    },
                    "page": {
                        "type": "integer",
                        "description": "Номер страницы (200 строк). По умолчанию 1.",
                        "default": 1,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Создать или перезаписать файл в проекте. Используй для создания спецификаций, обновления MASTER.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь от корня проекта"},
                    "content": {"type": "string", "description": "Содержимое файла"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Список файлов в директории проекта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Директория от корня. По умолчанию '.' (корень проекта).",
                        "default": ".",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob-паттерн. Пример: **/*.md",
                        "default": "*",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc_hash",
            "description": "Вычислить SHA-256 хэш (12 символов) для файла. Используй для генерации гибридных ссылок.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь от корня проекта"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_context",
            "description": "Получить содержимое файла по гибридной ссылке COD-DOC. Проверяет хэш перед выдачей.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Гибридная ссылка: 📁 /path | 🗃️ doc:id | 🔑 sha:12hex",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["L1", "L2"],
                        "description": "L1 — только файл, L2 — файл + список зависимостей",
                        "default": "L1",
                    },
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_master_hashes",
            "description": "Пересчитать все хэши в MASTER.md. Вызывай после изменения любого файла.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_ref",
            "description": "Сгенерировать гибридную ссылку для файла (нужна для вставки в MASTER.md).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь от корня проекта"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Создать новую задачу в очереди проекта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {
                        "type": "integer",
                        "description": "Приоритет 1 (высший) — 10 (низший)",
                        "default": 5,
                    },
                    "context_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Гибридные ссылки на связанные файлы",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Пометить текущую задачу как выполненную.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "result": {"type": "string", "description": "Краткий итог выполнения"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fail_task",
            "description": "Пометить задачу как проваленную с описанием причины.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["task_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Закоммитить изменения в репозиторий проекта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Сообщение коммита"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Список файлов для коммита. Пустой список = все изменения.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Создать и переключиться на ветку перед коммитом",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_human",
            "description": "Задать вопрос человеку и остановить выполнение до получения ответа. Используй при блокировке.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "context": {"type": "string", "description": "Контекст: почему нужен ответ"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_status",
            "description": "Получить текущий статус проекта: задачи, хэши, последний запуск.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Семантический поиск по проиндексированным документам проекта (ChromaDB). "
                "Используй для поиска связанных спецификаций и зависимостей."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос на естественном языке"},
                    "n_results": {
                        "type": "integer",
                        "description": "Количество результатов (по умолчанию 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reindex_project",
            "description": "Переиндексировать документы проекта в ChromaDB. Запускай после создания новых файлов.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
