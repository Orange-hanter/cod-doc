"""OBI-020: code-ref parser + resolver.

A ``[label](path)`` markdown link whose href ends in a code extension
(``.py``, ``.ts``, ``.go``, …) is classified as ``LinkKind.CODE`` instead
of ``LinkKind.MARKDOWN``. Resolver checks the file exists under project
root; ``#symbol`` fragment is substring-verified for typo-catching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import LinkKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DocumentModel,
    LinkModel,
    ProjectModel,
    SectionModel,
)
from cod_doc.services.link_service.parser import (
    _is_code_href,
    _split_code_href,
    parse,
)
from cod_doc.services.link_service.resolver import sync_section

if TYPE_CHECKING:
    from pathlib import Path

# ----------------------------------------------------------------- #
# Parser unit tests (no DB)                                          #
# ----------------------------------------------------------------- #


def test_is_code_href_recognizes_python() -> None:
    assert _is_code_href("cod_doc/services/foo.py")
    assert _is_code_href("tests/test_x.py")


def test_is_code_href_recognizes_many_languages() -> None:
    for path in (
        "src/api.ts",
        "app.tsx",
        "main.go",
        "lib.rs",
        "Foo.java",
        "Bar.kt",
        "core.c",
        "core.cpp",
        "script.sh",
        "schema.sql",
        "config.yaml",
        "page.html",
        "style.css",
        "App.vue",
    ):
        assert _is_code_href(path), path


def test_is_code_href_rejects_markdown_and_urls() -> None:
    assert not _is_code_href("docs/overview.md")
    assert not _is_code_href("https://example.com/foo")
    assert not _is_code_href("README")
    assert not _is_code_href("")


def test_is_code_href_handles_fragment() -> None:
    assert _is_code_href("cod_doc/services/foo.py#bar")
    assert _is_code_href("src/auth.ts#refresh_token")


def test_split_code_href_extracts_symbol() -> None:
    assert _split_code_href("cod_doc/foo.py#bar") == ("cod_doc/foo.py", "bar")
    assert _split_code_href("src/api.ts") == ("src/api.ts", None)


def test_split_code_href_normalizes_dot_prefixes() -> None:
    assert _split_code_href("./cod_doc/foo.py") == ("cod_doc/foo.py", None)
    assert _split_code_href("../../tests/x.py#fn") == ("tests/x.py", "fn")
    assert _split_code_href("/abs/path/foo.py") == ("abs/path/foo.py", None)


# ----------------------------------------------------------------- #
# Parser end-to-end                                                  #
# ----------------------------------------------------------------- #


def test_parse_yields_code_kind_for_python_link() -> None:
    body = "see [foo](cod_doc/services/foo.py) for details"
    parsed = parse(body)
    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind == LinkKind.CODE
    assert p.target_file_path == "cod_doc/services/foo.py"
    assert p.target_symbol is None


def test_parse_extracts_symbol_from_fragment() -> None:
    body = "call [complete](cod_doc/services/task_service.py#complete)"
    parsed = parse(body)
    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind == LinkKind.CODE
    assert p.target_file_path == "cod_doc/services/task_service.py"
    assert p.target_symbol == "complete"


def test_parse_keeps_md_link_as_markdown_kind() -> None:
    """Bare `*.md` link still treated as a document reference."""
    body = "see [overview](docs/system/overview.md)"
    parsed = parse(body)
    assert len(parsed) == 1
    assert parsed[0].kind == LinkKind.MARKDOWN
    assert parsed[0].target_file_path is None


def test_parse_mixes_code_and_doc_links() -> None:
    body = (
        "implements [task_service.complete](cod_doc/services/task_service.py#complete) "
        "per [spec](docs/system/MASTER.md)."
    )
    parsed = parse(body)
    kinds = [p.kind for p in parsed]
    assert LinkKind.CODE in kinds
    assert LinkKind.MARKDOWN in kinds


def test_parse_ignores_code_inside_fenced_block() -> None:
    body = """see real link:

    [real](cod_doc/foo.py)

    ```python
    [decoy](cod_doc/decoy.py)
    ```
    """
    parsed = parse(body)
    assert len(parsed) == 1
    assert parsed[0].target_file_path == "cod_doc/foo.py"


# ----------------------------------------------------------------- #
# Resolver — file existence fail-fast                                 #
# ----------------------------------------------------------------- #


def _seed(session, root: Path) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(
        slug="cref",
        title="P",
        root_path=str(root),
        config_json={},
    )
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    doc = DocumentModel(
        project_id=proj.row_id,
        doc_key="x/y",
        path="x/y.md",
        type="guide",
        status="active",
        title="Doc",
        sensitivity="internal",
    )
    doc.created = now
    doc.last_updated = now
    session.add(doc)
    session.flush()
    sec = SectionModel(
        document_id=doc.row_id,
        anchor="s1",
        heading="S1",
        level=2,
        position=0,
        body="",
        content_hash="0",
    )
    session.add(sec)
    session.flush()
    return proj.row_id, sec.row_id


def test_resolver_resolves_existing_file(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "real.py").write_text("def hi(): pass\n")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        sec.body = "see [hi](real.py)"
    # sync_section will parse, create a CODE link row.
    with transactional(factory) as session:
        sync_section(session, sec_id)
    # Now resolve.
    with transactional(factory) as session:
        from cod_doc.services.link_service.resolver import resolve_section

        resolved = resolve_section(session, sec_id)
    assert len(resolved) == 1
    code_link = resolved[0]
    assert code_link.kind == LinkKind.CODE
    assert code_link.resolved is True


def test_resolver_fails_fast_on_missing_file(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        sec.body = "see [ghost](no_such.py)"
    with transactional(factory) as session:
        sync_section(session, sec_id)
    with transactional(factory) as session:
        from cod_doc.services.link_service.resolver import resolve_section

        resolved = resolve_section(session, sec_id)
        # Re-read with broken_reason from DB.
        row = session.execute(
            select(LinkModel).where(LinkModel.from_section_id == sec_id)
        ).scalar_one()
    assert resolved[0].kind == LinkKind.CODE
    assert resolved[0].resolved is False
    assert "not found" in (row.broken_reason or "")


def test_resolver_resolves_when_symbol_present_in_file(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "sym.py").write_text("def magic_function(): pass\n")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        sec.body = "see [m](sym.py#magic_function)"
    with transactional(factory) as session:
        sync_section(session, sec_id)
    with transactional(factory) as session:
        from cod_doc.services.link_service.resolver import resolve_section

        resolved = resolve_section(session, sec_id)
    assert resolved[0].resolved is True


def test_resolver_resolves_line_fragment_when_in_range(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "lines.py").write_text("a\nb\nc\n")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        sec.body = "see [m](lines.py#L2-L3)"
    with transactional(factory) as session:
        sync_section(session, sec_id)
    with transactional(factory) as session:
        from cod_doc.services.link_service.resolver import resolve_section

        resolved = resolve_section(session, sec_id)
    assert resolved[0].resolved is True


def test_resolver_maps_python_module_file_to_package_init(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    package = tmp_path / "cod_doc" / "services" / "story_service"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("def create(): pass\n")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        sec.body = "see [story](cod_doc/services/story_service.py#create)"
    with transactional(factory) as session:
        sync_section(session, sec_id)
    with transactional(factory) as session:
        from cod_doc.services.link_service.resolver import resolve_section

        resolved = resolve_section(session, sec_id)
        row = session.execute(
            select(LinkModel).where(LinkModel.from_section_id == sec_id)
        ).scalar_one()
    assert resolved[0].resolved is True
    assert row.to_file_path == "cod_doc/services/story_service/__init__.py"


def test_resolver_resolves_code_ref_relative_to_source_doc(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "docs" / "system" / "adr-vision.html"
    target.parent.mkdir(parents=True)
    target.write_text("<html></html>\n")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        doc = session.get(DocumentModel, sec.document_id)
        doc.doc_key = "docs/system/audit/report"
        doc.path = "docs/system/audit/report.md"
        sec.body = "see [vision](../adr-vision.html)"
    with transactional(factory) as session:
        sync_section(session, sec_id)
    with transactional(factory) as session:
        from cod_doc.services.link_service.resolver import resolve_section

        resolved = resolve_section(session, sec_id)
        row = session.execute(
            select(LinkModel).where(LinkModel.from_section_id == sec_id)
        ).scalar_one()
    assert resolved[0].resolved is True
    assert row.to_file_path == "docs/system/adr-vision.html"


def test_resolver_fails_fast_on_missing_symbol(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "sym2.py").write_text("def other(): pass\n")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        sec.body = "see [m](sym2.py#missing_fn)"
    with transactional(factory) as session:
        sync_section(session, sec_id)
    with transactional(factory) as session:
        from cod_doc.services.link_service.resolver import resolve_section

        resolved = resolve_section(session, sec_id)
        row = session.execute(
            select(LinkModel).where(LinkModel.from_section_id == sec_id)
        ).scalar_one()
    assert resolved[0].resolved is False
    assert "missing_fn" in (row.broken_reason or "")


def test_sync_persists_file_path_and_symbol(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a.py").write_text("# nothing")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _proj_id, sec_id = _seed(session, tmp_path)
        sec = session.get(SectionModel, sec_id)
        sec.body = "see [x](a.py#foo)"
    with transactional(factory) as session:
        sync_section(session, sec_id)
    with transactional(factory) as session:
        row = session.execute(
            select(LinkModel).where(LinkModel.from_section_id == sec_id)
        ).scalar_one()
    assert row.to_file_path == "a.py"
    assert row.to_symbol == "foo"
    assert row.kind == "code"
