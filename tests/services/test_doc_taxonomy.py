"""ADO-116: правила раскладки документов по разделам. Чистые функции, без БД."""

from __future__ import annotations

import pytest

from cod_doc.domain.entities import DocumentType
from cod_doc.services.doc_taxonomy import (
    DEFAULT_RULES,
    DEFAULT_TREE,
    INBOX_KEY,
    PlacementRule,
    classify,
    node_spec,
)


@pytest.mark.parametrize(
    ("doc_key", "doc_type", "expected"),
    [
        # Тип решает, когда он говорящий.
        ("docs/system/VISION", DocumentType.VISION, "vision"),
        ("docs/system/ARCHITECTURE", DocumentType.ARCHITECTURE, "architecture"),
        ("docs/system/capabilities/x", DocumentType.CAPABILITY, "capabilities"),
        ("docs/system/scenarios/x", DocumentType.SCENARIO_SET, "scenarios"),
        ("docs/system/standards/x", DocumentType.STANDARD, "standards"),
        ("docs/system/audit/2026-01-01-x", DocumentType.AUDIT_REPORT, "audit"),
        # Каталог решает, когда тип — дефолтный module-spec импортёра.
        ("proposals/25-doc-curator-agent", DocumentType.MODULE_SPEC, "proposals"),
        ("docs/system/roadmap/ROADMAP", DocumentType.MODULE_SPEC, "roadmap"),
        ("cod_doc/skills/doc-style/SKILL", DocumentType.MODULE_SPEC, "skills"),
        ("docs/system/DATA_MODEL", DocumentType.MODULE_SPEC, "data-model"),
        # Точки входа: точный ключ и имя файла на любой глубине.
        ("AGENTS", DocumentType.MODULE_SPEC, "entry"),
        ("MASTER", DocumentType.MODULE_SPEC, "entry"),
        ("docs/system/MASTER", DocumentType.MODULE_SPEC, "entry"),
        ("docs/adoption-playbook", DocumentType.GUIDE, "entry"),
    ],
)
def test_known_shapes_land_where_expected(
    doc_key: str, doc_type: DocumentType, expected: str
) -> None:
    assert classify(doc_key, doc_type).node_key == expected


def test_index_of_a_covered_directory_stays_in_that_directory() -> None:
    """``proposals/README`` — индекс RFC, а не точка входа в проект.

    Правило по имени файла стоит последним именно ради этого: каталог,
    разобранный своим правилом, забирает и свой индекс.
    """
    assert classify("proposals/README", DocumentType.MODULE_SPEC).node_key == "proposals"


@pytest.mark.parametrize(
    "doc_key",
    [
        "session-log",
        "specs/modules",
        "plugins/cod-doc/commands/drift",
    ],
)
def test_ambiguous_documents_go_to_inbox(doc_key: str) -> None:
    placement = classify(doc_key, DocumentType.MODULE_SPEC)
    assert placement.node_key is None
    assert not placement.placed


def test_first_match_wins() -> None:
    """Порядок правил — часть контракта, а не деталь реализации."""
    rules = (
        PlacementRule(node_key="first", key_contains=("x",)),
        PlacementRule(node_key="second", key_contains=("x",)),
    )
    assert classify("axb", DocumentType.MODULE_SPEC, rules=rules).node_key == "first"


def test_conditions_inside_a_rule_are_conjunctive() -> None:
    """Здесь и проходит граница с ``nav_service``, где условия склеены через ``or``.

    Из-за ``type_ok or pat_ok`` шаг «Data Model» собирает все ``module-spec``
    корпуса — 78 документов из 170 на cod-doc. Правило с двумя условиями
    обязано требовать оба.
    """
    rule = PlacementRule(node_key="n", types=(DocumentType.VISION,), key_prefixes=("docs/",))
    assert rule.matches("docs/v", DocumentType.VISION)
    assert not rule.matches("other/v", DocumentType.VISION), "префикс не подошёл"
    assert not rule.matches("docs/v", DocumentType.GUIDE), "тип не подошёл"


def test_every_rule_points_at_a_node_of_the_default_tree() -> None:
    """Правило, ведущее в несуществующий раздел, кладёт документ в никуда."""
    known = {n.node_key for n in DEFAULT_TREE}
    unknown = sorted({r.node_key for r in DEFAULT_RULES} - known)
    assert not unknown, f"правила ссылаются на неизвестные разделы: {unknown}"


def test_default_tree_has_exactly_one_inbox() -> None:
    inboxes = [n.node_key for n in DEFAULT_TREE if n.is_inbox]
    assert inboxes == [INBOX_KEY]


def test_node_keys_are_unique() -> None:
    keys = [n.node_key for n in DEFAULT_TREE]
    assert len(keys) == len(set(keys))


def test_node_spec_resolves_by_key() -> None:
    spec = node_spec("audit")
    assert spec is not None
    assert spec.title
    assert spec.intent
    assert node_spec("no-such-node") is None
