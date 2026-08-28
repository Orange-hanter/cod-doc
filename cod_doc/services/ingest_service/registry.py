"""Ingest adapter registry.

External exports are parsed by named adapters. The registry is the single
entry point used by the CLI and future API surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TextIO

from cod_doc.services.ingest_service.ai_review import AiReviewAdapter
from cod_doc.services.ingest_service.zairgrush_findings import (
    ZairgrushFindingsAdapter,
)
from cod_doc.services.ingest_service.zairgrush_tasks import (
    ZairgrushTasksAdapter,
)

if TYPE_CHECKING:
    from cod_doc.services.ingest_service.models import RawFinding


class Adapter(Protocol):
    """Protocol for an ingest adapter.

    An adapter turns an external export stream into a list of raw findings.
    It must not compute fingerprints; it only extracts and normalizes fields.
    """

    def parse(self, stream: TextIO) -> list[RawFinding]: ...


INGEST_ADAPTERS: dict[str, Adapter] = {
    "ai_review": AiReviewAdapter(),
    "zairgrush_findings": ZairgrushFindingsAdapter(),
    "zairgrush_tasks": ZairgrushTasksAdapter(),
}


def lookup_adapter(name: str) -> Adapter:
    """Return the adapter registered under ``name``.

    Raises:
        KeyError: if no adapter is registered for ``name``.
    """
    try:
        return INGEST_ADAPTERS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown ingest adapter: {name}") from exc
