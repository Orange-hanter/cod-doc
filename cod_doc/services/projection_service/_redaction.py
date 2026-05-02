"""Audience-based body redaction (COD-025 / SD-002)."""

from __future__ import annotations

from cod_doc.domain.entities import Sensitivity

_REDACTION_MARKER = "> [content redacted: {sensitivity} — see DB]"


def _audience_blocks_sensitivity(audience: str | None, doc_sensitivity: str) -> bool:
    """Return True if `audience` cannot see content of level `doc_sensitivity`.

    Audience tiers (low → high clearance):
        public < internal < confidential < restricted

    Mapping: a `public` audience may only see `public` content; `internal`
    sees public+internal; `confidential` adds confidential; the implicit
    "owner"/None audience sees everything.
    """
    if not audience:
        return False
    rank = {
        Sensitivity.PUBLIC.value: 0,
        Sensitivity.INTERNAL.value: 1,
        Sensitivity.CONFIDENTIAL.value: 2,
        Sensitivity.RESTRICTED.value: 3,
    }
    audience_rank = rank.get(audience, 3)  # unknown audience → most restrictive
    doc_rank = rank.get(doc_sensitivity, 1)
    return doc_rank > audience_rank
