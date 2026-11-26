"""Словарь отношений однозначен (RFC 39 §3.2, REL-004)."""

from __future__ import annotations

import pytest

from cod_doc.domain.relation_types import (
    BUILTIN_TYPES,
    RelationType,
    Vocabulary,
    VocabularyConflictError,
    levenshtein,
)
from cod_doc.domain.relations import RefKind

K = RefKind


def _vocab() -> Vocabulary:
    return Vocabulary.build(BUILTIN_TYPES)


def test_builtin_vocabulary_builds() -> None:
    assert _vocab().entries


def test_equal_specificity_collision_names_both_types() -> None:
    a = RelationType("owns", "owned_by", frozenset({K.STORY}), frozenset({K.TASK}))
    b = RelationType("owns", "x", frozenset({K.STORY}), frozenset({K.TASK}), backend="dependency")
    with pytest.raises(VocabularyConflictError, match=r"owns.*dependency"):
        Vocabulary.build([a, b])


def test_inverse_collides_with_name_of_other_type() -> None:
    a = RelationType("a", "b", frozenset({K.STORY}), frozenset({K.TASK}))
    c = RelationType("b", "c", frozenset({K.TASK}), frozenset({K.STORY}))
    with pytest.raises(VocabularyConflictError):
        Vocabulary.build([a, c])


def test_narrow_pair_beats_wildcard() -> None:
    wide = RelationType("link", "linked", frozenset(RefKind), frozenset(RefKind))
    narrow = RelationType("link", "linked", frozenset({K.TASK}), frozenset({K.TASK}))
    v = Vocabulary.build([wide, narrow])
    assert v.lookup("link", K.TASK, K.TASK).type is narrow  # type: ignore[union-attr]
    assert v.lookup("link", K.STORY, K.TASK).type is wide  # type: ignore[union-attr]


def test_blocked_by_task_task_is_dependency() -> None:
    res = _vocab().lookup("blocked_by", K.TASK, K.TASK)
    assert res is not None
    assert res.type.backend == "dependency"
    assert not res.reversed


def test_blocked_by_task_question_is_reversed_question_blocks() -> None:
    res = _vocab().lookup("blocked_by", K.TASK, K.QUESTION)
    assert res is not None
    assert (res.type.name, res.type.backend, res.reversed) == ("blocks", "relation", True)
    assert _vocab().lookup("blocks", K.QUESTION, K.TASK).reversed is False  # type: ignore[union-attr]


def test_see_also_alias_and_symmetry() -> None:
    v = _vocab()
    res = v.lookup("see_also", K.STORY, K.STORY)
    assert res is not None
    assert (res.type.name, res.reversed) == ("relates_to", False)
    assert v.lookup("relates_to", K.URL, K.STORY) is None  # url — не сущность


def test_same_kind_duplicates_only() -> None:
    v = _vocab()
    assert v.lookup("duplicates", K.TASK, K.TASK) is not None
    assert v.lookup("duplicates", K.TASK, K.STORY) is None


def test_allowed_and_suggest() -> None:
    v = _vocab()
    names = {t.name for t in v.allowed(K.STORY, K.STORY)}
    assert {"relates_to", "depends_on", "duplicates", "splits_into"} <= names
    assert "depends_on" in v.suggest("depend_on", K.STORY, K.STORY)
    assert v.suggest("zzzzzz", K.STORY, K.STORY) == []


def test_levenshtein() -> None:
    assert levenshtein("", "ab") == 2
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("same", "same") == 0
