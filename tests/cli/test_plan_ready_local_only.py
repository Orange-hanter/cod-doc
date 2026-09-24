"""AFT-012: `plan ready --local-only/--all-files` (RFC 27 F13).

Чужая задача (все affected_files — абсолютные пути вне root_path проекта)
по умолчанию пропускается и считается в ``skipped_foreign``; ``--all-files``
возвращает полное ready-множество. Эталоны — литеральные task_id и числа.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _seed_plan(slug: str) -> str:
    """Зарегистрированный проект с планом: локальная LOC-001 и чужая FRG-001.

    Корень проекта — tmp_path/<slug>, поэтому '/elsewhere/a.py' гарантированно
    вне root_path.
    """
    from datetime import UTC, datetime

    from cod_doc.config import Config
    from cod_doc.domain.entities import Priority, TaskType
    from cod_doc.infra.db import db_for_entry, transactional
    from cod_doc.infra.models import PlanModel, PlanSectionModel
    from cod_doc.infra.repositories import ProjectRepository
    from cod_doc.services import task_service

    entry = Config.load().get_project(slug)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    scope = f"{slug}-plan"
    try:
        with transactional(factory) as session:
            project = ProjectRepository(session).get_by_slug(slug)
            assert project is not None and project.row_id is not None
            now = datetime.now(UTC)
            plan = PlanModel(project_id=project.row_id, scope=scope, created=now, last_updated=now)
            session.add(plan)
            session.flush()
            section = PlanSectionModel(
                plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
            )
            session.add(section)
            session.flush()
            task_service.create(
                session,
                project_id=project.row_id,
                plan_id=plan.row_id,
                section_id=section.row_id,
                task_id="LOC-001",
                title="Локальная задача",
                type=TaskType.FEATURE,
                priority=Priority.LOW,
                author="test",
            )
            task_service.create(
                session,
                project_id=project.row_id,
                plan_id=plan.row_id,
                section_id=section.row_id,
                task_id="FRG-001",
                title="Чужая задача",
                type=TaskType.FEATURE,
                priority=Priority.LOW,
                author="test",
                affected_files=["/elsewhere/a.py"],
            )
    finally:
        engine.dispose()
    return scope


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    slug = "pr"
    root = tmp_path / slug
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", slug])
    assert result.exit_code == 0, result.output
    return slug, _seed_plan(slug)


def test_cli_plan_ready_json_skips_foreign(
    tmp_path: Path, isolated_cod_doc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, scope = _setup(tmp_path, monkeypatch)

    result = CliRunner().invoke(main, ["plan", "ready", scope, "-p", slug, "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["skipped_foreign"] == 1
    assert [t["task_id"] for t in data["tasks"]] == ["LOC-001"]


def test_cli_plan_ready_all_files(
    tmp_path: Path, isolated_cod_doc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, scope = _setup(tmp_path, monkeypatch)

    result = CliRunner().invoke(main, ["plan", "ready", scope, "-p", slug, "--all-files", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["skipped_foreign"] == 0
    assert {t["task_id"] for t in data["tasks"]} == {"LOC-001", "FRG-001"}
