"""Две реплики одного проекта и file-remote в tmp — общая обвязка CLO-012/013.

Реплика A заводит проект через фабрику с захватом: строки проекта, плана и
секции плана уезжают её первым push'ем. Реплика B — отдельная БД, в которой
проект заведён голой ``Session`` (мимо захвата) с тем же ``project.uid``: так
выглядела бы реплика после clone (CLO-029), только без снапшота.

Сравнение состояния — по ``sync_capture.shipped_fields``: ровно те колонки,
которые уезжают, FK — uid конца. ``row_id`` и local-колонки в сравнение не
входят: они у реплик разные по построению.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import cod_doc.infra.models.link_suggestions  # noqa: F401 — в metadata, как у apply
from cod_doc.domain.entities import DocumentStatus, DocumentType, Priority, Sensitivity, TaskType
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    Base,
    DocumentModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.infra.remote.file import FileRemote
from cod_doc.services import doc_service, task_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Engine, Table
    from sqlalchemy.orm import sessionmaker

AUTHOR = "human:dakh"
SLUG = "demo"


@dataclass
class Replica:
    name: str
    sf: sessionmaker[Session]
    engine: Engine
    db_path: Path
    project_id: int
    plan_id: int
    section_id: int


def _db(tmp_path: Path, name: str) -> tuple[sessionmaker[Session], Engine, Path]:
    path = tmp_path / f"{name}.db"
    url = f"sqlite:///{path}"
    run_alembic("upgrade", "head", db_url=url)
    engine = make_engine(url)
    return make_session_factory(engine), engine, path


def replica_a(tmp_path: Path) -> Replica:
    sf, engine, path = _db(tmp_path, "a")
    now = datetime.now(UTC)
    with transactional(sf) as s:
        cap.set_author(s, AUTHOR)
        proj = ProjectModel(slug=SLUG, title="Demo", root_path="/tmp/a", config_json={})
        proj.created = now
        proj.updated = now
        s.add(proj)
        s.flush()
        plan = PlanModel(project_id=proj.row_id, scope="demo-plan", created=now, last_updated=now)
        s.add(plan)
        s.flush()
        sec = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
        )
        s.add(sec)
        s.flush()
        ids = (proj.row_id, plan.row_id, sec.row_id)
    return Replica("a", sf, engine, path, *ids)


def replica_b(tmp_path: Path, a: Replica, name: str = "b") -> Replica:
    """Вторая реплика: тот же ``project.uid``, плана и секции нет — приедут pull'ом."""
    sf, engine, path = _db(tmp_path, name)
    with transactional(a.sf) as s:
        uid = s.get_one(ProjectModel, a.project_id).uid
    with Session(engine) as s:  # мимо захвата: своего хвоста у B нет
        proj = ProjectModel(
            slug=SLUG, title="Demo", root_path=f"/tmp/{name}", config_json={}, uid=uid
        )
        now = datetime.now(UTC)
        proj.created = now
        proj.updated = now
        s.add(proj)
        s.commit()
        project_id = proj.row_id
    return Replica(name, sf, engine, path, project_id, 0, 0)


def remote(tmp_path: Path) -> FileRemote:
    root = tmp_path / "remote"
    root.mkdir(exist_ok=True)
    return FileRemote(root)


def plan_ids(r: Replica) -> tuple[int, int]:
    """План и секция плана на реплике (у B — после pull)."""
    with transactional(r.sf) as s:
        plan = s.scalars(select(PlanModel).where(PlanModel.project_id == r.project_id)).one()
        sec = s.scalars(select(PlanSectionModel).where(PlanSectionModel.plan_id == plan.row_id))
        return plan.row_id, sec.one().row_id


def make_task(r: Replica, task_id: str, *, description: str = "old") -> None:
    plan_id, section_id = plan_ids(r)
    with transactional(r.sf) as s:
        task_service.create(
            s,
            project_id=r.project_id,
            plan_id=plan_id,
            section_id=section_id,
            task_id=task_id,
            title=f"Task {task_id}",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author=AUTHOR,
            description=description,
        )


def set_description(r: Replica, task_id: str, text: str) -> None:
    with transactional(r.sf) as s:
        task_service.update_description(
            s, project_id=r.project_id, task_id=task_id, new_description=text, author=AUTHOR
        )


def make_doc(r: Replica, doc_key: str, sections: dict[str, str]) -> None:
    with transactional(r.sf) as s:
        doc = doc_service.create(
            s,
            project_id=r.project_id,
            doc_key=doc_key,
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title=doc_key,
            author=AUTHOR,
            path=f"docs/{doc_key}.md",
            sensitivity=Sensitivity.INTERNAL,
            reindex=False,
        )
        for i, (anchor, body) in enumerate(sections.items()):
            doc_service.add_section(
                s,
                project_id=r.project_id,
                document_id=doc.row_id,
                anchor=anchor,
                heading=anchor.title(),
                level=2,
                position=i,
                body=body,
                author=AUTHOR,
                reindex=False,
            )


def doc_id(r: Replica, doc_key: str) -> int:
    with transactional(r.sf) as s:
        return s.scalars(
            select(DocumentModel.row_id).where(
                DocumentModel.project_id == r.project_id, DocumentModel.doc_key == doc_key
            )
        ).one()


def task_description(r: Replica, task_id: str) -> str | None:
    with transactional(r.sf) as s:
        return s.scalars(
            select(TaskModel.description).where(
                TaskModel.project_id == r.project_id, TaskModel.task_id == task_id
            )
        ).one_or_none()


def pending(r: Replica) -> int:
    with transactional(r.sf) as s:
        return (
            s.scalar(
                select(func.count()).select_from(SyncOpModel).where(SyncOpModel.changeset.is_(None))
            )
            or 0
        )


def authored_state(r: Replica) -> dict[str, list[str]]:
    """Уезжающее состояние реплики: строки каждой захватываемой таблицы, FK как uid."""
    out: dict[str, list[str]] = {}
    with transactional(r.sf, commit=False) as s:
        for table in sorted(cap.CAPTURED_TABLES):
            tbl: Table = Base.metadata.tables[table]
            rows = []
            for row in s.execute(select(tbl)).mappings():
                data: dict[str, Any] | None = cap.shipped_fields(s, table, dict(row))
                if data is None:
                    continue
                if table in reg.UID_TABLES:
                    data["uid"] = row["uid"]
                rows.append(json.dumps(data, sort_keys=True, default=str, ensure_ascii=False))
            if rows:
                out[table] = sorted(rows)
    return out
