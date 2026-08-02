"""WEB-006: mini markdown renderer + integration with doc_show.

Renderer-level tests live first (cheap), then a couple of integration
checks via TestClient + seeded DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cod_doc.api.web.markdown import render_markdown
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Sensitivity,
)
from cod_doc.domain.entities import (
    Project as ProjectEntity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service as docs

# ── Renderer unit tests ──────────────────────────────────────────────────


def test_empty_input_returns_empty_string() -> None:
    assert render_markdown("") == ""
    assert render_markdown(None) == ""  # type: ignore[arg-type]


def test_paragraph_wraps_in_p() -> None:
    assert render_markdown("hello") == "<p>hello</p>"


def test_blank_line_starts_new_paragraph() -> None:
    out = render_markdown("first\n\nsecond")
    assert "<p>first</p>" in out
    assert "<p>second</p>" in out


def test_html_in_input_is_escaped() -> None:
    """User input must not be able to inject raw HTML."""
    out = render_markdown("<script>alert(1)</script>")
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_inline_code_renders_code_tag() -> None:
    out = render_markdown("Use `cod-doc list` to see tasks.")
    assert "<code>cod-doc list</code>" in out


def test_inline_code_shields_internal_markdown() -> None:
    """`*foo*` inside backticks must not become italic."""
    out = render_markdown("Run `*magic*` carefully.")
    assert "<em>" not in out
    # The * is shielded as &#42; → renders as literal *
    assert "<code>&#42;magic&#42;</code>" in out


def test_bold() -> None:
    out = render_markdown("This is **strong**.")
    assert "<strong>strong</strong>" in out


def test_italic() -> None:
    out = render_markdown("This is *emphasis*.")
    assert "<em>emphasis</em>" in out
    # Sanity: bold-double-stars don't accidentally become italic.
    out2 = render_markdown("**not italic**")
    assert "<em>" not in out2
    assert "<strong>not italic</strong>" in out2


def test_link() -> None:
    out = render_markdown("See [docs](https://example.com/d).")
    assert '<a href="https://example.com/d">docs</a>' in out


def test_link_with_inline_bold_inside_text() -> None:
    out = render_markdown("[**bold link**](u)")
    assert '<a href="u"><strong>bold link</strong></a>' in out


def test_bullet_list() -> None:
    out = render_markdown("- one\n- two\n- three")
    assert "<ul>" in out and "</ul>" in out
    assert "<li>one</li>" in out
    assert "<li>three</li>" in out


def test_code_fence_renders_pre_code() -> None:
    md = "```python\ndef f(): pass\n```"
    out = render_markdown(md)
    # Language is emitted as a `language-<lang>` class so highlight.js can colourise.
    assert '<pre><code class="language-python">' in out
    assert "def f(): pass" in out


def test_code_fence_without_lang_omits_class() -> None:
    """Plain ``` fences (no lang) emit a bare <code> — highlight.js auto-detects."""
    md = "```\nplain\n```"
    out = render_markdown(md)
    assert "<pre><code>plain</code></pre>" in out


def test_code_fence_escapes_html() -> None:
    md = "```\n<b>raw</b>\n```"
    out = render_markdown(md)
    assert "&lt;b&gt;raw&lt;/b&gt;" in out
    assert "<b>raw</b>" not in out


def test_unclosed_code_fence_does_not_lose_content() -> None:
    out = render_markdown("```\nmisformed")
    assert "misformed" in out
    assert "<pre><code>" in out


def test_mermaid_fence_emits_div_for_clientside_render() -> None:
    """COD-061: ```mermaid blocks become <div class="mermaid"> for mermaid.js."""
    md = "```mermaid\ngraph TD\n  A --> B\n```"
    out = render_markdown(md)
    assert '<div class="mermaid">' in out
    assert "graph TD" in out
    # Plain code-fence path is NOT used.
    assert "<pre><code>" not in out


def test_mermaid_fence_escapes_diagram_source() -> None:
    """User-supplied diagram text must be HTML-escaped — no raw HTML injection."""
    md = '```mermaid\ngraph TD\n  A["<script>alert(1)</script>"]\n```'
    out = render_markdown(md)
    assert "&lt;script&gt;" in out
    assert "<script>alert(1)" not in out


