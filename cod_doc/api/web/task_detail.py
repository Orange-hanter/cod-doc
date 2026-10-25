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
from cod_doc.domain.entities import EntityKind, QuestionLinkKind, Task
from cod_doc.services import plan_service as plans
from cod_doc.services import question_service
from cod_doc.services import revision_service as revisions
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


def task_head_context(session: Session, project_db_id: int, task: Task) -> dict[str, Any]:
    """Поля шапки: hero (статус, приоритет, тип, план, коммит) и действия."""
    plan = plans.get_for_project(session, task.plan_id, project_id=project_db_id)
    return {
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


def task_detail_context(session: Session, project_db_id: int, task: Task) -> dict[str, Any]:
    """Всё тело карточки: шапка, поля, цепочки зависимостей, трасса, ревизии."""
    assert task.row_id is not None
    forward = plans.forward_chain(session, task.task_id, project_id=project_db_id)
    reverse = plans.reverse_chain(session, task.task_id, project_id=project_db_id)
    history = revisions.list_for_entity(
        session, EntityKind.TASK, task.row_id, project_id=project_db_id
    )
    trace = traces.list_for_task(session, task.row_id)
    head = task_head_context(session, project_db_id, task)
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
