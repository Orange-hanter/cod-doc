"""
Управление проектами COD-DOC.
Каждый проект — внешний Git-репозиторий с MASTER.md и .cod-doc/ для состояния агента.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from cod_doc.config import ProjectEntry


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class Task:
    def __init__(
        self,
        title: str,
        description: str = "",
        priority: int = 5,
        task_id: str | None = None,
        status: TaskStatus = TaskStatus.PENDING,
        created: str | None = None,
        updated: str | None = None,
        result: str | None = None,
        context_refs: list[str] | None = None,
    ) -> None:
        self.id = task_id or str(uuid.uuid4())[:8]
        self.title = title
        self.description = description
        self.priority = priority
        self.status = TaskStatus(status)
        self.created = created or datetime.utcnow().isoformat()
        self.updated = updated or self.created
        self.result = result
        self.context_refs: list[str] = context_refs or []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "status": self.status.value,
            "created": self.created,
            "updated": self.updated,
            "result": self.result,
            "context_refs": self.context_refs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            title=d["title"],
            description=d.get("description", ""),
            priority=d.get("priority", 5),
            task_id=d.get("id"),
            status=d.get("status", TaskStatus.PENDING),
            created=d.get("created"),
            updated=d.get("updated"),
            result=d.get("result"),
            context_refs=d.get("context_refs", []),
        )


class Project:
    """
    Представление проекта COD-DOC.
    Данные хранятся в репозитории проекта (.cod-doc/).
    """

    def __init__(self, entry: ProjectEntry) -> None:
        self.entry = entry
        self._tasks_file = entry.cod_doc_dir / "tasks.yaml"
        self._state_file = entry.cod_doc_dir / "state.yaml"

    # ── Init ─────────────────────────────────────────────────────────────────

    def init(self) -> None:
        """Инициализировать .cod-doc/ в репозитории проекта."""
        self.entry.cod_doc_dir.mkdir(parents=True, exist_ok=True)
        if not self._tasks_file.exists():
            self._tasks_file.write_text(yaml.dump({"tasks": []}, allow_unicode=True))
        if not self._state_file.exists():
            self._write_state({"status": "idle", "last_run": None, "agent_context": []})
        if not self.entry.master_path.exists():
            self._create_master()
        # Добавить .cod-doc/ в .gitignore проекта (агент не коммитит своё состояние)
        self._ensure_gitignore()

    def _ensure_gitignore(self) -> None:
        gi = self.entry.root / ".gitignore"
        lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        needed = [".cod-doc/", "__pycache__/", "*.pyc"]
        added = [n for n in needed if n not in lines]
        if added:
            with gi.open("a", encoding="utf-8") as f:
                f.write("\n# COD-DOC\n" + "\n".join(added) + "\n")

    def _create_master(self) -> None:
        from jinja2 import Environment, FileSystemLoader

        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=False)
        tmpl = env.get_template("MASTER.md.j2")
        content = tmpl.render(
            project_name=self.entry.name,
            date=datetime.utcnow().strftime("%Y-%m-%d"),
            repo=self.entry.path,
        )
        self.entry.master_path.write_text(content, encoding="utf-8")

    # ── Tasks ─────────────────────────────────────────────────────────────────

    def _load_tasks(self) -> list[Task]:
        if not self._tasks_file.exists():
            return []
        data = yaml.safe_load(self._tasks_file.read_text(encoding="utf-8")) or {}
        return [Task.from_dict(d) for d in data.get("tasks", [])]

    def _save_tasks(self, tasks: list[Task]) -> None:
        self._tasks_file.write_text(
            yaml.dump({"tasks": [t.to_dict() for t in tasks]}, allow_unicode=True),
            encoding="utf-8",
        )

    def get_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        tasks = self._load_tasks()
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.priority)

    def add_task(self, task: Task) -> Task:
        tasks = self._load_tasks()
        tasks.append(task)
        self._save_tasks(tasks)
        return task

    def update_task(self, task_id: str, **kwargs: Any) -> Task | None:
        tasks = self._load_tasks()
        for t in tasks:
            if t.id == task_id:
                for k, v in kwargs.items():
                    if k == "status":
                        t.status = TaskStatus(v)
                    else:
                        setattr(t, k, v)
                t.updated = datetime.utcnow().isoformat()
                self._save_tasks(tasks)
                return t
        return None

    def next_pending_task(self) -> Task | None:
        tasks = self.get_tasks(TaskStatus.PENDING)
        return tasks[0] if tasks else None

    # ── State ─────────────────────────────────────────────────────────────────

    def _read_state(self) -> dict:
        if not self._state_file.exists():
            return {}
        return yaml.safe_load(self._state_file.read_text(encoding="utf-8")) or {}

    def _write_state(self, state: dict) -> None:
        self._state_file.write_text(yaml.dump(state, allow_unicode=True), encoding="utf-8")

    @property
    def state(self) -> dict:
        return self._read_state()

    def set_status(self, status: str) -> None:
        s = self._read_state()
        s["status"] = status
        s["last_run"] = datetime.utcnow().isoformat()
        self._write_state(s)

    def push_message(self, role: str, content: str) -> None:
        """Добавить сообщение в контекст агента (история диалога)."""
        s = self._read_state()
        ctx = s.get("agent_context", [])
        ctx.append({"role": role, "content": content})
        # Ограничение: последние 50 сообщений
        s["agent_context"] = ctx[-50:]
        self._write_state(s)

    def get_context_messages(self) -> list[dict]:
        return self._read_state().get("agent_context", [])

    def clear_context(self) -> None:
        s = self._read_state()
        s["agent_context"] = []
        self._write_state(s)

    # ── MASTER.md ─────────────────────────────────────────────────────────────

    def read_master(self) -> str | None:
        if self.entry.master_path.exists():
            return self.entry.master_path.read_text(encoding="utf-8")
        return None

    def extract_next_actions(self) -> dict:
        """Парсить блок next_actions из MASTER.md."""
        import json
        import re

        content = self.read_master() or ""
        # Ищем JSON в разделе "Quick Actions"
        m = re.search(r"```json\s*(\{[^`]+\"next_step\"[^`]+\})\s*```", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        return {}

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        tasks = self._load_tasks()
        return {
            "total": len(tasks),
            "pending": sum(1 for t in tasks if t.status == TaskStatus.PENDING),
            "in_progress": sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS),
            "done": sum(1 for t in tasks if t.status == TaskStatus.DONE),
            "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
            "status": self.state.get("status", "unknown"),
            "last_run": self.state.get("last_run"),
        }
