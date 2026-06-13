"""COD-052 / STB-014: CLI smoke for `plan freeze` + `doc accept`."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from cod_doc.cli.doc import doc
from cod_doc.cli.plan import plan
from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Plan,
    PlanSection,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
from cod_doc.services import doc_service

REPO_ROOT = Path(__file__).resolve().parents[1]


def _migrate(db_path: Path) -> None:
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=True,
        env={"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": f"sqlite:///{db_path}"},
        capture_output=True,
    )


def _bootstrap(tmp_path: Path) -> tuple[Config, str]:
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _migrate(db_path)

    entry = ProjectEntry(name="fz-cli", path=str(repo))
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [entry.model_dump()]

    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(slug="fz-cli", title="FZ", root_path=str(repo), config_json={})
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()

        p = PlanRepository(session).add(
            Plan(project_id=project.row_id, scope="fz-plan", principle="test-first")
        )
        p.created = now
        p.last_updated = now
        session.flush()
        PlanSectionRepository(session).add(
            PlanSection(plan_id=p.row_id, letter="A", title="Core", slug="A-Core", position=0)
        )

        doc_service.create(
            session,
            project_id=project.row_id,
            doc_key="draft/x",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="X",
            owner="docs",
            author="human:test",
        )
    return cfg, str(repo)


def test_cli_plan_freeze_creates_snapshot(tmp_path: Path) -> None:
    cfg, _repo = _bootstrap(tmp_path)
    result = CliRunner().invoke(
        plan, ["freeze", "fz-plan", "--project", "fz-cli"], obj={"config": cfg}
    )
    assert result.exit_code == 0, result.output
    assert "Frozen" in result.output
    assert "frozen/fz-plan/" in result.output


def test_cli_doc_accept_promotes_draft(tmp_path: Path) -> None:
    cfg, _repo = _bootstrap(tmp_path)
    result = CliRunner().invoke(
        doc, ["accept", "draft/x", "--project", "fz-cli"], obj={"config": cfg}
    )
    assert result.exit_code == 0, result.output
    assert "Accepted" in result.output
    assert "active" in result.output


def test_cli_doc_accept_unknown_doc_errors(tmp_path: Path) -> None:
    cfg, _repo = _bootstrap(tmp_path)
    result = CliRunner().invoke(
        doc, ["accept", "no/such", "--project", "fz-cli"], obj={"config": cfg}
    )
    assert result.exit_code != 0
    assert "not found" in result.output
