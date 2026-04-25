"""Domain layer — pure dataclasses, no infra dependencies."""

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    Link,
    LinkKind,
    Project,
    Section,
    Sensitivity,
)

__all__ = [
    "Document",
    "DocumentStatus",
    "DocumentType",
    "Link",
    "LinkKind",
    "Project",
    "Section",
    "Sensitivity",
]
