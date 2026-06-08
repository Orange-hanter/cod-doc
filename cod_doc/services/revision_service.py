"""RevisionService — append-only history for any entity.

DATA_MODEL §3.5: `revision_id` is a ULID (sortable by time); `parent_revision_id`
chains revisions of the same entity for optimistic concurrency control.

Public API:
- `write` — append a new revision; auto-fills `revision_id` (ULID) and
  `parent_revision_id` (last revision of the same entity, if any).
- `list_for_entity` — full history of an entity, oldest → newest.
- `revert` — undo a revision by delegating to the entity-owning service
  (COD-022). Supported entity_kinds: TASK (op=status/complete),
  SECTION (unified-diff body restore), DOCUMENT (op=rename).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select
from ulid import ULID

from cod_doc.domain.entities import EntityKind, Revision, TaskStatus
from cod_doc.infra.models import RevisionModel, SectionModel, TaskModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class RevisionConflictError(RuntimeError):
    """Raised when `expected_parent_revision_id` does not match the current head.

    Mirrors DATA_MODEL §3.5: writers may pass the parent ULID they last observed;
    if a concurrent writer landed first, the head moved and we refuse the write.
    """


# Sentinel: caller didn't pass a parent expectation. Distinct from `None`,
# which is a *valid* expectation ("I expect this is the first revision").
# Public so that other services can use it as a default parameter value.
NO_PARENT_CHECK: Final = object()
# Keep the underscore alias for backward compatibility within this module.
_NO_PARENT_CHECK = NO_PARENT_CHECK


def _to_domain(model: RevisionModel) -> Revision:
    return Revision(
        row_id=model.row_id,
        revision_id=model.revision_id,
        project_id=model.project_id,
        entity_kind=EntityKind(model.entity_kind),
        entity_id=model.entity_id,
        parent_revision_id=model.parent_revision_id,
        author=model.author,
        diff=model.diff,
        at=model.at,
        reason=model.reason,
        commit_sha=model.commit_sha,
        run_id=model.run_id,
    )


def _current_head(session: Session, entity_kind: EntityKind, entity_id: int) -> str | None:
    """Latest `revision_id` for the entity, or None if no revisions yet."""
    stmt = (
        select(RevisionModel.revision_id)
        .where(
            RevisionModel.entity_kind == entity_kind.value,
            RevisionModel.entity_id == entity_id,
        )
        .order_by(RevisionModel.at.desc(), RevisionModel.row_id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def write(
    session: Session,
    *,
    project_id: int,
    entity_kind: EntityKind,
    entity_id: int,
    author: str,
    diff: str,
    reason: str | None = None,
    commit_sha: str | None = None,
    expected_parent_revision_id: str | None | object = _NO_PARENT_CHECK,
) -> Revision:
    """Append a revision row.

    `parent_revision_id` is auto-derived from the entity's current head.
    If `expected_parent_revision_id` is passed (including explicit `None` for
    "I expect to be the first writer"), it must equal the current head, or
    `RevisionConflictError` is raised — this is the optimistic concurrency hook.
    """
    head = _current_head(session, entity_kind, entity_id)
    if expected_parent_revision_id is not _NO_PARENT_CHECK and head != expected_parent_revision_id:
        raise RevisionConflictError(
            f"head moved: expected parent={expected_parent_revision_id!r}, "
            f"actual={head!r} for {entity_kind.value} #{entity_id}"
        )

    rid_obj = ULID.from_datetime(datetime.now(UTC))
    # PCA-031: stamp the active run_id (set by Orchestrator.run_task or
    # tests via run_context.run_scope). NULL = human / external mutation.
    from cod_doc.services.run_context import get_current_run_id

    model = RevisionModel(
        revision_id=str(rid_obj),
        project_id=project_id,
        entity_kind=entity_kind.value,
        entity_id=entity_id,
        parent_revision_id=head,
        author=author,
        at=rid_obj.datetime,
        diff=diff,
        reason=reason,
        commit_sha=commit_sha,
        run_id=get_current_run_id(),
    )
    session.add(model)
    session.flush()
    return _to_domain(model)


def list_for_entity(session: Session, entity_kind: EntityKind, entity_id: int) -> list[Revision]:
    """Full history for the entity, oldest → newest."""
    stmt = (
        select(RevisionModel)
        .where(
            RevisionModel.entity_kind == entity_kind.value,
            RevisionModel.entity_id == entity_id,
        )
        .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
    )
    return [_to_domain(m) for m in session.execute(stmt).scalars()]


def head_for_entity(
    session: Session,
    entity_kind: EntityKind,
    entity_id: int,
) -> str | None:
    """Return the current head revision_id for an entity, or None.

    Used by web write-paths for optimistic concurrency tokens — the form
    embeds this as a hidden `expected_parent_revision_id`, the handler
    passes it to `patch_section` (or similar), and a mid-flight write
    raises `RevisionConflictError`.
    """
    return _current_head(session, entity_kind, entity_id)


def list_recent_for_project(
    session: Session,
    project_id: int,
    *,
    limit: int = 5,
) -> list[Revision]:
    """Most recent revisions across all entities of a project, newest → oldest.

    Used by the project overview dashboard (WEB-014). `RevisionModel` carries
    `project_id` directly, so this is a single index-supported query — no
    join chain through entity tables needed.
    """
    return list_for_project(session, project_id, limit=limit)


def list_for_project(
    session: Session,
    project_id: int,
    *,
    limit: int = 50,
    entity_kind: EntityKind | None = None,
    entity_id: int | None = None,
) -> list[Revision]:
    """Newest-first revisions of a project, optionally narrowed to one entity.

    Used by the revisions log page (WEB-021). When both `entity_kind` and
    `entity_id` are passed, behaves like `list_for_entity` but order is
    flipped to newest-first to match the timeline UX.
    """
    stmt = select(RevisionModel).where(RevisionModel.project_id == project_id)
    if entity_kind is not None:
        stmt = stmt.where(RevisionModel.entity_kind == entity_kind.value)
    if entity_id is not None:
        stmt = stmt.where(RevisionModel.entity_id == entity_id)
    stmt = stmt.order_by(RevisionModel.at.desc(), RevisionModel.row_id.desc()).limit(limit)
    return [_to_domain(m) for m in session.execute(stmt).scalars()]


class RevertNotSupportedError(NotImplementedError):
    """Raised for revision ops or entity kinds that cannot be auto-reverted."""


def revert(session: Session, revision_id: str, *, author: str) -> Revision:
    """Undo a revision by delegating to the entity-owning service.

    Dispatches based on `entity_kind` + the `op` field in `revision.diff`:

    * **TASK** — `op=status`: restore old status via TaskService.update_status.
      `op=complete`: same (restores `old_status` from diff).
    * **SECTION** — unified-diff body: restore old body via difflib.restore +
      DocService.patch_section.
    * **DOCUMENT** — `op=rename`: restore old doc_key via DocService.rename.

    The inverse operation is written as a new revision (not amending history).
    Raises `RevertNotSupportedError` for ops or entity kinds not listed above.
    """
    stmt = select(RevisionModel).where(RevisionModel.revision_id == revision_id)
    model = session.execute(stmt).scalar_one_or_none()
    if model is None:
        raise LookupError(f"revision not found: {revision_id!r}")

    kind = EntityKind(model.entity_kind)

    if kind is EntityKind.TASK:
        _revert_task(session, model, author=author)

    elif kind is EntityKind.SECTION:
        _revert_section(session, model, author=author)

    elif kind is EntityKind.DOCUMENT:
        _revert_document(session, model, author=author)

    elif kind is EntityKind.TASK_DOC:
        _revert_task_doc(session, model, author=author)

    else:
        raise RevertNotSupportedError(f"revert not supported for entity_kind={kind.value!r}")

    return _to_domain(model)


# --------------------------------------------------------------------------- #
# Entity-specific revert helpers                                                #
# --------------------------------------------------------------------------- #


def _revert_task(session: Session, model: RevisionModel, *, author: str) -> None:
    diff_obj = json.loads(model.diff)
    op = diff_obj.get("op")

    if op == "status":
        old_status = TaskStatus(diff_obj["old"])
    elif op == "complete":
        old_status = TaskStatus(diff_obj["old_status"])
    else:
        raise RevertNotSupportedError(f"cannot auto-revert TASK revision with op={op!r}")

    task_model = session.get(TaskModel, model.entity_id)
    if task_model is None:
        raise LookupError(f"task #{model.entity_id} not found (may have been deleted)")

    # Import locally to avoid circular dependency.
    from cod_doc.services import task_service as _tasks

    _tasks.update_status(
        session,
        task_id=task_model.task_id,
        new_status=old_status,
        author=author,
        reason=f"revert revision {model.revision_id}",
        # Reverts may produce transitions that aren't in the forward state
        # machine (e.g. in_progress → pending). Bypass validation since the
        # original transition was already validated when first applied.
        force=True,
    )


_HUNK_HEADER_RE = re.compile(r"@@[^@]+@@")


def _restore_original_from_unified(diff: str) -> str:
    """Extract the 'from' content (body BEFORE the patch) of a unified diff.

    Handles the storage format produced by DocService._unified_diff, where
    difflib.unified_diff is called with lineterm='' + "".join(). This means
    the three header lines (---, +++, @@) are NOT separated by newlines from
    each other or from the first content line — the only newlines come from
    the content itself.

    Strategy: locate the end of the last @@ hunk-header, then parse the
    content lines that follow. Content lines start with +, -, space, or \\
    and run to the next newline (or end of string).
    """
    m = _HUNK_HEADER_RE.search(diff)
    if not m:
        return ""

    content = diff[m.end() :]
    # Each content line starts with a diff prefix and runs to the next '\n'.
    # Use re.findall with a sentinel '\n' appended to catch the final line.
    content_lines = re.findall(r"(?:[-+ \\][^\n]*)(?:\n|$)", content + "\n")

    result = []
    for line in content_lines:
        if line.startswith(("-", "---")):
            # '-' alone is an original line; '---' is a nested diff header (skip).
            if not line.startswith("---"):
                result.append(line[1:])
        elif line.startswith(" "):
            result.append(line[1:])
        # '+' lines are new-only → skip
    return "".join(result)


def _revert_section(session: Session, model: RevisionModel, *, author: str) -> None:
    """Restore section body by reversing the stored unified diff."""
    old_body = _restore_original_from_unified(model.diff)

    sec = session.get(SectionModel, model.entity_id)
    if sec is None:
        raise LookupError(f"section #{model.entity_id} not found")

    from cod_doc.services import doc_service as _docs

    _docs.patch_section(
        session,
        document_id=sec.document_id,
        anchor=sec.anchor,
        new_body=old_body,
        author=author,
        reason=f"revert revision {model.revision_id}",
    )


def _revert_document(session: Session, model: RevisionModel, *, author: str) -> None:
    diff_obj = json.loads(model.diff)
    op = diff_obj.get("op")

    if op != "rename":
        raise RevertNotSupportedError(f"cannot auto-revert DOCUMENT revision with op={op!r}")

    old_doc_key = diff_obj["from"]["doc_key"]
    old_path = diff_obj["from"]["path"]

    from cod_doc.services import doc_service as _docs

    _docs.rename(
        session,
        document_id=model.entity_id,
        new_doc_key=old_doc_key,
        new_path=old_path,
        author=author,
        reason=f"revert revision {model.revision_id}",
    )


def _revert_task_doc(session: Session, model: RevisionModel, *, author: str) -> None:
    """PCA-917: Restore task-bound doc body from a revision snapshot.

    Delegates to ``task_doc_service.revert`` which handles snapshot lookup
    and writes the inverse revision via ``put`` with optimistic locking.
    """
    from cod_doc.infra.models import TaskDocumentModel
    from cod_doc.services import task_doc_service

    td = session.get(TaskDocumentModel, model.entity_id)
    if td is None:
        raise RevertNotSupportedError(f"TASK_DOC entity #{model.entity_id} not found")
    task_doc_service.revert(
        session,
        project_id=model.project_id,
        task_row_id=td.task_id,
        key=td.key,
        revision_id=model.revision_id,
        author=author,
    )
