"""`doc drift` — detect drift between DB / projection_hash / on-disk file."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _DRIFT_ICON, _get_root_path, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config
    from cod_doc.domain.entities import Document
    from cod_doc.services.projection_service import DriftReport, ProjectDriftItem


@doc.command("drift")
@click.argument("doc_key", required=False)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--all", "all_docs", is_flag=True, default=False, help="Check every document")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_drift(
    ctx: click.Context,
    doc_key: str | None,
    project: str,
    all_docs: bool,
    as_json: bool,
) -> None:
    """Detect drift between DB content, projection hash, and on-disk file."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service, projection_service

    if bool(doc_key) == all_docs:
        raise click.UsageError("Pass DOC_KEY or --all, but not both.")

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        if all_docs:
            project_report = projection_service.detect_project_drift(
                session, project_id, root_path=root
            )
        else:
            assert doc_key is not None
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                console.print(f"[red]Document '{doc_key}' not found.[/red]")
                sys.exit(1)
            assert d is not None
            report = projection_service.detect_drift(session, d.row_id, root_path=root)

    def _json_row(d: Document, report: DriftReport) -> dict[str, str | None]:
        return {
            "doc_key": d.doc_key,
            "path": d.path,
            "status": report.status.value,
            "projection_hash": report.projection_hash,
            "db_content_hash": report.db_content_hash,
            "file_hash": report.file_hash,
        }

    def _json_issue(item: ProjectDriftItem) -> dict[str, str | None]:
        return {
            "doc_key": item.doc_key,
            "path": item.path,
            "status": item.report.status.value,
            "projection_hash": item.report.projection_hash,
            "db_content_hash": item.report.db_content_hash,
            "file_hash": item.report.file_hash,
        }

    if all_docs:
        if as_json:
            console.print(
                _json.dumps(
                    {
                        "project": project,
                        "total_docs": project_report.total_docs,
                        "problem_count": project_report.problem_count,
                        "counts": project_report.counts,
                        "issues": [_json_issue(item) for item in project_report.issues],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        console.rule(f"[bold]Drift — {project}[/bold]")
        console.print(
            "  "
            + "  ".join(
                f"{_DRIFT_ICON.get(status, '⚪')} {status}: {count}"
                for status, count in project_report.counts.items()
            )
        )
        if not project_report.issues:
            console.print(
                f"[green]✅ No projection drift found ({project_report.total_docs} docs).[/green]"
            )
            return

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column("Status", width=18)
        table.add_column("Doc key", style="cyan")
        table.add_column("Path", style="dim")
        for item in project_report.issues:
            table.add_row(
                f"{_DRIFT_ICON.get(item.report.status.value, '⚪')} {item.report.status.value}",
                item.doc_key,
                item.path,
            )
        console.print(table)
        return

    assert doc_key is not None
    assert d is not None
    if as_json:
        console.print(
            _json.dumps(
                {
                    "doc_key": doc_key,
                    "path": d.path,
                    "status": report.status.value,
                    "projection_hash": report.projection_hash,
                    "db_content_hash": report.db_content_hash,
                    "file_hash": report.file_hash,
                },
                indent=2,
            )
        )
        return

    icon = _DRIFT_ICON.get(report.status.value, "⚪")
    console.rule(f"[bold]Drift — {doc_key}[/bold]")
    console.print(f"  Status: {icon} {report.status.value}")
    console.print(f"  DB hash:   {report.db_content_hash[:16]}…")
    console.print(
        f"  Proj hash: {(report.projection_hash or '—')[:16]}{'…' if report.projection_hash else ''}"
    )
    console.print(
        f"  File hash: {(report.file_hash or '(missing)')[:16]}{'…' if report.file_hash else ''}"
    )
