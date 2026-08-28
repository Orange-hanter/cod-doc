"""Raw finding model: pre-fingerprint container for ingest adapters.

Adapters only collect raw fields; ``RawFinding.to_seed`` turns them into a
``FindingSeed`` by calling the pure fingerprint functions in
``finding_service``. This keeps fingerprint logic out of adapters and keeps
adapters portable across export-format versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cod_doc.services.finding_service import (
    FindingSeed,
    fingerprint_ai_review,
    fingerprint_zairgrush,
)


@dataclass(frozen=True, slots=True)
class RawFinding:
    """A finding captured from an external export, before fingerprinting.

    Fields are a superset of ``FindingSeed`` plus the raw inputs required by
    the fingerprint functions. Adapters must not compute fingerprints; they
    populate these fields and let ``to_seed`` do the rest.
    """

    source: str
    title: str
    severity: str
    source_ref: str | None = None
    kind: str | None = None
    body: str | None = None
    path: str | None = None
    line: int | None = None
    confidence: float | None = None
    fp: str | None = None
    exp: str | None = None
    variant: str | None = None
    payload: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    def to_seed(self) -> FindingSeed:
        """Convert to a fingerprinted ``FindingSeed`` ready for dedup ingest."""
        if self.source == "ai_review":
            fingerprint, basis = fingerprint_ai_review(
                path=self.path,
                fp=self.fp,
                severity=self.severity,
                title=self.title,
            )
        elif self.source == "zairgrush":
            fingerprint, basis = fingerprint_zairgrush(
                exp=self.exp or "",
                variant=self.variant or "",
                kind=self.kind or "",
            )
        else:
            raise ValueError(f"Cannot compute fingerprint for unsupported source: {self.source!r}")

        merged_payload: dict[str, Any] = {**(self.payload or {}), **basis}
        return FindingSeed(
            fingerprint=fingerprint,
            source=self.source,
            title=self.title,
            severity=self.severity,
            source_ref=self.source_ref,
            kind=self.kind,
            body=self.body,
            path=self.path,
            line=self.line,
            confidence=self.confidence,
            payload=merged_payload,
            raw=self.raw,
        )
