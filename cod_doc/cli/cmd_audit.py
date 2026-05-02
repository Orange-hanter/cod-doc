"""cod-doc audit — frontmatter + link + drift checks with --strict / --staged modes.

Levels (from audit-and-ci.md §1):
  soft   : cod-doc audit               — print all issues, exit 0
  strict : cod-doc audit --strict      — exit 1 on any error-severity issue
  staged : cod-doc audit --strict --staged — check only git-staged .md files
"""

from __future__ import annotations

import json as _json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from cod_doc.config import Config

console = Console()
log = get_logger("cli.audit")

_SEV_COLOR = {"error": "red", "warning": "yellow", "info": "dim"}
_SEV_ICON = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}


# ──────────────────────────────────────────────────────────────────────────────
# Internal result types
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class AuditFinding:
    code: str
    severity: str  # "error" | "warning" | "info"
    subject: str  # e.g. "doc:arch/data-model"
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_session(project_name: str, cfg: Config):  # type: ignore[no-untyped-def]
    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    url = resolve_db_url(Path(entry.path))
    engine = make_engine(url)
    return make_session_factory(engine)


def _require_project_id(session, project_name: str) -> int:  # type: ignore[no-untyped-def]
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(f"[red]Project '{project_name}' not in DB. Run 'project add' first.[/red]")
        sys.exit(1)
    return proj.row_id


def _staged_md_paths(root: Path) -> set[str]:
    """Return relative paths of .md files currently staged in git."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return {line.strip() for line in result.stdout.splitlines() if line.strip().endswith(".md")}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()


# ──────────────────────────────────────────────────────────────────────────────
# Audit checks
# ──────────────────────────────────────────────────────────────────────────────


def _check_frontmatter(doc: Any, findings: list[AuditFinding]) -> None:
    """Run FM-* advisory checks on a Document entity."""
    from cod_doc.services.validation import audit_frontmatter

    issues = audit_frontmatter(
        type=doc.type,
        status=doc.status,
        owner=doc.owner,
        source_of_truth=doc.source_of_truth,
        frontmatter=doc.frontmatter,
        last_updated=doc.last_updated,
    )
    for issue in issues:
        findings.append(
            AuditFinding(
                code=issue.code,
                severity=issue.severity,
                subject=f"doc:{doc.doc_key}",
                message=issue.message,
                details=issue.details,
            )
        )


def _check_drift(doc: Any, root: Path, session: Any, findings: list[AuditFinding]) -> None:
    """DR-003: flag if projection_hash doesn't match disk file."""
    from cod_doc.services import projection_service
    from cod_doc.services.projection_service import DriftStatus

    if doc.row_id is None:
        return
    try:
        report = projection_service.detect_drift(session, doc.row_id, root_path=root)
    except Exception:
        return

    if report.status == DriftStatus.STALE_EXPORT:
        findings.append(
            AuditFinding(
                code="DR-003",
                severity="warning",
                subject=f"doc:{doc.doc_key}",
                message="DB content changed since last export (projection is stale)",
                details={"path": doc.path, "status": report.status.value},
            )
        )
    elif report.status == DriftStatus.EDITED_IN_PLACE:
        findings.append(
            AuditFinding(
                code="DR-003",
                severity="warning",
                subject=f"doc:{doc.doc_key}",
                message="On-disk file was edited after last export (possible data loss on re-export)",
                details={"path": doc.path, "status": report.status.value},
            )
        )
    elif report.status == DriftStatus.MISSING:
        findings.append(
            AuditFinding(
                code="DR-003",
                severity="warning",
                subject=f"doc:{doc.doc_key}",
                message="Projection file not found on disk",
                details={"path": doc.path, "status": report.status.value},
            )
        )


# ──────────────────────────────────────────────────────────────────────────────
# audit command
# ──────────────────────────────────────────────────────────────────────────────


@click.command("audit")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit 1 if any error-severity issues are found (CI mode)",
)
@click.option(
    "--staged",
    is_flag=True,
    default=False,
    help="Check only git-staged .md files (pre-commit mode; implies --strict)",
)
@click.option(
    "--drift",
    is_flag=True,
    default=False,
    help="Also run DR-003 projection drift checks",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def audit(
    ctx: click.Context,
    project: str,
    strict: bool,
    staged: bool,
    drift: bool,
    as_json: bool,
) -> None:
    """Audit document frontmatter (FM-* rules) and optionally drift (DR-003).

    Runs advisory checks from validation.audit_frontmatter on every document
    in the project (or only staged files with --staged). Prints findings and
    exits 1 if --strict and any error-severity issues exist.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    effective_strict = strict or staged

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    entry = cfg.get_project(project)
    root = Path(entry.path).expanduser().resolve() if entry else Path.cwd()

    staged_paths: set[str] = set()
    if staged:
        staged_paths = _staged_md_paths(root)
        if not staged_paths:
            if not as_json:
                console.print("[dim]No staged .md files — nothing to audit.[/dim]")
            else:
                console.print(_json.dumps({"findings": [], "total": 0, "errors": 0}))
            return

    findings: list[AuditFinding] = []

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        docs = doc_service.list_for_project(session, project_id)

        # Filter to staged docs if requested
        if staged:
            docs = [d for d in docs if d.path in staged_paths]

        for d in docs:
            _check_frontmatter(d, findings)
            if drift:
                _check_drift(d, root, session, findings)

    error_count = sum(1 for f in findings if f.severity == "error")
    warning_count = sum(1 for f in findings if f.severity == "warning")

    if as_json:
        console.print(
            _json.dumps(
                {
                    "findings": [
                        {
                            "code": f.code,
                            "severity": f.severity,
                            "subject": f.subject,
                            "message": f.message,
                            "details": f.details,
                        }
                        for f in findings
                    ],
                    "total": len(findings),
                    "errors": error_count,
                    "warnings": warning_count,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        if effective_strict and error_count > 0:
            sys.exit(1)
        return

    if not findings:
        scope_note = f"({len(staged_paths)} staged files)" if staged else f"({len(docs)} docs)"
        console.print(f"[green]✅ No issues found {scope_note}.[/green]")
        return

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Code", style="cyan", width=8)
    table.add_column("Sev", width=8)
    table.add_column("Subject", width=36)
    table.add_column("Message")
    for f in findings:
        sev_icon = _SEV_ICON.get(f.severity, "·")
        sev_color = _SEV_COLOR.get(f.severity, "white")
        table.add_row(
            f.code,
            f"[{sev_color}]{sev_icon} {f.severity}[/{sev_color}]",
            f.subject,
            f.message,
        )
    console.print(table)
    console.print()
    console.print(
        f"  [red]{error_count} error(s)[/red]  "
        f"[yellow]{warning_count} warning(s)[/yellow]  "
        f"total {len(findings)}"
    )

    if effective_strict and error_count > 0:
        sys.exit(1)
