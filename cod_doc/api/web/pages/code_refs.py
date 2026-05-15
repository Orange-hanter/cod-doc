"""OBI-021: code-refs panel + on-demand file preview endpoint.

Two routes:
- ``GET /p/<slug>/code-refs/preview?path=...`` — first 20 lines of a file
  under the project root, returned as JSON for hover-on-row preview.
- ``GET /p/<slug>/code-refs`` — standalone index page listing every
  ``link.kind='code'`` row in the project. Same panel template is used
  embedded in task/document pages later.

Path safety: every request is resolved relative to project root and
checked against directory traversal. Anything outside the root → 400.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.infra.models import LinkModel

router = APIRouter()


_PREVIEW_LINES = 20


def _safe_resolve(root: Path, rel: str) -> Path:
    """Return ``root/rel`` if it stays inside ``root``; raise 400 otherwise."""
    try:
        target = (root / rel).resolve()
        root_resolved = root.resolve()
    except OSError as exc:
        raise HTTPException(400, f"invalid path: {exc}") from exc
    if not str(target).startswith(str(root_resolved)):
        raise HTTPException(400, f"path escapes project root: {rel!r}")
    return target


@router.get("/p/{slug}/code-refs", response_class=HTMLResponse)
def code_refs_index(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Standalone page listing all parsed code-refs in the project."""
    proj = get_project(slug)
    session, project_id = db

    rows = list(session.execute(
        select(LinkModel)
        .where(LinkModel.project_id == project_id, LinkModel.kind == "code")
        .order_by(LinkModel.to_file_path, LinkModel.to_symbol)
    ).scalars())

    items = [
        {
            "file_path": r.to_file_path or "",
            "symbol": r.to_symbol or None,
            "resolved": r.resolved,
            "broken_reason": r.broken_reason,
            "raw": r.raw,
            "from_section_id": r.from_section_id,
        }
        for r in rows
    ]

    # Group by file_path for cleaner display.
    by_file: dict[str, list[dict]] = {}
    for it in items:
        by_file.setdefault(it["file_path"] or "(missing path)", []).append(it)
    grouped = sorted(
        ({"file_path": fp, "refs": refs} for fp, refs in by_file.items()),
        key=lambda x: x["file_path"],
    )

    return templates.TemplateResponse(
        request,
        "project/code_refs_list.html",
        {
            "project": proj.entry,
            "grouped": grouped,
            "total": len(items),
            "resolved_count": sum(1 for it in items if it["resolved"]),
            "broken_count": sum(1 for it in items if not it["resolved"]),
        },
    )


@router.get("/p/{slug}/code-refs/preview", response_class=JSONResponse)
def code_refs_preview(
    slug: str,
    path: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> JSONResponse:
    """Return the first ``_PREVIEW_LINES`` lines of ``path`` (relative to project root).

    Used by the panel's hover-preview tooltip. Empty array if the file
    is missing or unreadable.
    """
    proj = get_project(slug)
    root = Path(proj.entry.path)
    target = _safe_resolve(root, path)
    if not target.is_file():
        return JSONResponse({"path": path, "lines": [], "missing": True})
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            lines: list[str] = []
            for i, line in enumerate(f):
                if i >= _PREVIEW_LINES:
                    break
                lines.append(line.rstrip("\n"))
    except OSError as exc:
        raise HTTPException(500, f"read error: {exc}") from exc
    return JSONResponse({"path": path, "lines": lines, "missing": False})
