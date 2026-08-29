"""export_document — write the rendered projection to disk + update hash."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from cod_doc.domain.entities import DocumentType

from ._frontmatter import _leading_shape
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


def _audience_target(canonical: Path, audience: str) -> Path:
    """Path of the audience-specific export, derived from the canonical one.

    `overview.md` with audience `public` → `overview.public.md`. The suffix
    keeps the redacted artifact next to the canonical file while leaving the
    canonical file (and its `projection_hash`) untouched (ADO-053): neither
    `detect_drift` nor a later `doc import` ever sees the redacted body.
    Characters outside `[A-Za-z0-9_-]` are flattened to `-` so an exotic
    audience value cannot escape the directory or break the name.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in audience)
    return canonical.with_name(f"{canonical.stem}.{safe}{canonical.suffix}")


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


def _assert_projection_fidelity_known(
    model: DocumentModel,
    target: Path,
    *,
    current: str,
    projected: str,
    file_hash: str,
) -> None:
    """Refuse to export a row that predates `0025_projection_fidelity` (ADO-022).

    On such a row `frontmatter_raw` and `title_in_body` are NULL, which the
    renderer reads as "DB-authored" — so it re-serialises the frontmatter from
    `frontmatter_json` (reordering or inventing keys) and emits an `# H1` the
    source never had. On a real corpus that rewrote 107 of 121 files.

    The refusal is deliberately narrow, because NULL is also the *legitimate*
    state of a document created with `doc create`. It fires only when all of
    the following hold:

    1. one of the two fidelity columns is NULL;
    2. the file on disk is not cod-doc's own last export (`projection_hash`) —
       when it is, the projection is already what is on disk and there is
       nothing to lose;
    3. the export would actually change the file's leading shape — a different
       YAML block, or an H1 appearing/disappearing.

    Cured by `backfill_projection_fidelity`, which recovers the two columns
    from the file without touching domain fields.
    """
    if model.frontmatter_raw is not None and model.title_in_body is not None:
        return
    if model.projection_hash is not None and file_hash == model.projection_hash:
        return
    if _leading_shape(current) == _leading_shape(projected):
        return
    raise ExportGuardError(
        f"{target}: the DB does not remember how this file is shaped "
        f"(frontmatter_raw/title_in_body are NULL — the '{model.doc_key}' row "
        f"predates migration 0025_projection_fidelity), so the export would "
        f"rewrite its frontmatter or invent a heading. Run "
        f"`doc backfill-projection` (MCP: doc_backfill_projection) to recover "
        f"the shape from disk, or `doc import` to accept the file wholesale. "
        f"Re-run with dry_run to see the diff, or force_write to overwrite it."
    )


def _pending_type_recoercion(model: DocumentModel) -> str | None:
    """The `type:` the source file carried, when the DB still holds a coercion.

    `frontmatter_json` keeps the authored frontmatter verbatim, so a row whose
    `type` column disagrees with it was written by a build whose `DocumentType`
    had no such member and silently fell back to `module-spec`. That is exactly
    the predicate of migration `0026_document_type_recoercion`: returns
    non-None iff the migration would still repair this row.

    None when there is nothing to restore — the two agree (the normal case, and
    the case after 0026 has run), the frontmatter names no type, or the
    authored value is still unrepresentable, which `_raw_matches_db` already
    keeps verbatim on its own.
    """
    authored = (model.frontmatter_json or {}).get("type")
    if not isinstance(authored, str) or authored == model.type:
        return None
    try:
        DocumentType(authored)
    except ValueError:
        return None
    return authored


def _assert_type_recoercion_applied(
    model: DocumentModel,
    target: Path,
    *,
    current: str,
    projected: str,
) -> None:
    """Refuse to export a row that predates `0026_document_type_recoercion`.

    ADO-015 made eight corpus types (`capability`, `audit-report`, `design`, …)
    real `DocumentType` members. That widening is what *disarms* the ADO-010
    protection for files carrying them: `_raw_matches_db` used to treat an
    unstorable `type:` as agreeing with the row, and the moment the value
    became storable the comparison turned into `'capability' != 'module-spec'`
    and the renderer started rebuilding the block from `frontmatter_json`.

    Migration 0026 restores the authored value in the DB, and nothing else in
    this pipeline checks that it ran: the file still matches the last import,
    so the ADO-010 provenance guard passes, and `frontmatter_raw` is not NULL,
    so the ADO-022 fidelity guard returns early. Upgrade the package without
    upgrading the database and the next export silently rewrites the author's
    `type:` — the corruption 0026's own docstring predicts.

    Narrow on purpose: fires only when the row is one 0026 would repair *and*
    the export would actually change the file's leading shape. A file that
    already carries the coerced type (cod-doc's own earlier export) has nothing
    left to lose, so it is not blocked.
    """
    authored = _pending_type_recoercion(model)
    if authored is None:
        return
    if _leading_shape(current) == _leading_shape(projected):
        return
    raise ExportGuardError(
        f"{target}: '{model.doc_key}' is stored as type '{model.type}' while its "
        f"frontmatter says '{authored}' — the row was written before "
        f"'{authored}' was a known document type and migration "
        f"0026_document_type_recoercion has not been applied to this database, "
        f"so the export would rewrite the file's `type:` to the coerced value. "
        f"Run `cod-doc project init <slug>` (or `alembic upgrade head`) to "
        f"restore the authored type. Re-run with dry_run to see the diff, or "
        f"force_write to overwrite it."
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
    see `render_markdown`. An audience-specific export writes to a derived
    path (`<stem>.<audience><suffix>`, see `_audience_target`) instead of the
    canonical file and never updates `projection_hash` (ADO-053), so neither
    `detect_drift` nor a later `doc import` of the canonical path ever sees
    the redacted body.

    Guards, all lifted by `force_write=True`:

    - ADO-010: the target file must match cod-doc's last export or last
      accepted import;
    - ADO-010: with `own_checkout_only=True` (what CLI and MCP pass), the
      project root must be cod-doc's own source checkout;
    - ADO-022: a row that predates `0025_projection_fidelity` may not have its
      frontmatter rewritten — run `backfill_projection_fidelity` first;
    - ADO-015: a row that predates `0026_document_type_recoercion` — its `type`
      column is an old build's silent coercion — may not have its frontmatter
      rewritten either; apply the migration first.

    The provenance/fidelity/recoercion guards protect the *canonical*
    projection, so they only apply to `audience=None` exports — an audience
    artifact is derived, never imported back, and has no DB-remembered hash
    to compare against. The own-checkout guard applies to every write.

    `dry_run=True` renders and diffs without touching disk or DB, and never
    trips a guard: the result carries the unified diff in `ExportResult.diff`.

    Returns `ExportResult` with `written=False` on a skipped or dry-run export.
    """
    model = _require_doc_model(session, document_id)
    target = _safe_target(root_path, model.path)
    if audience is not None:
        target = _audience_target(target, audience)
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
        if audience is None and exists:
            file_hash = _sha256(current)
            _assert_file_provenance(model, target, file_hash)
            _assert_projection_fidelity_known(
                model,
                target,
                current=current,
                projected=content,
                file_hash=file_hash,
            )
            _assert_type_recoercion_applied(
                model,
                target,
                current=current,
                projected=content,
            )

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
