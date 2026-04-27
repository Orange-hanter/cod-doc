"""Bridge: legacy Config-project (slug=name) → DB session + ProjectModel.row_id.

Embedded sqlite lives at `<project_root>/.cod-doc/state.db`. If the file is
absent or the schema isn't migrated, returns `(None, None)` — caller renders
"no DB project yet" instead of crashing.

Web-only helper: lives under `cod_doc.api.web` and is allowed to call
`infra.db` and `ProjectRepository` (read-only lookup), per
`docs/system/capabilities/web-frontend.md §7`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config
from cod_doc.config import ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.repositories import ProjectRepository


@contextmanager
def open_db_for_project(slug: str) -> Iterator[tuple[Session | None, int | None]]:
    """Yield `(session, project_db_id)` or `(None, None)` if unavailable.

    Unavailable means: legacy project missing, `.cod-doc/state.db` absent,
    schema not migrated, or no `ProjectModel` row matching the legacy slug.
    """
    cfg = get_config()
    entry: ProjectEntry | None = cfg.get_project(slug)
    if entry is None:
        yield (None, None)
        return

    db_path = entry.cod_doc_dir / "state.db"
    if not db_path.exists():
        yield (None, None)
        return

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    session = factory()
    try:
        try:
            proj = ProjectRepository(session).get_by_slug(slug)
        except OperationalError:
            yield (None, None)
            return
        if proj is None or proj.row_id is None:
            yield (None, None)
        else:
            yield (session, proj.row_id)
    finally:
        session.close()
        engine.dispose()
