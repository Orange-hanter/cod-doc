"""ADO-199: валидаторы секции плана и генератор слага — литеральные кейсы.

Эталоны — литералы: построить ожидаемое значение вызовом ``plan_section_slug``
или регулярками из ``_patterns`` значит проверить функцию ею же самой.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cod_doc.services import validation as v

if TYPE_CHECKING:
    from collections.abc import Callable


def _code(fn: Callable[..., object], *args: object) -> str:
    with pytest.raises(v.ValidationError) as exc:
        fn(*args)
    return exc.value.code


def test_title_rules() -> None:
    for bad in ["", "   ", "x" * 257, "a\nb", "a\x00b"]:
        assert _code(v.validate_plan_section_title, bad) == "PS-001", repr(bad)
    v.validate_plan_section_title("x" * 256)
    v.validate_plan_section_title("Structure protocol (RFC 24)")


def test_letter_rules() -> None:
    v.validate_plan_section_letter("A")
    v.validate_plan_section_letter("AB")
    for bad in ["a", "ABC", "1", "", "A-"]:
        assert _code(v.validate_plan_section_letter, bad) == "PS-002", repr(bad)


def test_position_rules() -> None:
    v.validate_plan_section_position(0)
    v.validate_plan_section_position(5)
    for bad in [-1, True, 1.0, "1"]:
        assert _code(v.validate_plan_section_position, bad) == "PS-003", repr(bad)


@pytest.mark.parametrize(
    ("letter", "title", "expected"),
    [
        ("F", "Structure protocol (RFC 24)", "F-Structure-protocol-RFC-24"),
        ("A", "Data & Core", "A-Data-Core"),
        ("B", "Протокол", "B-Section"),
        ("AB", "  --x--  ", "AB-x"),
        ("C", "Café", "C-Cafe"),
    ],
)
def test_slug_literals(letter: str, title: str, expected: str) -> None:
    assert v.plan_section_slug(letter, title) == expected


def test_slug_length_cap() -> None:
    result = v.plan_section_slug("A", "word-" * 40)
    assert len(result) <= 62
    assert not result.endswith("-")
    v.validate_section_slug(result)


def test_slug_rejects_bad_letter() -> None:
    assert _code(v.plan_section_slug, "a", "X") == "PS-002"
    assert _code(v.plan_section_slug, "ABC", "X") == "PS-002"
