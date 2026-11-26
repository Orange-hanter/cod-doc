"""Встроенный тип без читателя — метка, а не тип (RFC 39 §3.2, REL-004)."""

from __future__ import annotations

from cod_doc.domain.relation_types import BUILTIN_TYPES, KNOWN_CONSUMERS


def test_every_builtin_type_has_a_known_consumer() -> None:
    bad = [t.name for t in BUILTIN_TYPES if not t.consumers or not t.consumers <= KNOWN_CONSUMERS]
    assert bad == []
