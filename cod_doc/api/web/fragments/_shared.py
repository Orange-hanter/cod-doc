"""Cross-fragment helpers — HTMX detection + task-row renderer.

The task-row fragment is shared between status_update and complete handlers
because both paths can return either a fresh row + OOB alert (HTMX) or
redirect with cookie-flash (form post).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError

from cod_doc.api.web.templates_env import templates


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
    if inline_alert is not None:
        severity, message = inline_alert
        alert_html = templates.get_template("_frag/alert.html").render(
            severity=severity, message=message, oob=True
        )
        return HTMLResponse(row_html + alert_html)
    return HTMLResponse(row_html)
