"""
Определения инструментов агента (OpenAI function calling формат)
и их обработчики.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from cod_doc.core.context import get_context
from cod_doc.core.hash_calc import calc_hash, make_ref, update_hashes
from cod_doc.core.project import Project, Task, TaskStatus
from cod_doc.core.reindex import reindex_project, search_documents as _search_docs

# ── Определения инструментов для LLM ─────────────────────────────────────────

TOOL_DEFINITIONS = [
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


# ── Обработчики инструментов ──────────────────────────────────────────────────

class ToolExecutor:
    """Выполняет вызовы инструментов от имени агента."""

    def __init__(
        self,
        project: Project,
        on_ask_human: Callable[[str, str], str] | None = None,
        chroma_path: str | None = None,
    ) -> None:
        self.project = project
        self.root = project.entry.root
        self.on_ask_human = on_ask_human
        self.chroma_path = chroma_path
        self._blocked = False
        self._blocked_question: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self._blocked

    def execute(self, name: str, arguments: str | dict) -> str:
        """Вызвать инструмент по имени. Возвращает строку-результат."""
        args: dict = json.loads(arguments) if isinstance(arguments, str) else arguments
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return json.dumps({"error": f"Неизвестный инструмент: {name}"})
        try:
            result = handler(**args)
            return json.dumps(result, ensure_ascii=False, indent=2) if not isinstance(result, str) else result
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── File tools ────────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        p = self.root / path.lstrip("/")
        # Защита от path traversal
        p = p.resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError(f"Путь за пределами проекта: {path}")
        return p

    def _tool_read_file(self, path: str, page: int = 1) -> dict:
        p = self._resolve(path)
        if not p.exists():
            return {"error": f"Файл не найден: {path}"}
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        page_size = 200
        total = max(1, (len(lines) + page_size - 1) // page_size)
        start = (page - 1) * page_size
        return {
            "content": "".join(lines[start : start + page_size]),
            "page": page,
            "total_pages": total,
            "path": path,
        }

    def _tool_write_file(self, path: str, content: str) -> dict:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        h = calc_hash(p)
        return {"written": path, "hash": h, "bytes": len(content.encode())}

    def _tool_list_files(self, directory: str = ".", pattern: str = "*") -> dict:
        d = self._resolve(directory)
        if not d.exists():
            return {"error": f"Директория не найдена: {directory}"}
        files = [str(f.relative_to(self.root)) for f in d.glob(pattern) if f.is_file()]
        return {"files": sorted(files), "count": len(files)}

    def _tool_calc_hash(self, path: str) -> dict:
        p = self._resolve(path)
        if not p.exists():
            return {"error": f"Файл не найден: {path}"}
        return {"path": path, "hash": f"sha:{calc_hash(p)}"}

    def _tool_get_context(self, ref: str, depth: str = "L1") -> dict:
        return get_context(ref, self.root, depth=depth)

    def _tool_update_master_hashes(self) -> dict:
        master = self.project.entry.master_path
        if not master.exists():
            return {"error": "MASTER.md не найден"}
        n, warns = update_hashes(master)
        return {"updated": n, "warnings": warns}

    def _tool_make_ref(self, path: str) -> dict:
        p = self._resolve(path)
        if not p.exists():
            return {"error": f"Файл не найден: {path}"}
        return {"ref": make_ref(p, self.root)}

    # ── Task tools ────────────────────────────────────────────────────────────

    def _tool_create_task(
        self, title: str, description: str = "", priority: int = 5, context_refs: list[str] | None = None
    ) -> dict:
        task = Task(title=title, description=description, priority=priority, context_refs=context_refs or [])
        self.project.add_task(task)
        return {"created": task.id, "title": task.title}

    def _tool_complete_task(self, task_id: str, result: str = "") -> dict:
        t = self.project.update_task(task_id, status=TaskStatus.DONE, result=result)
        if not t:
            return {"error": f"Задача не найдена: {task_id}"}
        return {"done": task_id, "title": t.title}

    def _tool_fail_task(self, task_id: str, reason: str) -> dict:
        t = self.project.update_task(task_id, status=TaskStatus.FAILED, result=reason)
        if not t:
            return {"error": f"Задача не найдена: {task_id}"}
        return {"failed": task_id, "reason": reason}

    # ── Git tools ─────────────────────────────────────────────────────────────

    def _tool_git_commit(self, message: str, files: list[str] | None = None, branch: str | None = None) -> dict:
        root = str(self.root)
        try:
            if branch:
                subprocess.run(["git", "checkout", "-b", branch], cwd=root, check=True, capture_output=True)
            if files:
                subprocess.run(["git", "add"] + files, cwd=root, check=True, capture_output=True)
            else:
                subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
            result = subprocess.run(
                ["git", "commit", "-m", message], cwd=root, capture_output=True, text=True
            )
            if result.returncode != 0:
                return {"error": result.stderr.strip()}
            return {"committed": True, "message": message, "branch": branch}
        except subprocess.CalledProcessError as e:
            return {"error": e.stderr.decode() if e.stderr else str(e)}

    # ── Meta tools ────────────────────────────────────────────────────────────

    def _tool_ask_human(self, question: str, context: str = "") -> dict:
        self._blocked = True
        self._blocked_question = question
        if self.on_ask_human:
            answer = self.on_ask_human(question, context)
            self._blocked = False
            return {"answer": answer}
        return {"blocked": True, "question": question, "context": context}

    def _tool_get_project_status(self) -> dict:
        return {
            "project": self.project.entry.name,
            "stats": self.project.stats(),
            "next_actions": self.project.extract_next_actions(),
        }

    # ── ChromaDB tools ────────────────────────────────────────────────────────

    def _tool_search_documents(self, query: str, n_results: int = 5) -> dict:
        if not self.chroma_path:
            return {"error": "ChromaDB не настроен. Укажите chroma_path в конфиге."}
        try:
            hits = _search_docs(
                query=query,
                chroma_path=self.chroma_path,
                project_root=str(self.root),
                n_results=n_results,
            )
            return {"results": hits, "count": len(hits)}
        except ImportError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"ChromaDB ошибка: {e}"}

    def _tool_reindex_project(self) -> dict:
        if not self.chroma_path:
            return {"error": "ChromaDB не настроен. Укажите chroma_path в конфиге."}
        try:
            result = reindex_project(self.root, self.chroma_path)
            return result
        except ImportError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Reindex ошибка: {e}"}
