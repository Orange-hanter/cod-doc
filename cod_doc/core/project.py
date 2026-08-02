"""
Управление проектами COD-DOC.
Каждый проект — внешний Git-репозиторий с MASTER.md и .cod-doc/ для состояния агента.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

import yaml

if TYPE_CHECKING:
    from cod_doc.config import ProjectEntry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskStatus(StrEnum):
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
        blocked_by: list[str] | None = None,
        affects_files: list[str] | None = None,
        acceptance: str | None = None,
        story_id: str | None = None,
    ) -> None:
        self.id = task_id or str(uuid.uuid4())[:8]
        self.title = title
        self.description = description
        self.priority = priority
        self.status = TaskStatus(status)
        self.created = created or _now()
        self.updated = updated or self.created
        self.result = result
        self.context_refs: list[str] = context_refs or []
        self.blocked_by: list[str] = blocked_by or []
        self.affects_files: list[str] = affects_files or []
        self.acceptance: str | None = acceptance
        self.story_id: str | None = story_id

    def to_dict(self) -> dict[str, Any]:
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
            "blocked_by": self.blocked_by,
            "affects_files": self.affects_files,
            "acceptance": self.acceptance,
            "story_id": self.story_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Task:
        blocked_by: list[str] = d.get("blocked_by", [])
        # C2: if no explicit blocked_by, extract IDs from description text
        if not blocked_by:
            import re

            desc = d.get("description", "")
            for pattern in (
                r"[Зз]ависит\s+от[:\s]+([^\n]+)",
                r"[Bb]locked\s+by[:\s]+([^\n]+)",
                r"[Dd]epends\s+on[:\s]+([^\n]+)",
            ):
                m = re.search(pattern, desc)
                if m:
                    raw = m.group(1)
                    # Match known ID formats: 8-char hex, COD-NNN, T-NNN, US-NNN, A1..Z9
                    blocked_by = re.findall(
                        r"`([0-9a-f]{8})`|([A-Z]+-\d+)|([A-Za-z]\d+(?=[^\w]|$))", raw
                    )
                    # Flatten non-empty groups
                    blocked_by = [g for tup in blocked_by for g in tup if g]
                    break

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
            blocked_by=blocked_by,
            affects_files=d.get("affects_files", []),
            acceptance=d.get("acceptance"),
            story_id=d.get("story_id"),
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
        from importlib.resources import files

        from jinja2 import BaseLoader, Environment

        tmpl_text = files("cod_doc.templates").joinpath("MASTER.md.j2").read_text(encoding="utf-8")
        env = Environment(loader=BaseLoader(), autoescape=False)
        tmpl = env.from_string(tmpl_text)
        content = tmpl.render(
            project_name=self.entry.name,
            date=datetime.now(UTC).strftime("%Y-%m-%d"),
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
        archived = self._tasks_file.with_suffix(".archived.yaml")
        if archived.exists():
            raise RuntimeError(
                "Legacy tasks.yaml has been archived. Use the DB-backed task_create tool instead."
            )
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
                t.updated = _now()
                self._save_tasks(tasks)
                return t
        return None

    def next_pending_task(self) -> Task | None:
        # PCA-923: accept both 'pending' (legacy) and 'todo' (new taxonomy).
        pending = self.get_tasks(TaskStatus.PENDING)
        if pending:
            return pending[0]
        # 'todo' is the canonical new-taxonomy equivalent of 'pending'.
        try:
            todo_status = TaskStatus("todo")
            todo = self.get_tasks(todo_status)
            return todo[0] if todo else None
        except ValueError:
            return None

    # ── State ─────────────────────────────────────────────────────────────────

    def _read_state(self) -> dict[str, Any]:
        if not self._state_file.exists():
            return {}
        return yaml.safe_load(self._state_file.read_text(encoding="utf-8")) or {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self._state_file.write_text(yaml.dump(state, allow_unicode=True), encoding="utf-8")

    @property
    def state(self) -> dict[str, Any]:
        return self._read_state()

    def set_status(self, status: str) -> None:
        s = self._read_state()
        s["status"] = status
        s["last_run"] = _now()
        self._write_state(s)

    def push_message(self, role: str, content: str) -> None:
        s = self._read_state()
        ctx = s.get("agent_context", [])
        ctx.append({"role": role, "content": content})
        s["agent_context"] = ctx[-50:]
        self._write_state(s)

    def get_context_messages(self) -> list[dict[str, Any]]:
        messages = self._read_state().get("agent_context", [])
        return list(messages)

    def clear_context(self) -> None:
        s = self._read_state()
        s["agent_context"] = []
        self._write_state(s)

    # ── MASTER.md ─────────────────────────────────────────────────────────────

    def read_master(self) -> str | None:
        if self.entry.master_path.exists():
            return self.entry.master_path.read_text(encoding="utf-8")
        return None

    def extract_next_actions(self) -> dict[str, Any]:
        import json
        import re

        content = self.read_master() or ""
        m = re.search(r"```json\s*(\{[^`]+\"next_step\"[^`]+\})\s*```", content, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(1))
                if isinstance(parsed, dict):
                    return cast("dict[str, Any]", parsed)
            except json.JSONDecodeError:
                pass
        return {}

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
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

    @staticmethod
    def batch_stats(
        entries: list[ProjectEntry],
        *,
        max_workers: int = 8,
    ) -> list[dict[str, Any]]:
        """Read `stats()` for every entry in parallel via a thread pool.

        File I/O releases the GIL, so threads give a meaningful speed-up over
        the previous N×sequential pattern in `pages.py:index()`. Each call
        creates a short-lived pool sized to `min(max_workers, len(entries))`.
        Output preserves input order so callers can zip with `entries`.
        """
        from concurrent.futures import ThreadPoolExecutor

        if not entries:
            return []
        workers = min(max_workers, len(entries))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(lambda e: Project(e).stats(), entries))
