"""CLI tests for ``cod-doc finding`` (SYM-006B)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.infra.db import db_for_entry, make_session_factory, transactional
from cod_doc.services.finding_service import FindingSeed, fingerprint_ai_review, ingest_findings

if TYPE_CHECKING:
    from pathlib import Path


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _seed_project(project_name: str, tmp_path: Path) -> Any:
    """Return (engine, project_id) for a freshly initialised project."""
    from cod_doc.config import Config
    from cod_doc.infra.repositories import ProjectRepository

    _init_project(tmp_path, project_name)
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(project_name)
        assert proj is not None and proj.row_id is not None
    return engine, proj.row_id


def test_finding_stability_eight_runs(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    engine, project_id = _seed_project("p", tmp_path)
    factory = make_session_factory(engine)
    sha = "deadbeef1234567890abcdef1234567890abcdef"

    with transactional(factory) as session:
        for i in range(8):
            seeds: list[FindingSeed] = []
            for j in range(3):
                fp, basis = fingerprint_ai_review(
                    path=f"src/file_{j}.py",
                    fp=f"fp-{j}",
                    severity="major",
                    title=f"Issue {j}",
                )
                seeds.append(
                    FindingSeed(
                        fingerprint=fp,
                        source="ai_review",
                        title=f"Issue {j}",
                        severity="major",
                        path=f"src/file_{j}.py",
                        payload=basis,
                    )
                )
            ingest_findings(
                session,
                project_id=project_id,
                source_run_id=f"run-{i}-{sha}",
                seeds=seeds,
            )

    runner = CliRunner()
    result = runner.invoke(main, ["finding", "stability", "--project", "p", "--sha", sha])
    assert result.exit_code == 0, result.output
    assert "Jaccard" in result.output
    assert "1.00" in result.output
    assert "min=1.00" in result.output
    assert "mean=1.00" in result.output
    assert "max=1.00" in result.output
    engine.dispose()


def test_finding_stability_too_few_runs(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    engine, project_id = _seed_project("p", tmp_path)
    factory = make_session_factory(engine)
    sha = "cafebabe"

    with transactional(factory) as session:
        fp, basis = fingerprint_ai_review(
            path="src/x.py", fp="fp-x", severity="minor", title="Lonely"
        )
        ingest_findings(
            session,
            project_id=project_id,
            source_run_id=f"run-{sha}",
            seeds=[
                FindingSeed(
                    fingerprint=fp,
                    source="ai_review",
                    title="Lonely",
                    severity="minor",
                    path="src/x.py",
                    payload=basis,
                )
            ],
        )

    runner = CliRunner()
    result = runner.invoke(main, ["finding", "stability", "--project", "p", "--sha", sha])
    assert result.exit_code == 0, result.output
    assert "Only 1 source run(s)" in result.output
    engine.dispose()


def test_finding_stability_json_output(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    engine, project_id = _seed_project("p", tmp_path)
    factory = make_session_factory(engine)
    sha = "beefcafe"

    with transactional(factory) as session:
        for i in range(2):
            fp, basis = fingerprint_ai_review(
                path=f"src/{i}.py", fp=f"fp-{i}", severity="major", title=f"Issue {i}"
            )
            ingest_findings(
                session,
                project_id=project_id,
                source_run_id=f"run-{i}-{sha}",
                seeds=[
                    FindingSeed(
                        fingerprint=fp,
                        source="ai_review",
                        title=f"Issue {i}",
                        severity="major",
                        path=f"src/{i}.py",
                        payload=basis,
                    )
                ],
            )

    runner = CliRunner()
    result = runner.invoke(main, ["finding", "stability", "--project", "p", "--sha", sha, "--json"])
    assert result.exit_code == 0, result.output
    import json

    data = json.loads(result.output)
    assert data["sha"] == sha
    assert len(data["runs"]) == 2
    assert "matrix" in data
    assert "summary" in data
    engine.dispose()
