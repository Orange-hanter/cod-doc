"""Контекст карточки задачи — общий для страницы, шторки и фрагментов.

Шапку задачи (`_frag/task_head.html`) рендерят три места: полная страница,
шторка (WUI-001) и ответы `/status` и `/complete` с `view=detail`. Чтобы
ответ эндпоинта не разошёлся со страницей, контекст собирается здесь, один
раз, а не в каждом обработчике.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from cod_doc.api.web.markdown import render_markdown
from cod_doc.domain.entities import EntityKind, QuestionLinkKind, Task, TaskStatus
from cod_doc.services import plan_service as plans
from cod_doc.services import question_service
from cod_doc.services import revision_service as revisions
from cod_doc.services import task_service as tasks
from cod_doc.services import trace_service as traces

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Событие для htmx: «задача изменилась». Шторка по нему помечает список под
# собой устаревшим и перечитывает его при закрытии.
TASK_CHANGED_EVENT = "cod_doc:task-changed"

# Длина первой строки диффа в таблице ревизий.
_DIFF_PREVIEW_CHARS = 240


def task_changed_header(task_id: str) -> dict[str, str]:
    """Заголовок `HX-Trigger`, который сообщает клиенту об изменении задачи.

    В полезной нагрузке нет ключа `target`: htmx по нему перенаправил бы
    событие на другой элемент, а слушатель шторки ждёт его на `document`.
    """
    return {"HX-Trigger": json.dumps({TASK_CHANGED_EVENT: {"task_id": task_id}})}


def _is_gitlab(repo_url: str) -> bool:
    return "gitlab" in repo_url.split("/")[2]


def commit_url(repo_url: str | None, sha: str | None) -> str | None:
    """Коммит на хостинге репозитория (ADO-148); без `repo_url` — None."""
    if not repo_url or not sha:
        return None
    return f"{repo_url}{'/-' if _is_gitlab(repo_url) else ''}/commit/{sha}"


def pr_url(repo_url: str | None, pr: int | None) -> str | None:
    """PR (GitHub) или MR (GitLab) по номеру; без `repo_url` — None."""
    if not repo_url or not pr:
        return None
    if _is_gitlab(repo_url):
        return f"{repo_url}/-/merge_requests/{pr}"
    return f"{repo_url}/pull/{pr}"


def file_url(repo_url: str | None, ref: str | None, path: str) -> str | None:
    """Файл в репозитории на коммите закрытия (иначе — на HEAD ветки по умолчанию)."""
    if not repo_url:
        return None
    return (
        f"{repo_url}{'/-' if _is_gitlab(repo_url) else ''}/blob/{ref or 'HEAD'}/{path.lstrip('/')}"
    )


def task_head_context(
    session: Session,
    project_db_id: int,
    task: Task,
    *,
    repo_url: str | None = None,
) -> dict[str, Any]:
    """Поля шапки: hero (статус, приоритет, тип, план, коммит, PR) и действия.

    ADO-148: чип коммита — ссылка на хостинг, когда у проекта задан
    `repo_url`; PR берётся из закрывающей ревизии (`completion_record`).
    """
    plan = plans.get_for_project(session, task.plan_id, project_id=project_db_id)
    completion = (
        tasks.completion_record(session, project_id=project_db_id, task_row_id=task.row_id)
        if task.status is TaskStatus.DONE and task.row_id is not None
        else None
    )
    pr = completion.pr if completion else None
    return {
        "links": {
            "commit_url": commit_url(repo_url, task.completed_commit),
            "pr": pr,
            "pr_url": pr_url(repo_url, pr),
        },
        "task": {
            "task_id": task.task_id,
            "title": task.title,
            "type": task.type.value,
            "status": task.status.value,
            "priority": task.priority.value,
            "completed_commit": task.completed_commit,
        },
        "plan": (
            {"plan_id": plan.row_id, "scope": plan.scope}
            if plan and plan.row_id is not None
            else None
        ),
    }


def task_detail_context(
    session: Session,
    project_db_id: int,
    task: Task,
    *,
    repo_url: str | None = None,
) -> dict[str, Any]:
    """Всё тело карточки: шапка, поля, итог закрытия, файлы, цепочки, трасса, ревизии."""
    assert task.row_id is not None
    forward = plans.forward_chain(session, task.task_id, project_id=project_db_id)
    reverse = plans.reverse_chain(session, task.task_id, project_id=project_db_id)
    history = revisions.list_for_entity(
        session, EntityKind.TASK, task.row_id, project_id=project_db_id
    )
    trace = traces.list_for_task(session, task.row_id)
    head = task_head_context(session, project_db_id, task, repo_url=repo_url)
    completion = (
        tasks.completion_record(session, project_id=project_db_id, task_row_id=task.row_id)
        if task.status is TaskStatus.DONE
        else None
    )
    return {
        **head,
        "task": {
            **head["task"],
            "description": task.description or "",
            "description_html": render_markdown(task.description or ""),
            "acceptance": task.acceptance or "",
            "acceptance_html": render_markdown(task.acceptance or ""),
            "plan_id": task.plan_id,
            "section_id": task.section_id,
            "created": task.created,
            "last_updated": task.last_updated,
            "completed_at": task.completed_at,
        },
        # ADO-148: «что сделано» — итог из закрывающей ревизии, отдельно от
        # исходной постановки; и файлы, которые задача объявила затронутыми.
        "completion": (
            {
                "summary_html": render_markdown(completion.summary),
                "author": completion.author,
                "at": completion.at,
            }
            if completion and completion.summary
            else None
        ),
        "affected_files": [
            {"path": path, "url": file_url(repo_url, task.completed_commit, path)}
            for path in tasks.affected_files(session, task.row_id)
        ],
        "linked_questions": question_service.linked_questions(
            session, project_db_id, target_kind=QuestionLinkKind.TASK, target_ref=task.task_id
        ),
        "forward": [
            {"task_id": e.task_id, "title": e.title, "status": e.status.value, "depth": e.depth}
            for e in forward
        ],
        "reverse": [
            {"task_id": e.task_id, "title": e.title, "status": e.status.value, "depth": e.depth}
            for e in reverse
        ],
        "history": [
            {
                "revision_id": r.revision_id,
                "author": r.author,
                "at": r.at,
                "reason": r.reason or "",
                "diff_first_line": (
                    (r.diff or "").splitlines()[0][:_DIFF_PREVIEW_CHARS] if r.diff else ""
                ),
            }
            for r in reversed(history)  # newest first for the timeline
        ],
        "trace": [
            {
                "ts": t.ts,
                "model": t.model,
                "kind": t.kind,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "total_tokens": t.total_tokens,
                "duration_ms": t.duration_ms,
                "tool_calls": t.tool_calls or [],
                "error": t.error,
            }
            for t in trace
        ],
        "trace_totals": {
            "calls": len(trace),
            "input_tokens": sum(t.input_tokens for t in trace),
            "output_tokens": sum(t.output_tokens for t in trace),
            "duration_ms": sum(t.duration_ms for t in trace),
        },
    }
