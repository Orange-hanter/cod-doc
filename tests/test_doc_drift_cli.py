"""CLI coverage for project-wide ``cod-doc doc drift --all``."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from cod_doc.cli.doc import doc
from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service, projection_service

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


def _bootstrap_repo(tmp_path: Path) -> tuple[Config, ProjectEntry]:
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _migrate(db_path)

    entry = ProjectEntry(name="drift-proj", path=str(repo))
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [entry.model_dump()]

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(
            slug="drift-proj",
            title="Drift Project",
            root_path=str(repo),
            config_json={},
        )
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()

        synced = doc_service.create(
            session,
            project_id=project.row_id,
            doc_key="synced",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Synced",
            owner="docs",
            author="human:test",
        )
        projection_service.export_document(session, synced.row_id, root_path=repo)

        doc_service.create(
            session,
            project_id=project.row_id,
            doc_key="missing",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Missing",
            owner="docs",
            author="human:test",
        )
    engine.dispose()
    return cfg, entry


def test_doc_drift_all_json_reports_project_summary(tmp_path: Path) -> None:
    cfg, _entry = _bootstrap_repo(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        doc,
        ["drift", "--project", "drift-proj", "--all", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_docs"] == 2
    assert payload["counts"]["in_sync"] == 1
    assert payload["counts"]["missing"] == 1
    assert payload["problem_count"] == 1
    assert payload["issues"][0]["doc_key"] == "missing"


def test_doc_drift_requires_doc_key_or_all() -> None:
    runner = CliRunner()

    result = runner.invoke(
        doc,
        ["drift", "--project", "drift-proj"],
        obj={"config": Config(projects=[])},
    )

    assert result.exit_code != 0
    assert "Pass DOC_KEY or --all" in result.output
