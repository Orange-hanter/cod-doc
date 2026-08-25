"""export_document — write the rendered projection to disk + update hash."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from ._internals import _require_doc_model
from ._safety import _own_source_checkout, _safe_target, _sha256
from ._types import ExportGuardError, ExportResult
from .render import render_markdown

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.infra.models import DocumentModel


def _unified_diff(current: str, projected: str, *, path: Path) -> str:
    """Diff of what an export would do to the file on disk."""
    return "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            projected.splitlines(keepends=True),
            fromfile=f"{path} (on disk)",
            tofile=f"{path} (projection)",
        )
    )


def _assert_file_provenance(model: DocumentModel, target: Path, file_hash: str) -> None:
    """Refuse to overwrite content cod-doc never wrote or accepted (ADO-010, F7).

    A file matching either the last export (`projection_hash`) or the last
    accepted import (`content_sha256_head`) is ours to rewrite. Anything else
    is a human edit — or an unknown file at that path — and overwriting it
    would destroy work with no way back.
    """
    known = {h for h in (model.projection_hash, model.content_sha256_head) if h}
    if file_hash not in known:
        raise ExportGuardError(
            f"{target} does not match the last export or import of "
            f"'{model.doc_key}' — it was edited in place or never imported. "
            f"Re-run with dry_run to see the diff, `doc import` to accept the "
            f"file, or force_write to overwrite it."
        )


def _assert_own_checkout(root_path: Path) -> None:
    """Refuse to write into a repository that is not cod-doc's own checkout.

    The pilot-phase speed bump from ADO-010 stage 1: until round-trip fidelity
    is proven on foreign corpora, an export into someone else's repository has
    to be asked for explicitly. Inert when cod-doc runs as an installed
    package — there is no source checkout to compare against.
    """
    own = _own_source_checkout()
    if own is not None and root_path.resolve() != own:
        raise ExportGuardError(
            f"refusing to export into {root_path.resolve()}: not cod-doc's own "
            f"checkout ({own}). Use dry_run to preview, or force_write to "
            f"export into this project."
        )


def export_document(
    session: Session,
    document_id: int,
    *,
    root_path: Path,
    force: bool = False,
    audience: str | None = None,
    dry_run: bool = False,
    force_write: bool = False,
    own_checkout_only: bool = False,
) -> ExportResult:
    """Write the document projection to disk and update `projection_hash`.

    Idempotent: skips the write if `projection_hash` already matches the current
    DB content (no changes since last export), unless `force=True`.

    `audience` (COD-025 / SD-002) controls redaction in the rendered body —
    see `render_markdown`. The `projection_hash` is only updated for the
    canonical (audience=None) export, so an audience-specific export does NOT
    overwrite the stored hash. This keeps `detect_drift` consistent against
    the canonical projection.

    ADO-010 guards, both lifted by `force_write=True`:

    - the target file must match cod-doc's last export or last accepted import;
    - with `own_checkout_only=True` (what CLI and MCP pass), the project root
      must be cod-doc's own source checkout.

    `dry_run=True` renders and diffs without touching disk or DB, and never
    trips a guard: the result carries the unified diff in `ExportResult.diff`.

    Returns `ExportResult` with `written=False` on a skipped or dry-run export.
    """
    model = _require_doc_model(session, document_id)
    target = _safe_target(root_path, model.path)
    content = render_markdown(session, document_id, audience=audience)
    content_hash = _sha256(content)
    exists = target.exists()
    current = target.read_text(encoding="utf-8") if exists else ""

    if dry_run:
        return ExportResult(
            document_id=document_id,
            path=target,
            written=False,
            content_hash=content_hash,
            diff=_unified_diff(current, content, path=target),
        )

    if audience is None and not force and model.projection_hash == content_hash:
        return ExportResult(
            document_id=document_id,
            path=target,
            written=False,
            content_hash=content_hash,
        )

    if not force_write:
        if own_checkout_only:
            _assert_own_checkout(root_path)
        if exists:
            _assert_file_provenance(model, target, _sha256(current))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if audience is None:
        model.projection_hash = content_hash
        session.flush()

    return ExportResult(
        document_id=document_id,
        path=target,
        written=True,
        content_hash=content_hash,
    )
