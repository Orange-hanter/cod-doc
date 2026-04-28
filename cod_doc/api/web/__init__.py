"""Web frontend (server-rendered Jinja + HTMX)."""

from cod_doc.api.web.fragments import router as fragments_router
from cod_doc.api.web.pages import router as pages_router

__all__ = ["pages_router", "fragments_router"]
