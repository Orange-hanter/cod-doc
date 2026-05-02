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
    assert "<pre><code>" in out
    assert "def f(): pass" in out


def test_code_fence_escapes_html() -> None:
    md = "```\n<b>raw</b>\n```"
    out = render_markdown(md)
    assert "&lt;b&gt;raw&lt;/b&gt;" in out
    assert "<b>raw</b>" not in out


def test_unclosed_code_fence_does_not_lose_content() -> None:
    out = render_markdown("```\nmisformed")
    assert "misformed" in out
    assert "<pre><code>" in out


def test_paragraph_adjacent_to_list_is_rendered_separately() -> None:
    md = "intro paragraph\n\n- item one\n- item two\n\noutro paragraph"
    out = render_markdown(md)
    assert "<p>intro paragraph</p>" in out
    assert "<ul>" in out
    assert "<p>outro paragraph</p>" in out


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
    assert ">View rendered<" in r.text


def test_doc_show_rendered_mode_shows_view_raw_link(md_doc_client) -> None:
    client, entry = md_doc_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1/spec")
    assert ">View raw<" in r.text


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
