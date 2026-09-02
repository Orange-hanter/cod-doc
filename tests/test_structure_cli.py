"""CLI coverage for ingest / obligation export / pinned ctx."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from cod_doc.cli.cmd_ctx import ctx
from cod_doc.cli.cmd_ingest import ingest
from cod_doc.cli.obligation import obligation
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures" / "structure"


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


def _bootstrap(tmp_path: Path) -> tuple[Config, ProjectEntry]:
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _migrate(db_path)
    entry = ProjectEntry(name="struct-proj", path=str(repo))
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [entry.model_dump()]
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(slug="struct-proj", title="S", root_path=str(repo), config_json={})
        project.created = now
        project.updated = now
        session.add(project)
    return cfg, entry


def test_ingest_and_ctx_require_pin(tmp_path: Path) -> None:
    cfg, _entry = _bootstrap(tmp_path)
    runner = CliRunner()
    facts = FIXTURES / "structure-facts.v1.json"
    result = runner.invoke(
        ingest,
        ["structure", "-p", "struct-proj", "--facts", str(facts), "--trust-tier", "trusted_local", "--json"],
        obj={"config": cfg},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["publishedCurrent"] is True
    assert payload["idempotent"] is False

    export = runner.invoke(obligation, ["export", "-p", "struct-proj"], obj={"config": cfg})
    assert export.exit_code == 0, export.output
    exported = json.loads(export.output)
    assert exported["kind"] == "obligations_export"

    unpinned = runner.invoke(ctx, ["structure", "-p", "struct-proj"], obj={"config": cfg})
    assert unpinned.exit_code != 0

    pinned = runner.invoke(
        ctx,
        ["structure", "-p", "struct-proj", "--head-sha", "abcdef1"],
        obj={"config": cfg},
    )
    assert pinned.exit_code == 0, pinned.output
    context = json.loads(pinned.output)
    assert context["pinned"]["headSha"] == "abcdef1"
