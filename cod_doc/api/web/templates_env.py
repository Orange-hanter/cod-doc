"""Jinja2 environment for the web frontend."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from fastapi.templating import Jinja2Templates

from cod_doc.domain.entities import TaskStatus

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "web"
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

TASK_STATUS_OPTIONS: list[str] = [s.value for s in TaskStatus]

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# `urldecode` lets base.html surface percent-encoded flash cookies (which we
# encode at write time because cookie headers are latin-1).
templates.env.filters["urldecode"] = unquote
# Avoid passing the same enum dump from every handler — make it a Jinja global.
templates.env.globals["task_status_options"] = TASK_STATUS_OPTIONS
