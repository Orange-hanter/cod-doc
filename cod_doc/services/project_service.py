"""ProjectService — bootstrap a project's DB.

Wraps the bootstrap sequence so that Web UI / CLI / tests can all do the
same thing:

1. Ensure the on-disk `.cod-doc/` folder + `tasks.yaml` + `state.yaml` +
   `MASTER.md` exist (idempotent — see `cod_doc.core.project.Project.init`).
2. Run Alembic migrations against the DB the entry actually resolves to —
   embedded SQLite (creates `state.db` on first run) или hub-БД из
   `db_url` реестра (STO-027).
3. Insert a `ProjectModel` row whose `slug` matches the legacy config name
   so `try_open_project_db` can map slug → DB row.

Step 2 is done programmatically via `alembic.command.upgrade` — no shell-out,
no `alembic.ini` required at runtime. Migration scripts ship inside the
package under `cod_doc/infra/migrations/versions/*.py` and are picked up
automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import TYPE_CHECKING

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import db_for_entry, db_url_for_entry, sqlite_file_path, transactional
from cod_doc.infra.repositories import ProjectRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.config import ProjectEntry


@dataclass(slots=True)
class InitResult:
    """What `init_project` actually did, for surfacing in the UI."""

    db_existed: bool
    db_row_existed: bool
    files_created: bool


def _alembic_config_for(db_url: str) -> AlembicConfig:
    """Build an in-memory Alembic config pointing at our packaged migrations."""
    cfg = AlembicConfig()
    scripts_path = files("cod_doc.infra.migrations")
    cfg.set_main_option("script_location", str(scripts_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _bootstrap_default_routines(session: Session, project_id: int) -> None:
    """PCA-914: Idempotently create default routines for a project.

    Default routines:
    - approval_stale (every 15 min): auto-expire pending approvals past expires_at.

    Uses an explicit existence check (instead of try/except + UniqueConstraint)
    because IntegrityError invalidates the surrounding session.
    """
    from sqlalchemy import select

    from cod_doc.infra.models import RoutineModel
    from cod_doc.services import routine_service

    existing = session.execute(
        select(RoutineModel.row_id).where(
            RoutineModel.project_id == project_id,
            RoutineModel.name == "approval_stale_default",
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    routine_service.create(
        session,
        project_id=project_id,
        name="approval_stale_default",
        check_name="approval_stale",
        trigger="cron",
        cron="*/15 * * * *",
        on_finding="comment_only",
        enabled=True,
    )


def init_project(entry: ProjectEntry) -> InitResult:
    """Idempotent bootstrap. Safe to call repeatedly.

    Returns an InitResult so callers can render a meaningful confirmation
    («DB already initialised», «Created from scratch», etc.).
    """
    # 1. Local files (tasks.yaml, state.yaml, MASTER.md, .cod-doc/, .gitignore)
    files_existed = entry.cod_doc_dir.exists() and (entry.cod_doc_dir / "tasks.yaml").exists()
    Project(entry).init()
    files_created = not files_existed

    # 2. Alembic upgrade. `state.db` is created by sqlite on first connection
    # by the alembic engine — even if absent before, this just creates it.
    # STO-027: мигрируем ту БД, которую откроет `db_for_entry` на шаге 3.
    # В hub-режиме (`db_url` в реестре) она лежит вне рабочего дерева; резолв
    # по embedded-пути создавал лишний пустой `.cod-doc/state.db` и ронял
    # шаг 3 на сверке alembic-головы ненакатанного hub'а.
    db_url = db_url_for_entry(entry)
    db_path = sqlite_file_path(db_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    # Нефайловую БД (postgres) мы не создаём — она существует до init.
    db_existed = db_path.exists() if db_path is not None else True
    cfg = _alembic_config_for(db_url)
    alembic_command.upgrade(cfg, "head")

    # 3. ProjectModel row keyed by slug=entry.name (so `try_open_project_db`
    # can resolve URL-slug → DB row).
    factory, engine = db_for_entry(entry)
    db_row_existed: bool
    with transactional(factory) as session:
        existing = ProjectRepository(session).get_by_slug(entry.name)
        db_row_existed = existing is not None
        if existing is None:
            now = datetime.now(UTC)
            row = ProjectRepository(session).add(
                ProjectEntity(
                    slug=entry.name,
                    title=entry.name,
                    root_path=str(entry.root),
                    config={},
                )
            )
            row.created = now
            row.updated = now
            project_id = row.row_id
        else:
            project_id = existing.row_id

        # PCA-914: bootstrap default approval_stale routine (every 15 min).
        # Idempotent — uses unique (project_id, name) constraint.
        if project_id is not None:
            _bootstrap_default_routines(session, project_id)
    engine.dispose()

    return InitResult(
        db_existed=db_existed,
        db_row_existed=db_row_existed,
        files_created=files_created,
    )
