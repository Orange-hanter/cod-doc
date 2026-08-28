"""Pure fingerprint functions for external findings.

These functions are deterministic, side-effect free and never touch the
 database or I/O. They are called by the service layer only; ingest adapters
must pass raw fields and must not compute fingerprints themselves.
"""

from __future__ import annotations

import hashlib
from typing import Any

from cod_doc.domain.text import normalize_title


def _sha256(parts: list[str | None]) -> str:
    """SHA-256 hex digest of ``|``-joined parts, with ``None`` treated as ''."""
    payload = "|".join("" if p is None else p for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_ai_review(
    *,
    source: str = "ai_review",
    path: str | None,
    fp: str | None,
    severity: str,
    title: str,
) -> tuple[str, dict[str, Any]]:
    """Fingerprint for ai-review findings.

    Formula: ``sha256(source|path|fp|severity)``.
    When ``fp`` is missing, degrade to ``normalize_title(title)`` and mark
    ``payload.fp_basis="title"`` so consumers know the fingerprint may shift
    if the title changes.
    """
    basis: dict[str, Any]
    if fp:
        digest = _sha256([source, path, fp, severity])
        basis = {"fp_basis": "fp"}
    else:
        digest = _sha256([source, path, normalize_title(title), severity])
        basis = {"fp_basis": "title"}
    return digest, basis


def fingerprint_zairgrush(
    *,
    source: str = "zairgrush",
    exp: str,
    variant: str,
    kind: str,
) -> tuple[str, dict[str, Any]]:
    """Fingerprint for ZAIrgRush experiment findings.

    Formula: ``sha256(source|exp|variant|kind)``.
    Human-readable ``note`` is intentionally excluded from the key.
    """
    digest = _sha256([source, exp, variant, kind])
    return digest, {"fp_basis": "exp_variant_kind"}


def fingerprint_routine(
    *,
    source: str = "routine",
    check_name: str,
    scope_kind: str,
    scope_id: str,
) -> tuple[str, dict[str, Any]]:
    """Fingerprint for routine-generated findings.

    Formula: ``sha256(source|check_name|scope_kind|scope_id)``.
    """
    digest = _sha256([source, check_name, scope_kind, scope_id])
    return digest, {"fp_basis": "check_scope"}
