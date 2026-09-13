"""STO-028: демон тикает рутины по той БД, которую резолвит реестр.

`run_daemon` резолвил БД жёстко как `<root>/.cod-doc/state.db`. У проекта в
hub-режиме это значит, что демон внутри `cod-doc serve` вёл журнал рутин в
файл, который никто не читает, параллельно с cron-тиком, писавшим в hub.

Тест зовёт вынесенную из цикла `tick_project_routines` — поднимать
бесконечный `run_daemon` для проверки резолва не нужно.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from cod_doc.agent.orchestrator import tick_project_routines
from cod_doc.config import ProjectEntry
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import routine_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


def _seed(db_path: Path, slug: str, root: Path) -> None:
    """Готовая БД: схема, строка проекта и одна рутина, которая сразу due."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    engine = make_engine(f"sqlite:///{db_path}")
    with transactional(make_session_factory(engine)) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug=slug, title=slug, root_path=str(root), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()
        assert proj.row_id is not None
        routine_service.create(
            session,
            proj.row_id,
            name="probe",
            check_name="alembic_head",
            trigger="cron",
            cron="*/15 * * * *",
            enabled=True,
        )
    engine.dispose()


def _routine_runs(db_path: Path) -> int:
    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM routine_run")).scalar_one())
    finally:
        engine.dispose()


def test_tick_uses_hub_db_and_leaves_embedded_alone(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    hub_db = tmp_path / "hub" / "state.db"
    _seed(hub_db, "hubbed", root)

    entry = ProjectEntry(name="hubbed", path=str(root), db_url=f"sqlite:///{hub_db}")
    messages: list[str] = []
    tick_project_routines(entry, messages.append)

    assert _routine_runs(hub_db) == 1, messages
    assert not (root / ".cod-doc" / "state.db").exists()


def test_tick_uses_embedded_db_when_no_db_url(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    embedded = root / ".cod-doc" / "state.db"
    _seed(embedded, "plain", root)

    entry = ProjectEntry(name="plain", path=str(root))
    messages: list[str] = []
    tick_project_routines(entry, messages.append)

    assert _routine_runs(embedded) == 1, messages


def test_tick_skips_when_db_absent(tmp_path: Path) -> None:
    """Ни падения, ни создания файла, если БД проекта ещё не заведена."""
    root = tmp_path / "repo"
    root.mkdir()
    entry = ProjectEntry(name="bare", path=str(root))
    messages: list[str] = []

    tick_project_routines(entry, messages.append)

    assert not (root / ".cod-doc" / "state.db").exists()
    assert messages == []
