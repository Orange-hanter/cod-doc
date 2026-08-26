"""CLI coverage for ADO-022: `doc backfill-projection` + the export refusal.

`doc export` had no CLI test at all, so the "guard → exit code 2" branch was
uncovered — including the message that has to tell an operator how to get out.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli.doc import doc
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import import_service
from cod_doc.services.projection_service import export as export_mod

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

RAW = "---\ntype: guide\nstatus: draft\nowner: docs\n---\n\n# Legacy\n\nBody.\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _bootstrap_repo(tmp_path: Path) -> Config:
    """A project whose only document is a pre-0025 row with its file on disk."""
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _migrate(db_path)

    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name="legacy-proj", path=str(repo)).model_dump()]

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(
            slug="legacy-proj", title="Legacy", root_path=str(repo), config_json={}
        )
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()

        d = import_service.import_markdown(
            session,
            project_id=project.row_id,
            doc_key="legacy",
            raw_markdown=RAW,
            author="human:test",
        ).document
        assert d.row_id is not None
        model = session.get(DocumentModel, d.row_id)
        assert model is not None
        model.path = "legacy.md"
        model.frontmatter_raw = None
        model.title_in_body = None
        model.content_sha256_head = _sha256(RAW)
        model.projection_hash = _sha256(f"pre-ADO-010 render of {RAW}")
    engine.dispose()

    (repo / "legacy.md").write_text(RAW, encoding="utf-8")
    return cfg


def test_backfill_projection_json_reports_counts(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        doc,
        ["backfill-projection", "--project", "legacy-proj", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["scanned"] == 1
    assert payload["filled"] == 1
    assert payload["file_missing"] == 0
    assert payload["items"][0] == {
        "doc_key": "legacy",
        "path": "legacy.md",
        "action": "filled",
    }


def test_backfill_projection_dry_run_leaves_the_row_unfixed(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)
    runner = CliRunner()

    dry = runner.invoke(
        doc,
        ["backfill-projection", "--project", "legacy-proj", "--dry-run", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.output)["filled"] == 1

    again = runner.invoke(
        doc,
        ["backfill-projection", "--project", "legacy-proj", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert json.loads(again.output)["scanned"] == 1, "dry run must not have written"


def test_doc_export_refuses_a_legacy_row_and_names_the_cure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code 2 plus a message an operator can act on."""
    cfg = _bootstrap_repo(tmp_path)
    # The own-checkout guard would fire first on a tmp project; ADO-022 is what
    # this case is about, so pretend cod-doc runs as an installed package.
    monkeypatch.setattr(export_mod, "_own_source_checkout", lambda: None)
    runner = CliRunner()

    result = runner.invoke(
        doc,
        ["export", "legacy", "--project", "legacy-proj"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 2, result.output
    assert "backfill-projection" in result.output
    assert (tmp_path / "repo" / "legacy.md").read_text(encoding="utf-8") == RAW


def test_doc_export_succeeds_after_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _bootstrap_repo(tmp_path)
    monkeypatch.setattr(export_mod, "_own_source_checkout", lambda: None)
    runner = CliRunner()

    fix = runner.invoke(
        doc,
        ["backfill-projection", "--project", "legacy-proj"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert fix.exit_code == 0, fix.output

    result = runner.invoke(
        doc,
        ["export", "legacy", "--project", "legacy-proj"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "repo" / "legacy.md").read_text(encoding="utf-8") == RAW
