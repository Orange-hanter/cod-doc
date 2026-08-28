"""FindingService — fingerprint / dedup / promote for external findings."""

from __future__ import annotations

from cod_doc.services.finding_service.dedup import FindingSeed, IngestResult, ingest_findings
from cod_doc.services.finding_service.fingerprint import (
    fingerprint_ai_review,
    fingerprint_routine,
    fingerprint_zairgrush,
)
from cod_doc.services.finding_service.promote import promote_finding
from cod_doc.services.finding_service.queries import (
    dismiss_finding,
    finding_to_dict,
    get_finding,
    list_findings,
)

__all__ = [
    "FindingSeed",
    "IngestResult",
    "dismiss_finding",
    "finding_to_dict",
    "fingerprint_ai_review",
    "fingerprint_routine",
    "fingerprint_zairgrush",
    "get_finding",
    "ingest_findings",
    "list_findings",
    "promote_finding",
]
