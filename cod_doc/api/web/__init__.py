"""Web frontend (server-rendered Jinja + HTMX)."""

from cod_doc.api.web.fragments import router as fragments_router
from cod_doc.api.web.pages import router as pages_router

__all__ = ["fragments_router", "pages_router"]
