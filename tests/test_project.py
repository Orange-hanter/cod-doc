"""Тесты cod_doc.core.project"""

from pathlib import Path

import pytest

from cod_doc.config import ProjectEntry
from cod_doc.core.project import Project, Task, TaskStatus


@pytest.fixture
def project(tmp_path: Path) -> Project:
    entry = ProjectEntry(name="test-proj", path=str(tmp_path))
    proj = Project(entry)
    proj.init()
    return proj


# ── Task CRUD ─────────────────────────────────────────────────────────────────

def test_add_and_get_task(project: Project) -> None:
    task = Task(title="Написать спецификацию", priority=1)
    project.add_task(task)

    tasks = project.get_tasks()
    assert len(tasks) == 1
    assert tasks[0].title == "Написать спецификацию"
    assert tasks[0].status == TaskStatus.PENDING


def test_tasks_sorted_by_priority(project: Project) -> None:
    project.add_task(Task(title="Low", priority=9))
    project.add_task(Task(title="High", priority=1))
    project.add_task(Task(title="Med", priority=5))

    tasks = project.get_tasks()
    assert [t.priority for t in tasks] == [1, 5, 9]


def test_update_task_status(project: Project) -> None:
    task = Task(title="Do something")
    project.add_task(task)

    updated = project.update_task(task.id, status=TaskStatus.DONE, result="ok")
    assert updated is not None
    assert updated.status == TaskStatus.DONE
    assert updated.result == "ok"


def test_update_task_not_found(project: Project) -> None:
    result = project.update_task("nonexistent-id", status=TaskStatus.DONE)
    assert result is None


def test_next_pending_task(project: Project) -> None:
    project.add_task(Task(title="First", priority=2))
    project.add_task(Task(title="Second", priority=1))
    project.add_task(Task(title="Third", priority=1))

    next_t = project.next_pending_task()
    assert next_t is not None
    assert next_t.priority == 1


def test_next_pending_task_none_when_empty(project: Project) -> None:
    assert project.next_pending_task() is None


def test_filter_tasks_by_status(project: Project) -> None:
    t1 = Task(title="Pending")
    t2 = Task(title="Done")
    project.add_task(t1)
    project.add_task(t2)
    project.update_task(t2.id, status=TaskStatus.DONE)

    pending = project.get_tasks(TaskStatus.PENDING)
    done = project.get_tasks(TaskStatus.DONE)
    assert len(pending) == 1
    assert len(done) == 1


# ── State ─────────────────────────────────────────────────────────────────────

def test_set_status(project: Project) -> None:
    project.set_status("running")
    assert project.state["status"] == "running"


def test_push_and_get_messages(project: Project) -> None:
    project.push_message("user", "Привет")
    project.push_message("assistant", "Ответ")
    msgs = project.get_context_messages()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"


def test_context_trimmed_to_50(project: Project) -> None:
    for i in range(60):
        project.push_message("user", f"msg {i}")
    assert len(project.get_context_messages()) == 50


def test_clear_context(project: Project) -> None:
    project.push_message("user", "test")
    project.clear_context()
    assert project.get_context_messages() == []


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats(project: Project) -> None:
    project.add_task(Task(title="A"))
    project.add_task(Task(title="B"))
    t = project.add_task(Task(title="C"))
    project.update_task(t.id, status=TaskStatus.DONE)

    stats = project.stats()
    assert stats["total"] == 3
    assert stats["pending"] == 2
    assert stats["done"] == 1


# ── Init ──────────────────────────────────────────────────────────────────────

def test_init_creates_cod_doc_dir(tmp_path: Path) -> None:
    entry = ProjectEntry(name="new-proj", path=str(tmp_path))
    proj = Project(entry)
    proj.init()

    assert (tmp_path / ".cod-doc").exists()
    assert (tmp_path / ".cod-doc" / "tasks.yaml").exists()
    assert (tmp_path / ".cod-doc" / "state.yaml").exists()
    assert (tmp_path / "MASTER.md").exists()


def test_init_idempotent(project: Project) -> None:
    project.init()  # второй раз не должен упасть
    assert (project.entry.cod_doc_dir / "tasks.yaml").exists()
