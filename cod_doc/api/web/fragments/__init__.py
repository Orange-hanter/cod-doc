"""HTMX fragment endpoints — return small HTML chunks for swap targets.

Convention: each fragment endpoint accepts both HTMX and non-HTMX form posts.
- HTMX request (`HX-Request: true`) → returns the fragment HTML.
- Regular form post → 303 redirect back to the parent list page (so the user
  sees the new state without an empty <tr> being rendered as a full page).

Exceptional errors (404 unknown task, 400 invalid form value) raise WebError
subclasses; the handler in `cod_doc.api.server` renders an alert fragment
(HTMX) or sets a cookie-flash + redirects (form). Recoverable errors that
also need a row update (conflict on update_status) keep returning the row
fragment AND append an OOB alert in the same response.
"""

from __future__ import annotations

from fastapi import APIRouter

from cod_doc.services import task_service as tasks  # noqa: F401 — preserves

# `cod_doc.api.web.fragments.tasks.update_status` for tests/external
# monkeypatchers that depended on the pre-split module attribute.
from . import sections, tasks_board, tasks_fields, tasks_status

router = APIRouter()
router.include_router(tasks_status.router)
router.include_router(tasks_fields.router)
router.include_router(tasks_board.router)
router.include_router(sections.router)

__all__ = ["router"]
