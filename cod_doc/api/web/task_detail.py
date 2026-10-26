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
from cod_doc.api.web.prose_links import ProseLinks
from cod_doc.api.web.repo_links import commit_url, file_url, pr_url
from cod_doc.domain.entities import EntityKind, QuestionLinkKind, Task, TaskStatus
from cod_doc.services import plan_service as plans
from cod_doc.services import question_service
from cod_doc.services import revision_service as revisions
from cod_doc.services import task_service as tasks
from cod_doc.services import trace_service as traces

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.services.task_service import CompletionRecord

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


# «Итог не передан» — отличие от «закрытия нет» (None) для task_head_context.
_UNSET: Any = object()


def prose_links(
    session: Session,
    project_db_id: int,
    *,
    slug: str | None,
    repo_url: str | None,
    root: Path | None,
    texts: Iterable[str | None],
) -> Callable[[str], str]:
    """Переписчик отрисованной прозы карточки (ADO-142); без `slug` — как есть."""
    if slug is None:
        return lambda html: html
    return ProseLinks.build(
        session, project_db_id, slug=slug, repo_url=repo_url, root=root, texts=texts
    ).apply


def _completion(session: Session, project_db_id: int, task: Task) -> CompletionRecord | None:
    if task.status is not TaskStatus.DONE or task.row_id is None:
        return None
    return tasks.completion_record(session, project_id=project_db_id, task_row_id=task.row_id)


def task_head_context(
    session: Session,
    project_db_id: int,
    task: Task,
    *,
    repo_url: str | None = None,
    completion: CompletionRecord | None = _UNSET,
) -> dict[str, Any]:
    """Поля шапки: hero (статус, приоритет, тип, план, коммит, PR) и действия.

    ADO-148: чип коммита — ссылка на хостинг, когда у проекта задан
    `repo_url`; PR берётся из закрывающей ревизии (`completion_record`).
    `completion` уже прочитан вызывающим (тело карточки) — второй раз не читаем.
    """
    plan = plans.get_for_project(session, task.plan_id, project_id=project_db_id)
    if completion is _UNSET:
        completion = _completion(session, project_db_id, task)
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
    slug: str | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Всё тело карточки: шапка, поля, итог закрытия, файлы, цепочки, трасса, ревизии.

    ADO-142: в описании, acceptance и итоге ключи сущностей, `#N`, пути к
    файлам и адреса становятся ссылками (`ProseLinks`), если `slug` задан.
    """
    assert task.row_id is not None
    forward = plans.forward_chain(session, task.task_id, project_id=project_db_id)
    reverse = plans.reverse_chain(session, task.task_id, project_id=project_db_id)
    history = revisions.list_for_entity(
        session, EntityKind.TASK, task.row_id, project_id=project_db_id
    )
    trace = traces.list_for_task(session, task.row_id)
    completion = _completion(session, project_db_id, task)
    prose = prose_links(
        session,
        project_db_id,
        slug=slug,
        repo_url=repo_url,
        root=project_root,
        texts=(task.description, task.acceptance, completion.summary if completion else None),
    )
    head = task_head_context(session, project_db_id, task, repo_url=repo_url, completion=completion)
    return {
        **head,
        "task": {
            **head["task"],
            "description": task.description or "",
            "description_html": prose(render_markdown(task.description or "")),
            "acceptance": task.acceptance or "",
            "acceptance_html": prose(render_markdown(task.acceptance or "")),
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
                "summary_html": prose(render_markdown(completion.summary)),
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
