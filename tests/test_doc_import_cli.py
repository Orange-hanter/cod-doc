"""ADO-015 at the CLI: `doc import` names every value it had to bend.

`doc import` had no CLI test, which is part of why the coercion stayed
invisible for so long: the only place an operator could have noticed it printed
nothing but a green success line.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli.doc import doc
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import import_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

# `kickoff-brief` is a real type from this repo's own docs that no version of
# cod-doc can store; `living` is the pilots' spelling of "active".
ORIGINAL = "---\ntype: guide\nstatus: draft\n---\n\n# Brief\n\nBody.\n"
EDITED = "---\ntype: kickoff-brief\nstatus: living\n---\n\n# Brief\n\nBody.\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _migrate(db_path: Path) -> None:
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")


def _bootstrap_repo(tmp_path: Path, *, file_content: str) -> Config:
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _migrate(db_path)

    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name="brief-proj", path=str(repo)).model_dump()]

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(
            slug="brief-proj", title="Brief", root_path=str(repo), config_json={}
        )
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()

        d = import_service.import_markdown(
            session,
            project_id=project.row_id,
            doc_key="brief",
            raw_markdown=ORIGINAL,
            author="human:test",
        ).document
        assert d.row_id is not None
        model = session.get(DocumentModel, d.row_id)
        assert model is not None
        model.path = "brief.md"
        model.content_sha256_head = _sha256(ORIGINAL)
        model.projection_hash = _sha256(ORIGINAL)
    engine.dispose()

    (repo / "brief.md").write_text(file_content, encoding="utf-8")
    return cfg


def test_doc_import_prints_the_coercions_it_applied(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path, file_content=EDITED)
    runner = CliRunner()

    result = runner.invoke(
        doc,
        ["import", str(tmp_path / "repo" / "brief.md"), "--project", "brief-proj"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Imported brief" in result.output
    # The update path keeps the type already on the row as its fallback, so the
    # line has to name both halves — the word the file used and what was stored.
    assert "type: 'kickoff-brief' → 'guide'" in result.output
    assert "status: 'living' → 'active'" in result.output


def test_doc_import_stays_quiet_when_nothing_was_bent(tmp_path: Path) -> None:
    """A clean file must not grow a warning block — the signal has to stay rare."""
    clean = "---\ntype: capability\nstatus: active\n---\n\n# Brief\n\nBody.\n"
    cfg = _bootstrap_repo(tmp_path, file_content=clean)
    runner = CliRunner()

    result = runner.invoke(
        doc,
        ["import", str(tmp_path / "repo" / "brief.md"), "--project", "brief-proj"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "⚠" not in result.output
