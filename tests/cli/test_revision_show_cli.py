"""`cod-doc revision show`: ошибки без traceback (ai-review #192, AFT-022).

``_require_project_id`` и ``revision_service.get`` ничего не бросают наружу:
неизвестный проект и неизвестная ревизия — сообщение и код 1. Ревизия ищется
в скоупе проекта (`revision_service.get(project_id=…)`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, RevisionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from pathlib import Path

_UNKNOWN_REVISION = "01HQX5Z9F0K8R0000000000000"


def _init_project_with_revision(tmp_path: Path, name: str = "rv") -> str:
    """Проект с одной задачей; возвращает ULID её create-ревизии."""
    root = tmp_path / name
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output

    entry = Config.load().get_project(name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(name)
            assert proj is not None and proj.row_id is not None
            now = datetime.now(UTC)
            plan = PlanModel(
                project_id=proj.row_id, scope=f"{name}-plan", created=now, last_updated=now
            )
            session.add(plan)
            session.flush()
            sec = PlanSectionModel(
                plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
            )
            session.add(sec)
            session.flush()
            tasks.create(
                session,
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=sec.row_id,
                task_id="RV-001",
                title="Task with history",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:seed",
            )
            session.flush()
            rid = session.execute(select(RevisionModel.revision_id)).scalars().first()
    finally:
        engine.dispose()
    assert rid is not None
    return rid


def test_revision_show_unknown_project_exits_cleanly(tmp_path: Path) -> None:
    _init_project_with_revision(tmp_path)

    result = CliRunner().invoke(main, ["revision", "show", _UNKNOWN_REVISION, "-p", "nope"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Project not found: nope" in result.output


def test_revision_show_unknown_revision_exits_cleanly(tmp_path: Path) -> None:
    _init_project_with_revision(tmp_path)

    result = CliRunner().invoke(main, ["revision", "show", _UNKNOWN_REVISION, "-p", "rv"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert f"Revision '{_UNKNOWN_REVISION}' not found." in result.output


def test_revision_show_known_revision_as_json(tmp_path: Path) -> None:
    rid = _init_project_with_revision(tmp_path)

    result = CliRunner().invoke(main, ["revision", "show", rid, "-p", "rv", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["revision_id"] == rid