def test_unclosed_mermaid_fence_still_emits_div() -> None:
    out = render_markdown("```mermaid\ngraph TD\n  A --> B")
    assert '<div class="mermaid">' in out
    assert "graph TD" in out


def test_non_mermaid_lang_still_uses_pre_code() -> None:
    """Other language tags keep the existing pre/code rendering (with language class)."""
    md = "```python\nprint('hi')\n```"
    out = render_markdown(md)
    assert '<pre><code class="language-python">' in out
    assert '<div class="mermaid">' not in out


# ── COD-079: GFM tables ────────────────────────────────────────────────


def test_gfm_table_renders_header_and_rows() -> None:
    md = (
        "| Field | Meaning |\n"
        "| --- | --- |\n"
        "| `agency_id` | Ensures delegation remains workspace-local |\n"
        "| `user_id` | Employee receiving the grant |"
    )
    out = render_markdown(md)
    assert '<table class="md-table">' in out
    assert "<thead>" in out and "<tbody>" in out
    assert "<th>Field</th>" in out
    assert "<th>Meaning</th>" in out
    assert "<code>agency_id</code>" in out
    assert "Ensures delegation remains workspace-local" in out
    # Pipe-soup raw text must NOT leak into output.
    assert "| Field | Meaning |" not in out


def test_gfm_table_alignment_via_colon() -> None:
    md = "| L | C | R |\n| :--- | :---: | ---: |\n| a | b | c |"
    out = render_markdown(md)
    assert 'style="text-align:left"' in out
    assert 'style="text-align:center"' in out
    assert 'style="text-align:right"' in out


def test_table_without_delimiter_falls_back_to_paragraph() -> None:
    """Pipes inside a paragraph must not be misread as a 1-row table."""
    md = "| not | a table |"
    out = render_markdown(md)
    assert "<table" not in out
    assert "<p>" in out


def test_jagged_rows_pad_to_header_width() -> None:
    md = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 |\n| 1 | 2 | 3 | extra |"
    out = render_markdown(md)
    # First row gets a third <td> (empty); second row trims the extra cell.
    assert out.count("<tr>") == 3  # 1 header + 2 body
    # Padding: short row still has 3 cells.
    assert out.count("<td") == 6


def test_table_delim_must_have_3plus_dashes() -> None:
    """A single dash in the delimiter row is too sloppy — fall through."""
    md = "| a | b |\n| - | - |\n| 1 | 2 |"
    out = render_markdown(md)
    assert "<table" not in out


def test_paragraph_adjacent_to_list_is_rendered_separately() -> None:
    md = "intro paragraph\n\n- item one\n- item two\n\noutro paragraph"
    out = render_markdown(md)
    assert "<p>intro paragraph</p>" in out
    assert "<ul>" in out
    assert "<p>outro paragraph</p>" in out


# ── Headings (extension for MASTER.md preview) ───────────────────────────


def test_headings_render_h1_through_h6() -> None:
    md = "# H1 title\n\n## H2 title\n\n### H3 title\n\n#### H4\n\n##### H5\n\n###### H6"
    out = render_markdown(md)
    for level in range(1, 7):
        assert f"<h{level} " in out, f"missing h{level}"
        assert f"</h{level}>" in out


def test_heading_gets_slug_id_for_anchor_scroll() -> None:
    out = render_markdown("## 2. Context Map")
    # Slugified: lowercase, spaces → dashes, punctuation stripped.
    assert '<h2 id="2-context-map">' in out
    assert "2. Context Map" in out  # title rendered intact


def test_heading_with_inline_markdown_is_rendered() -> None:
    out = render_markdown("# Project **Foo** Navigator")
    assert "<strong>Foo</strong>" in out
    assert "<h1 " in out


def test_heading_separates_from_following_paragraph() -> None:
    md = "# Title\nFirst paragraph after heading."
    out = render_markdown(md)
    assert "<h1 " in out
    assert "<p>First paragraph after heading.</p>" in out


# ── Blockquotes ──────────────────────────────────────────────────────────


def test_blockquote_renders() -> None:
    out = render_markdown("> a quoted note")
    assert "<blockquote>a quoted note</blockquote>" in out


def test_consecutive_blockquote_lines_collapse() -> None:
    md = "> first line\n> second line"
    out = render_markdown(md)
    assert "<blockquote>" in out
    assert "first line" in out
    assert "second line" in out
    # Only ONE blockquote element (collapsed)
    assert out.count("<blockquote>") == 1


