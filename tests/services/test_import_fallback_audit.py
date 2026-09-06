"""TY-001: import fallback type=module-spec status=draft with no authored type:."""

from types import SimpleNamespace

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.services.validation import audit_import_fallback, is_import_fallback


def test_authored_type_is_not_fallback() -> None:
    assert not is_import_fallback(
        type="module-spec",
        status="draft",
        frontmatter={"type": "guide"},
    )


def test_fallback_pair_without_type_key() -> None:
    assert is_import_fallback(
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        frontmatter={"Документ": "PRD"},
    )


def test_classified_module_spec_active_is_ok() -> None:
    assert not is_import_fallback(
        type="module-spec",
        status="active",
        frontmatter={},
    )


def test_corpus_emits_one_ty001() -> None:
    docs = [
        SimpleNamespace(type="module-spec", status="draft", frontmatter={}, doc_key="a"),
        SimpleNamespace(type="vision", status="active", frontmatter={}, doc_key="b"),
        SimpleNamespace(type="module-spec", status="draft", frontmatter={}, doc_key="c"),
    ]
    issues = audit_import_fallback(docs)
    assert len(issues) == 1
    assert issues[0].code == "TY-001"
    assert issues[0].severity == "error"
    assert issues[0].details["count"] == 2
    assert issues[0].details["sample"] == ["a", "c"]


def test_empty_corpus_is_clean() -> None:
    assert audit_import_fallback([]) == []
    docs = [
        SimpleNamespace(type="module-spec", status="active", frontmatter={}, doc_key="spec"),
    ]
    assert audit_import_fallback(docs) == []
