"""Filesystem-safety helpers — path containment guard + stable content hash."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from ._types import PathEscapeError

if TYPE_CHECKING:
    from pathlib import Path


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_target(root_path: Path, doc_path: str) -> Path:
    """Compose root_path/doc_path, then verify the result stays under root.

    Raises `PathEscapeError` if the resolved target is not contained in
    the resolved root. This catches absolute paths, `..` segments, and
    symlink-based escapes that slipped past `validate_doc_path`.
    """
    resolved_root = root_path.resolve()
    target = (root_path / doc_path).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise PathEscapeError(
            f"document path {doc_path!r} resolves outside project root "
            f"({resolved_root}); refusing to read or write"
        ) from exc
    return target
