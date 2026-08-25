"""Filesystem-safety helpers — path containment guard + stable content hash."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from ._types import PathEscapeError


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _own_source_checkout() -> Path | None:
    """Repo root cod-doc is running from, or None when installed as a package.

    Used by the ADO-010 export guard to tell "cod-doc editing its own docs"
    from "cod-doc writing into someone else's repository". Resolved from the
    package location, so a `pip install -e .` checkout counts and a wheel in
    site-packages does not.
    """
    root = Path(__file__).resolve().parents[3]
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return None
    if 'name = "cod-doc"' not in pyproject.read_text(encoding="utf-8"):
        return None
    return root


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
