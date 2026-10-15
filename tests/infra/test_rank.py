"""Свойства дробного ранга (CLO-003, RFC 29 §3.2)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cod_doc.infra.rank import DIGITS, between, initial

_RANK = st.text(alphabet=DIGITS, min_size=1, max_size=6).filter(lambda s: s.rstrip("0"))


@given(st.integers(min_value=0, max_value=3000))
def test_initial_is_strictly_increasing_without_trailing_zero(n: int) -> None:
    ranks = initial(n)
    assert len(ranks) == n
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == n
    assert all(r and not r.endswith("0") for r in ranks)


@given(_RANK, _RANK)
def test_between_lands_strictly_inside(x: str, y: str) -> None:
    a, b = sorted((x, y))
    if a.rstrip("0") == b.rstrip("0"):
        with pytest.raises(ValueError, match="no rank between"):
            between(a, b)
        return
    mid = between(a, b)
    assert a < mid < b
    assert not mid.endswith("0")


@given(_RANK)
def test_edges(x: str) -> None:
    assert between(x, None) > x
    assert between(None, x) < x


@settings(max_examples=50)
@given(st.lists(st.integers(min_value=0), min_size=1, max_size=200))
def test_repeated_inserts_keep_order(choices: list[int]) -> None:
    """Любая последовательность вставок даёт строго возрастающий список."""
    ranks: list[str] = []
    for choice in choices:
        index = choice % (len(ranks) + 1)
        left = ranks[index - 1] if index else None
        right = ranks[index] if index < len(ranks) else None
        ranks.insert(index, between(left, right))
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_appends_grow_slowly() -> None:
    ranks = [between(None, None)]
    for _ in range(1000):
        ranks.append(between(ranks[-1], None))
    assert ranks == sorted(ranks)
    assert max(map(len, ranks)) <= 40


@pytest.mark.parametrize(("a", "b"), [("b", "a"), ("a", "a"), ("a", "a0"), (None, "0")])
def test_between_rejects_empty_interval(a: str | None, b: str) -> None:
    with pytest.raises(ValueError, match="no rank between"):
        between(a, b)


def test_between_rejects_foreign_alphabet() -> None:
    with pytest.raises(ValueError, match="base-36"):
        between("A", None)
