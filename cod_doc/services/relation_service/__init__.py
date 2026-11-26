"""Граф связей: единый вход для записи и чтения рёбер (RFC 39 §3.4).

Модуль ничего не отдаёт сервисам-источникам: удаления сущностей приходят
событием (`entity_events`, REL-007), поэтому ни один сервис не импортирует
`relation_service`.
"""

from __future__ import annotations

from ._types import (
    RelationEdge,
    RelationError,
    RelationTypeInfo,
    RelationTypeSpec,
    RelationView,
    VerifyReport,
)
from .edges import relate, relations, unrelate
from .serialize import edge_to_dict, report_to_dict, type_info_to_dict, view_to_dict
from .types import define_type, deprecate_type, relation_types
from .verify import verify

__all__ = [
    "RelationEdge",
    "RelationError",
    "RelationTypeInfo",
    "RelationTypeSpec",
    "RelationView",
    "VerifyReport",
    "define_type",
    "deprecate_type",
    "edge_to_dict",
    "relate",
    "relation_types",
    "relations",
    "report_to_dict",
    "type_info_to_dict",
    "unrelate",
    "verify",
    "view_to_dict",
]
