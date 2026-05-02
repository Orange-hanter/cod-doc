"""DEPRECATED shim — forwards to cod_doc.api.deps.try_open_project_db.

Kept temporarily so existing pages.py / fragments.py keep working until
WEB-040 migrates them to the FastAPI `Depends(get_project_db)` pattern.
After WEB-040 this module is removed entirely.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from cod_doc.api.deps import try_open_project_db


@contextmanager
def open_db_for_project(slug: str) -> Iterator[tuple[Session | None, int | None]]:
    """DEPRECATED: forwards to deps.try_open_project_db. Removed in WEB-040."""
    with try_open_project_db(slug) as result:
        yield result
