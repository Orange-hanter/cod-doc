"""CLI commands for working with external findings."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config, ProjectEntry

console = Console()
log = get_logger("cli.finding")


def _open_entry(
    cfg: Config, project_name: str
) -> tuple[ProjectEntry, sessionmaker[Session], Engine]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if entry is None:
        raise click.ClickException(f"Project not found: {project_name}")
    factory, engine = db_for_entry(entry)
    return entry, factory, engine


_MIN_RUNS_FOR_STABILITY = 2
_RUN_LABEL_TAIL = 12


def _require_project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        raise click.ClickException(
            f"Project '{project_name}' is not registered in the DB. "
            "Run `cod-doc project init {name}` first."
        )
    return proj.row_id


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


@click.group()
def finding() -> None:
    """Inspect and manage external findings."""


@finding.command("stability")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--sha", required=True, help="Commit SHA to analyse")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output")
@click.pass_context
def stability(
    ctx: click.Context,
    project: str,
    sha: str,
    as_json: bool,
) -> None:
    """Compute pairwise Jaccard stability of finding sets for a SHA."""
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import FindingModel, FindingSourceRunModel

    cfg: Config = ctx.obj["config"]
    _entry, factory, engine = _open_entry(cfg, project)

    try:
        with transactional(factory) as session:
            project_id = _require_project_id(session, project)
            stmt = (
                select(FindingSourceRunModel.source_run_id, FindingModel.fingerprint)
                .join(
                    FindingModel,
                    FindingSourceRunModel.finding_id == FindingModel.row_id,
                )
                .where(
                    FindingModel.project_id == project_id,
                    FindingSourceRunModel.source_run_id.like(f"%{sha}%"),
                )
            )
            rows = list(session.execute(stmt).all())
    finally:
        engine.dispose()

    runs: dict[str, set[str]] = {}
    for source_run_id, fingerprint in rows:
        runs.setdefault(source_run_id, set()).add(fingerprint)
    run_ids = sorted(runs)

    if len(run_ids) < _MIN_RUNS_FOR_STABILITY:
        msg = (
            f"Only {len(run_ids)} source run(s) matched sha '{sha}'; "
            "stability needs at least 2 runs."
        )
        if as_json:
            console.print(json.dumps({"error": msg, "runs": run_ids}, ensure_ascii=False))
        else:
            console.print(f"[yellow]{msg}[/yellow]")
        return

    matrix = [[_jaccard(runs[a], runs[b]) for b in run_ids] for a in run_ids]
    pairwise = [matrix[i][j] for i in range(len(run_ids)) for j in range(i + 1, len(run_ids))]
    summary = {
        "min": min(pairwise),
        "mean": sum(pairwise) / len(pairwise),
        "max": max(pairwise),
    }

    if as_json:
        data = {
            "project": project,
            "sha": sha,
            "runs": run_ids,
            "matrix": [[round(v, 4) for v in row] for row in matrix],
            "summary": {k: round(v, 4) for k, v in summary.items()},
        }
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    labels = [f"…{r[-_RUN_LABEL_TAIL:]}" if len(r) > _RUN_LABEL_TAIL else r for r in run_ids]
    table = Table(title=f"Finding stability — {project} @ {sha[:_RUN_LABEL_TAIL]}")
    table.add_column("Run", style="cyan")
    for label in labels:
        table.add_column(label, justify="right")
    for i, label in enumerate(labels):
        row = [f"{matrix[i][j]:.2f}" for j in range(len(run_ids))]
        table.add_row(label, *row)
    console.print(table)
    console.print(
        f"Jaccard summary: min={summary['min']:.2f} "
        f"mean={summary['mean']:.2f} max={summary['max']:.2f}"
    )
