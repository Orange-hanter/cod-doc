"""JSON-представление ответов `relation_service` — общее для MCP и CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.domain.relations import ENTITY_KINDS

if TYPE_CHECKING:
    from cod_doc.domain.relations import RefKind

    from ._types import RelationEdge, RelationTypeInfo, RelationView, VerifyReport


def edge_to_dict(edge: RelationEdge) -> dict[str, Any]:
    """Ребро как записано: канонический тип, направление хранения, оба конца."""
    return {
        "type": edge.type,
        "from": str(edge.from_ref),
        "to": str(edge.to_ref),
        "edge_uid": edge.uid,
        "from_uid": edge.from_uid,
        "to_uid": edge.to_uid,
        "note": edge.note,
        "resolved": edge.resolved,
        "reversed": edge.reversed,
        "created": edge.created,
    }


def view_to_dict(view: RelationView) -> dict[str, Any]:
    return {
        "label": view.label,
        "type": view.type,
        "direction": view.direction,
        "other": view.other_ref,
        "other_title": view.other_title,
        "source": view.source,
        "resolved": view.resolved,
        "note": view.note,
    }


def _kinds(kinds: frozenset[RefKind]) -> list[str]:
    """Полный набор видов сущностей — `["*"]`: двенадцать имён в каждой строке — шум."""
    return ["*"] if kinds == ENTITY_KINDS else sorted(k.value for k in kinds)


def type_info_to_dict(info: RelationTypeInfo) -> dict[str, Any]:
    t = info.type
    return {
        "name": t.name,
        "inverse": t.inverse,
        "from_kinds": _kinds(t.from_kinds),
        "to_kinds": _kinds(t.to_kinds),
        "symmetric": t.symmetric,
        "acyclic": t.acyclic,
        "allow_forward": t.allow_forward,
        "backend": t.backend,
        "aliases": sorted(t.aliases),
        "description": t.description,
        "builtin": info.builtin,
        "deprecated": info.deprecated,
        "edges": info.edges,
    }


def report_to_dict(report: VerifyReport) -> dict[str, Any]:
    return {
        "checked": report.checked,
        "resolved": report.resolved,
        "filled": report.filled,
        "broken": report.broken,
    }
