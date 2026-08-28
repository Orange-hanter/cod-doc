"""Ingest service: adapters for external findings."""

from __future__ import annotations

from cod_doc.services.ingest_service.models import RawFinding
from cod_doc.services.ingest_service.registry import (
    INGEST_ADAPTERS,
    Adapter,
    lookup_adapter,
)

__all__ = [
    "INGEST_ADAPTERS",
    "Adapter",
    "RawFinding",
    "lookup_adapter",
]
