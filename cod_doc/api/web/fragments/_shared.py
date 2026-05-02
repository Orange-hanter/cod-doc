"""Cross-fragment helpers — HTMX detection + task-row renderer.

The task-row fragment is shared between status_update and complete handlers
because both paths can return either a fresh row + OOB alert (HTMX) or
redirect with cookie-flash (form post).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from cod_doc.api.web.templates_env import templates


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
