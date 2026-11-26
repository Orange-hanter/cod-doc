"""Встроенный тип без читателя — метка, а не тип (RFC 39 §3.2, REL-004/REL-011)."""

from __future__ import annotations

import importlib

from cod_doc.domain.relation_types import BUILTIN_TYPES, KNOWN_CONSUMERS, PLANNED_CONSUMERS


def test_every_builtin_type_has_a_real_consumer() -> None:
    bad = [t.name for t in BUILTIN_TYPES if not t.consumers & KNOWN_CONSUMERS]
    assert bad == []


def test_consumers_are_known_or_planned() -> None:
    unknown = {c for t in BUILTIN_TYPES for c in t.consumers} - KNOWN_CONSUMERS - PLANNED_CONSUMERS
    assert unknown == set()


def test_known_and_planned_do_not_overlap() -> None:
    assert not KNOWN_CONSUMERS & PLANNED_CONSUMERS


def test_known_consumers_resolve_to_real_callables() -> None:
    """«Реальный» — значит, путь указывает на существующую функцию сервиса."""
    for dotted in KNOWN_CONSUMERS:
        module, _, attr = dotted.rpartition(".")
        mod = importlib.import_module(f"cod_doc.services.{module}")
        assert callable(getattr(mod, attr)), dotted