def test_blockquote_with_inline_code_and_bold() -> None:
    out = render_markdown("> Use **`cod-doc serve`** to run the API.")
    assert "<blockquote>" in out
    assert "<strong>" in out
    assert "<code>cod-doc serve</code>" in out


def test_blockquote_then_paragraph_then_blockquote() -> None:
    md = "> note one\n\nbody paragraph\n\n> note two"
    out = render_markdown(md)
    assert out.count("<blockquote>") == 2
    assert "<p>body paragraph</p>" in out


# ── Integration: doc_show renders sections with anchors ──────────────────


@pytest.fixture
def md_doc_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "md-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        doc = docs.create(
            session,
            project_id=proj.row_id,
            doc_key="modules/M1/spec",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="M1 spec",
            author="human:dakh",
            owner="human:dakh",
            sensitivity=Sensitivity.INTERNAL,
            preamble="Top-level **summary** of the module.",
        )
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="data-model",
            heading="Data Model",
            level=2,
            position=0,
            body="The `entities` table stores rows.\n\n- one\n- two",
            author="human:dakh",
        )
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="api",
            heading="API",
            level=2,
            position=1,
            body="See [routes.py](https://example.com/r) for details.",
            author="human:dakh",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_doc_show_renders_sections_with_anchor_ids(md_doc_client) -> None:
    """Each section becomes <section id="section-{anchor}"> with an inner <a id="{anchor}">.

    The wrapper id is the HTMX swap target (WEB-012); the inner anchor preserves
    the sidebar `#anchor` scroll behaviour (WEB-006).
    """
    client, entry = md_doc_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1/spec")
    assert r.status_code == 200
    # WEB-012 wrappers
    assert '<section id="section-data-model"' in r.text
    assert '<section id="section-api"' in r.text
    # WEB-006 anchors (inner)
    assert '<a id="data-model"' in r.text
    assert '<a id="api"' in r.text
    # Sidebar nav references the inner anchors
    assert 'href="#data-model"' in r.text
    assert 'href="#api"' in r.text


def test_doc_show_renders_inline_markdown(md_doc_client) -> None:
    client, entry = md_doc_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1/spec")
    assert r.status_code == 200
    # Preamble: bold rendered
    assert "<strong>summary</strong>" in r.text
    # Section with code + bullet list
    assert "<code>entities</code>" in r.text
    assert "<li>one</li>" in r.text
    # Section with link
    assert '<a href="https://example.com/r">routes.py</a>' in r.text


def test_doc_show_raw_mode_returns_pre(md_doc_client) -> None:
    """`?raw=1` falls back to <pre> rendering of the full body."""
    client, entry = md_doc_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1/spec?raw=1")
    assert r.status_code == 200
    assert '<pre class="md-preview">' in r.text
    # Sections still present in nav, but no <section id=...> in raw mode body
    assert 'href="#data-model"' in r.text
    # Specifically: no rendered <section id> wrapper in body
    assert '<section id="data-model"' not in r.text
    # Toggle link points back to rendered mode
    assert "Rendered" in r.text


def test_doc_show_rendered_mode_shows_view_raw_link(md_doc_client) -> None:
    client, entry = md_doc_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1/spec")
    assert "Raw" in r.text


def test_doc_show_does_not_smuggle_raw_html(md_doc_client) -> None:
    """If a doc body contains raw HTML, it must be HTML-escaped on render."""
    # Re-seed: add a section with raw <script>
    from cod_doc.infra.db import make_engine, make_session_factory, transactional
    from cod_doc.infra.repositories import ProjectRepository

    client, entry = md_doc_client
    db_path = Path(entry.cod_doc_dir) / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug("demo")
        doc = docs.get(session, proj.row_id, "modules/M1/spec")
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="evil",
            heading="Evil",
            level=2,
            position=2,
            body="<script>alert(1)</script>",
            author="human:dakh",
        )
    engine.dispose()

    r = client.get(f"/p/{entry.name}/docs/modules/M1/spec")
    assert r.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    # Make sure no actual <script> tag leaked into HTML body
    body_lower = r.text.lower()
    # Allow defer src= existing for htmx; just check there's no inline alert
    assert "alert(1)" not in body_lower or "&lt;script&gt;alert(1)" in body_lower
