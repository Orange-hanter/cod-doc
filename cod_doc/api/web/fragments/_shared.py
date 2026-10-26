"""Cross-fragment helpers — HTMX detection + task-row renderer.

The task-row fragment is shared between status_update and complete handlers
because both paths can return either a fresh row + OOB alert (HTMX) or
redirect with cookie-flash (form post).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError

from cod_doc.api.web.task_detail import task_changed_header, task_head_context
from cod_doc.api.web.templates_env import templates

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _log_write_failure(logger: logging.Logger, what: str, task_id: str, exc: Exception) -> None:
    """Записать сбой web-записи без SQL и значений параметров.

    ``str(IntegrityError)`` несёт текст запроса и параметры, а сообщение
    Postgres — ещё и ``DETAIL`` со значениями ключа. На WARNING уходят тип
    исключения и имя нарушенного ограничения (у SQLite его заменяет первая
    строка сообщения драйвера: там только таблица и колонки); полный
    traceback — на DEBUG, для отладки на своей машине.
    """
    cause: BaseException = exc
    constraint = None
    if isinstance(exc, IntegrityError) and exc.orig is not None:
        cause = exc.orig
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        if constraint is None:
            constraint = next(iter(str(cause).splitlines()), None)
    logger.warning(
        "%s failed for %s: %s (constraint=%s)",
        what,
        task_id,
        type(cause).__name__,
        constraint,
    )
    logger.debug("%s failed for %s", what, task_id, exc_info=exc)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _render_task_row(
    request: Request,
    *,
    project_name: str,
    task: Any,
    inline_alert: tuple[str, str] | None = None,
) -> HTMLResponse:
    """Render the task row, optionally followed by an OOB alert fragment.

    `inline_alert`, when set, is `(severity, message)` and produces an
    additional `<div class='alert' hx-swap-oob>` block appended to the row
    HTML. HTMX picks the OOB block separately and lands it in `#alerts`.
    """
    row_html = templates.get_template("_frag/task_row.html").render(
        project={"name": project_name},
        t={
            "task_id": task.task_id,
            "title": task.title,
            "type": task.type.value,
            "status": task.status.value,
            "priority": task.priority.value,
            "plan_id": task.plan_id,
            "section_id": task.section_id,
        },
    )
    return HTMLResponse(row_html + _render_oob_alert(inline_alert))


def _render_oob_alert(inline_alert: tuple[str, str] | None) -> str:
    if inline_alert is None:
        return ""
    severity, message = inline_alert
    return templates.get_template("_frag/alert.html").render(
        severity=severity, message=message, oob=True
    )


def _render_task_head(
    request: Request,
    *,
    session: Session,
    project_db_id: int,
    project_name: str,
    task: Any,
    inline_alert: tuple[str, str] | None = None,
    repo_url: str | None = None,
) -> HTMLResponse:
    """Шапка задачи вместо строки — ответ контролам карточки (`view=detail`).

    Контролы шапки живут на странице задачи и в шторке (WUI-001); строки
    таблицы там нет, и `<tr>` из `_render_task_row` htmx вставил бы внутрь
    самого `<select>`. Удачная запись дополнительно шлёт `HX-Trigger`: по нему
    шторка перечитает список под собой при закрытии. Отказ задачу не менял —
    событие не нужно, только алерт.
    """
    head_html = templates.get_template("_frag/task_head.html").render(
        request=request,
        project={"name": project_name},
        **task_head_context(session, project_db_id, task, repo_url=repo_url),
    )
    response = HTMLResponse(head_html + _render_oob_alert(inline_alert))
    if inline_alert is None:
        response.headers.update(task_changed_header(task.task_id))
    return response
