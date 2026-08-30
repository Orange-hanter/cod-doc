"""OBI-010: commit_link_service — parse commit messages for task IDs + import.

Two public entry points:

- ``parse_task_refs(message)`` — extract task IDs (e.g. ``COD-042``) from a
  freeform commit message. Pure regex, no DB.
- ``import_from_git_log(session, project_id, repo_path, since=None, limit=500)``
  — shell out to ``git log`` in ``repo_path`` and persist any commits whose
  message references known task IDs.

After import, ``task_metrics.commit_count`` is updated for every affected
task. The dashboard at ``/p/<slug>/commits`` (OBI-011) reads this.

Idempotent: unique (project_id, task_id, sha) avoids duplicates on rerun.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from cod_doc.infra.models import CommitLinkModel, TaskMetricsModel, TaskModel
from cod_doc.services import activity_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Task-id token: 2-5 capital letters, dash, 3 digits, optional trailing capital
# for sub-tasks (matches the validator in services.validation.structural).
_TASK_REF_RE = re.compile(r"\b([A-Z]{2,5}-\d{3}[A-Z]?)\b")


def parse_task_refs(message: str) -> list[str]:
    """Return unique task IDs referenced in ``message``, in first-seen order."""
    if not message:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _TASK_REF_RE.finditer(message):
        tid = m.group(1)
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def _known_task_ids(session: Session, project_id: int) -> set[str]:
    rows = (
        session.execute(select(TaskModel.task_id).where(TaskModel.project_id == project_id))
        .scalars()
        .all()
    )
    return {r for r in rows if r}


def _parse_git_log(output: str) -> list[dict[str, Any]]:
    """Parse ``git log --pretty='%H|%an|%aI|%s'`` output into dicts."""
    out: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        sha, author, iso_ts, message = parts
        try:
            ts = datetime.fromisoformat(iso_ts)
        except ValueError:
            ts = None
        out.append(
            {
                "sha": sha.strip(),
                "author": author.strip(),
                "ts": ts,
                "message": message.strip(),
            }
        )
    return out


def import_from_git_log(
    session: Session,
    *,
    project_id: int,
    repo_path: Path | str,
    since: str | None = None,
    limit: int = 500,
) -> dict[str, int]:
    """Run ``git log`` in ``repo_path``, persist any commits with task refs.

    Returns ``{scanned, linked, skipped_existing}`` for log/UI use.
    """
    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        raise ValueError(f"not a git repository: {repo_path}")

    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        f"--max-count={limit}",
        "--pretty=format:%H|%an|%aI|%s",
    ]
    if since:
        cmd.append(f"--since={since}")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git log failed: {proc.stderr.strip()}")

    commits = _parse_git_log(proc.stdout)
    known = _known_task_ids(session, project_id)
    scanned = len(commits)
    linked = 0
    skipped = 0
    touched_tasks: set[str] = set()

    for c in commits:
        refs = parse_task_refs(c["message"])
        for tid in refs:
            if tid not in known:
                continue
            # Skip if (project, task, sha) already exists.
            existing = session.execute(
                select(CommitLinkModel).where(
                    CommitLinkModel.project_id == project_id,
                    CommitLinkModel.task_id == tid,
                    CommitLinkModel.sha == c["sha"],
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue
            session.add(
                CommitLinkModel(
                    project_id=project_id,
                    task_id=tid,
                    sha=c["sha"],
                    short_sha=c["sha"][:12],
                    message=c["message"][:1024],
                    author=c["author"][:128],
                    ts=c["ts"],
                )
            )
            linked += 1
            touched_tasks.add(tid)
    session.flush()

    if touched_tasks:
        _refresh_commit_counts(session, project_id, touched_tasks)
        session.flush()

    activity_service.emit_for_write(
        session,
        project_id,
        "commit_link.imported",
        "system",
        scope_kind="project",
        scope_id=str(project_id),
        payload={
            "scanned": scanned,
            "linked": linked,
            "skipped_existing": skipped,
            "touched_tasks": sorted(touched_tasks),
        },
        summary=f"Imported {linked} commit link(s) for project {project_id}",
    )

    return {"scanned": scanned, "linked": linked, "skipped_existing": skipped}


def _refresh_commit_counts(
    session: Session,
    project_id: int,
    task_ids: set[str],
) -> None:
    """Update ``task_metrics.commit_count`` for the given tasks."""
    if not task_ids:
        return
    # Count commits per task_id.
    rows = session.execute(
        select(
            CommitLinkModel.task_id,
            func.count(CommitLinkModel.row_id).label("n"),
        )
        .where(
            CommitLinkModel.project_id == project_id,
            CommitLinkModel.task_id.in_(task_ids),
        )
        .group_by(CommitLinkModel.task_id)
    ).all()
    by_id = {r.task_id: int(r.n) for r in rows}

    # Update each metrics row that has a corresponding task.
    for tid, n in by_id.items():
        tm_row = session.execute(
            select(TaskMetricsModel)
            .join(TaskModel, TaskModel.row_id == TaskMetricsModel.task_id)
            .where(
                TaskMetricsModel.project_id == project_id,
                TaskModel.task_id == tid,
            )
        ).scalar_one_or_none()
        if tm_row is not None:
            tm_row.commit_count = n


def list_for_task(
    session: Session,
    project_id: int,
    task_id: str,
) -> list[CommitLinkModel]:
    """All commit_link rows referencing ``task_id`` in this project."""
    return list(
        session.execute(
            select(CommitLinkModel)
            .where(
                CommitLinkModel.project_id == project_id,
                CommitLinkModel.task_id == task_id,
            )
            .order_by(CommitLinkModel.ts.desc().nulls_last())
        ).scalars()
    )


def list_for_project(
    session: Session,
    project_id: int,
    *,
    limit: int = 200,
) -> list[CommitLinkModel]:
    """All commit_link rows for this project, newest first."""
    return list(
        session.execute(
            select(CommitLinkModel)
            .where(CommitLinkModel.project_id == project_id)
            .order_by(CommitLinkModel.ts.desc().nulls_last())
            .limit(limit)
        ).scalars()
    )
