"""Jinja2 environment for the web frontend."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "web"
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
