"""CLI commands for ingesting external findings into COD-DOC."""

from __future__ import annotations

import json
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

import click
from rich.console import Console

from cod_doc.logging_config import get_logger
from cod_doc.services.ingest_service.registry import INGEST_ADAPTERS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config, ProjectEntry

console = Console()
log = get_logger("cli.ingest")

_DRY_RUN_PREVIEW_LIMIT = 20


def _download_pr_artifact(pr: int, dest: Path) -> None:
    """Download the ``pr-review-export-<PR>`` artifact using ``gh run download``.

    This function is the only place that shells out to ``gh``. It is exposed as
    a module-level hook so tests can monkey-patch it without touching the CLI
    surface.
    """
    artifact_name = f"pr-review-export-{pr}"
    cmd = ["gh", "run", "download", "--name", artifact_name, "--dir", str(dest)]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise click.ClickException(
            "`gh` CLI not found. Install GitHub CLI to use --from-pr."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        raise click.ClickException(
            f"Failed to download artifact `{artifact_name}`: {stderr}"
        ) from exc

    log.debug("gh run download output: %s", result.stdout)


@contextmanager
def _resolve_input_stream(
    input_stream: TextIO,
    *,
    from_pr: int | None,
) -> Iterator[TextIO]:
    """Return the input stream, downloading from GitHub Actions if requested."""
    if from_pr is None:
        yield input_stream
        return

    with tempfile.TemporaryDirectory(prefix="cod-doc-ingest-") as td:
        dest = Path(td)
        _download_pr_artifact(from_pr, dest)
        json_files = sorted(dest.rglob("*.json"))
        if not json_files:
            raise click.ClickException(
                f"Artifact `pr-review-export-{from_pr}` contains no .json files."
            )
        if len(json_files) > 1:
            log.warning(
                "Artifact contains multiple JSON files; using %s",
                json_files[0].name,
            )
        with json_files[0].open("r", encoding="utf-8") as fh:
            yield fh


def _extract_head_sha(raw_findings: Sequence[object]) -> str | None:
    """Pull ``headSha`` from the first ai_review raw finding payload."""
    for raw in raw_findings:
        if isinstance(raw, object):
            payload = getattr(raw, "payload", None)
            if isinstance(payload, dict):
                head_sha = payload.get("head_sha")
                if head_sha:
                    return str(head_sha)
    return None


def _build_source_run_id(
    adapter_name: str,
    head_sha: str | None,
    *,
    pr: int | None,
) -> str:
    """Build a stable, sha-bearing source_run_id for ``finding_source_run``."""
    from cod_doc.services.activity_service import _uuid7

    parts = ["cli-ingest", adapter_name]
    if pr is not None:
        parts.append(f"pr{pr}")
    if head_sha:
        parts.append(str(head_sha))
    parts.append(_uuid7())
    return "-".join(parts)


def _require_project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        raise click.ClickException(
            f"Project '{project_name}' is not registered in the DB. "
            "Run `cod-doc project init {name}` first."
        )
    return proj.row_id


def _open_entry(
    cfg: Config, project_name: str
) -> tuple[ProjectEntry, tuple[sessionmaker[Session], Engine]]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if entry is None:
        raise click.ClickException(f"Project not found: {project_name}")
    return entry, db_for_entry(entry)


def _run_ingest(
    ctx: click.Context,
    *,
    adapter_name: str,
    project: str,
    input_stream: TextIO,
    dry_run: bool,
    as_json: bool,
    pr: int | None = None,
) -> None:
    """Parse, fingerprint and (optionally) persist findings."""
    from cod_doc.infra.db import transactional
    from cod_doc.services.activity_service import emit
    from cod_doc.services.finding_service import ingest_findings

    cfg = ctx.obj["config"]
    _entry, (factory, engine) = _open_entry(cfg, project)

    try:
        adapter = INGEST_ADAPTERS[adapter_name]
        raw_findings = adapter.parse(input_stream)
        seeds = [raw.to_seed() for raw in raw_findings]

        head_sha = _extract_head_sha(raw_findings)
        source_run_id = _build_source_run_id(adapter_name, head_sha, pr=pr)

        with transactional(factory, commit=not dry_run) as session:
            # Re-resolve project id inside the transaction for dry-run parity.
            project_id = _require_project_id(session, project)
            result = ingest_findings(
                session,
                project_id=project_id,
                source_run_id=source_run_id,
                seeds=seeds,
            )
            if not dry_run:
                emit(
                    session,
                    project_id=project_id,
                    kind="finding.ingested",
                    actor_kind="cli",
                    scope_kind="source_run",
                    scope_id=source_run_id,
                    payload={
                        "adapter": adapter_name,
                        "pr": pr,
                        **result.as_dict(),
                    },
                )
    finally:
        engine.dispose()

    total = result.created + result.updated
    if as_json:
        console.print(
            json.dumps(
                {
                    "adapter": adapter_name,
                    "source_run_id": source_run_id,
                    "total": total,
                    "created": result.created,
                    "updated": result.updated,
                    "dry_run": dry_run,
                },
                ensure_ascii=False,
            )
        )
        return

    if dry_run:
        console.print("[yellow]Dry-run — no rows written.[/yellow]")
    console.print(
        f"Ingested [bold green]{total}[/bold green] finding(s): "
        f"{result.created} created, {result.updated} updated."
    )
    console.print(f"Source run: [dim]{source_run_id}[/dim]")
    if dry_run and seeds:
        console.print(f"Would ingest seeds ({len(seeds)}):")
        for seed in seeds[:_DRY_RUN_PREVIEW_LIMIT]:
            console.print(f"  • {seed.fingerprint[:16]}… {seed.title}")
        hidden = len(seeds) - _DRY_RUN_PREVIEW_LIMIT
        if hidden > 0:
            console.print(f"  … and {hidden} more")


@click.group()
def ingest() -> None:
    """Ingest external findings (ai-review, ZAIrgRush, routines)."""


# Common options shared by every adapter subcommand.
_project_option = click.option("--project", "-p", required=True, help="Project slug")
_input_option = click.option(
    "--input",
    "input_stream",
    type=click.File("r"),
    default="-",
    help="Input file (default: stdin).",
)
_dry_run_option = click.option("--dry-run", is_flag=True, help="Preview without writing to the DB.")
_json_option = click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")


@ingest.command("ai_review")
@_project_option
@_input_option
@click.option(
    "--from-pr",
    type=int,
    default=None,
    help="Download `pr-review-export-<PR>` artifact via `gh run download`.",
)
@_dry_run_option
@_json_option
@click.pass_context
def ai_review_cmd(
    ctx: click.Context,
    project: str,
    input_stream: TextIO,
    from_pr: int | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Ingest an ai-review JSON export (file, stdin, or PR artifact)."""
    with _resolve_input_stream(input_stream, from_pr=from_pr) as stream:
        _run_ingest(
            ctx,
            adapter_name="ai_review",
            project=project,
            input_stream=stream,
            dry_run=dry_run,
            as_json=as_json,
            pr=from_pr,
        )


# Register a generic subcommand for every non-ai_review adapter.
for _adapter_name in INGEST_ADAPTERS:
    if _adapter_name == "ai_review":
        continue

    def _make_cmd(name: str) -> Callable[..., Any]:
        @ingest.command(name)
        @_project_option
        @_input_option
        @_dry_run_option
        @_json_option
        @click.pass_context
        def _cmd(
            ctx: click.Context,
            project: str,
            input_stream: TextIO,
            dry_run: bool,
            as_json: bool,
        ) -> None:
            """Ingest findings from an external export."""
            _run_ingest(
                ctx,
                adapter_name=name,
                project=project,
                input_stream=input_stream,
                dry_run=dry_run,
                as_json=as_json,
            )

        return _cmd

    _make_cmd(_adapter_name)
