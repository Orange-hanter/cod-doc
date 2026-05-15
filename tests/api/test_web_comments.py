"""Comments — section-bubbles + doc-level zone CRUD + AI rework preview."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import comment_service as comments
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def comments_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "demo-repo"
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
            doc_key="guides/intro",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Intro",
            author="human:test",
            owner="human:test",
            sensitivity=Sensitivity.INTERNAL,
            preamble="Intro preamble.",
        )
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="overview",
            heading="Overview",
            level=2,
            position=0,
            body="Body of overview.",
            author="human:test",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry, db_path


def _engine(db_path):
    e = make_engine(f"sqlite:///{db_path}")
    f = make_session_factory(e)
    return e, f


def test_create_section_comment_via_post(comments_client) -> None:
    client, entry, db_path = comments_client
    r = client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"anchor": "overview", "body": "Add a code example", "quote": "Body of overview."},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Section comments redirect back to the section anchor so the
    # viewport stays put; doc-level ones go to #comments (see other test).
    assert r.headers["location"].endswith("#overview")

    e, f = _engine(db_path)
    with transactional(f) as s:
        # Project + doc lookup via service.
        proj_id = ProjectRepository(s).get_by_slug("demo").row_id
        doc = docs.get(s, proj_id, "guides/intro")
        rows = comments.list_for_document(s, doc.row_id)
    e.dispose()
    assert len(rows) == 1
    assert rows[0].anchor == "overview"
    assert rows[0].body == "Add a code example"
    assert rows[0].quote == "Body of overview."
    assert rows[0].status == "open"


def test_create_doc_level_comment(comments_client) -> None:
    client, entry, db_path = comments_client
    r = client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"body": "Document needs a glossary"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    e, f = _engine(db_path)
    with transactional(f) as s:
        proj_id = ProjectRepository(s).get_by_slug("demo").row_id
        doc = docs.get(s, proj_id, "guides/intro")
        rows = comments.list_for_document(s, doc.row_id)
    e.dispose()
    assert len(rows) == 1
    assert rows[0].section_id is None
    assert rows[0].anchor is None
    assert rows[0].body == "Document needs a glossary"


def test_comment_resolve_and_reopen(comments_client) -> None:
    client, entry, db_path = comments_client
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"anchor": "overview", "body": "Tighten this"},
        follow_redirects=False,
    )

    e, f = _engine(db_path)
    with transactional(f) as s:
        proj_id = ProjectRepository(s).get_by_slug("demo").row_id
        doc = docs.get(s, proj_id, "guides/intro")
        cid = comments.list_for_document(s, doc.row_id)[0].row_id
    e.dispose()

    r = client.post(
        f"/p/{entry.name}/docs/guides/intro/comments/{cid}/resolve",
        follow_redirects=False,
    )
    assert r.status_code == 303

    e, f = _engine(db_path)
    with transactional(f) as s:
        c = comments.get(s, cid)
    e.dispose()
    assert c.status == "resolved"

    r = client.post(
        f"/p/{entry.name}/docs/guides/intro/comments/{cid}/reopen",
        follow_redirects=False,
    )
    assert r.status_code == 303
    e, f = _engine(db_path)
    with transactional(f) as s:
        c = comments.get(s, cid)
    e.dispose()
    assert c.status == "open"


def test_comment_delete(comments_client) -> None:
    client, entry, db_path = comments_client
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"body": "Doc-level note"},
        follow_redirects=False,
    )
    e, f = _engine(db_path)
    with transactional(f) as s:
        proj_id = ProjectRepository(s).get_by_slug("demo").row_id
        doc = docs.get(s, proj_id, "guides/intro")
        cid = comments.list_for_document(s, doc.row_id)[0].row_id
    e.dispose()

    r = client.post(
        f"/p/{entry.name}/docs/guides/intro/comments/{cid}/delete",
        follow_redirects=False,
    )
    assert r.status_code == 303
    e, f = _engine(db_path)
    with transactional(f) as s:
        assert comments.get(s, cid) is None
    e.dispose()


def test_doc_show_renders_comment_badge_and_doclevel_zone(comments_client) -> None:
    client, entry, _ = comments_client
    # Seed one section comment + one doc-level.
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"anchor": "overview", "body": "Improve this section"},
    )
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"body": "Add references section at the end"},
    )
    r = client.get(f"/p/{entry.name}/docs/guides/intro")
    assert r.status_code == 200
    html = r.text
    # Section bubble
    assert "Improve this section" in html
    assert 'class="section-comments"' in html
    # Doc-level zone
    assert "Add references section at the end" in html
    assert "ds-comment-doclevel-list" in html
    # Counter chip
    assert "open" in html


def test_comments_json_returns_all(comments_client) -> None:
    client, entry, _ = comments_client
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"anchor": "overview", "body": "First"},
    )
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"body": "Second"},
    )
    r = client.get(f"/p/{entry.name}/docs/guides/intro/comments.json")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {row["body"] for row in rows} == {"First", "Second"}


def test_apply_with_ai_renders_preview_with_drafts(comments_client, monkeypatch) -> None:
    """The apply endpoint groups open comments by section and calls the LLM
    per section. We monkeypatch the LLM call to verify routing without
    network."""
    client, entry, _ = comments_client

    # Seed one section comment.
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"anchor": "overview", "body": "Add concrete examples", "quote": "Body of overview."},
    )
    # And one doc-level (for the summary block).
    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"body": "Add glossary"},
    )

    def fake_rewrite(body, comments_list, cfg):
        joined = " | ".join(comments_list)
        return f"REWRITTEN[{body}]<{joined}>"

    monkeypatch.setattr(comments, "_call_rewrite", fake_rewrite)

    r = client.post(f"/p/{entry.name}/docs/guides/intro/comments/apply")
    assert r.status_code == 200
    html = r.text
    assert "REWRITTEN[Body of overview.]" in html
    assert "Add concrete examples" in html
    assert "Add glossary" in html  # doc-level summary
    # Hidden anchor field for the commit form
    assert 'name="apply_anchor"' in html
    assert 'value="overview"' in html


def test_apply_with_ai_commit_patches_sections_and_marks_applied(
    comments_client, monkeypatch
) -> None:
    client, entry, db_path = comments_client

    client.post(
        f"/p/{entry.name}/docs/guides/intro/comments",
        data={"anchor": "overview", "body": "Reword this"},
    )

    e, f = _engine(db_path)
    with transactional(f) as s:
        proj_id = ProjectRepository(s).get_by_slug("demo").row_id
        doc = docs.get(s, proj_id, "guides/intro")
        cid = comments.list_for_document(s, doc.row_id)[0].row_id
    e.dispose()

    r = client.post(
        f"/p/{entry.name}/docs/guides/intro/comments/apply/commit",
        data={
            "apply_anchor": "overview",
            "new_body__overview": "Brand-new body for overview.",
            "comment_ids__overview": str(cid),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    e, f = _engine(db_path)
    with transactional(f) as s:
        proj_id = ProjectRepository(s).get_by_slug("demo").row_id
        doc = docs.get(s, proj_id, "guides/intro")
        secs = docs.get_sections(s, doc.row_id)
        sec_body = next(x.body for x in secs if x.anchor == "overview")
        c = comments.get(s, cid)
    e.dispose()
    assert sec_body == "Brand-new body for overview."
    assert c.status == "applied"
