"""Web init / import surfaces.

Covers:
- POST /p/{slug}/init — bootstraps `.cod-doc/state.db` (alembic + ProjectModel),
  idempotent, redirects with flash cookie, makes the dashboard "live".
- POST /p/{slug}/docs/import — uploads a markdown file and creates a Document +
  Sections.
- import_service.parse_markdown — pure unit tests for the markdown parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.api import deps
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.services import import_service
from cod_doc.services import project_service as project_svc

if TYPE_CHECKING:
    from pathlib import Path


# ── parse_markdown unit tests ───────────────────────────────────────────


def test_parse_no_frontmatter_no_h1_only_body() -> None:
    out = import_service.parse_markdown("Just a paragraph.\n\nAnother.")
    assert out.frontmatter == {}
    assert out.title_h1 is None
    assert out.preamble == "Just a paragraph.\n\nAnother."
    assert out.sections == []


def test_parse_frontmatter_extracted() -> None:
    md = """---
title: Foo Module
type: module-spec
status: active
owner: human:dakh
---

# Foo Module

Top-level **summary**.
"""
    out = import_service.parse_markdown(md)
    assert out.frontmatter["title"] == "Foo Module"
    assert out.frontmatter["status"] == "active"
    assert out.title_h1 == "Foo Module"
    assert "Top-level **summary**." in out.preamble


def test_parse_splits_by_h2() -> None:
    md = """preamble line.

## Data Model

table content.

## API

routes content.

### Sub-section under API

still inside API.
"""
    out = import_service.parse_markdown(md)
    assert out.preamble == "preamble line."
    assert len(out.sections) == 2
    assert out.sections[0].anchor == "data-model"
    assert out.sections[0].heading == "Data Model"
    assert "table content" in out.sections[0].body
    assert out.sections[1].heading == "API"
    assert "Sub-section under API" in out.sections[1].body  # H3 stays inside its H2


def test_parse_h2_inside_fenced_code_is_not_a_section() -> None:
    md = """## Real

```python
## fake heading inside code
```
"""
    out = import_service.parse_markdown(md)
    assert len(out.sections) == 1
    assert out.sections[0].heading == "Real"
    assert "fake heading" in out.sections[0].body


def test_parse_disambiguates_duplicate_anchors() -> None:
    md = "## Notes\n\nfoo\n\n## Notes\n\nbar"
    out = import_service.parse_markdown(md)
    anchors = [s.anchor for s in out.sections]
    assert anchors == ["notes", "notes-2"]


def test_parse_invalid_yaml_falls_back_to_empty_frontmatter() -> None:
    md = "---\nthis: is: bad: yaml\n---\n\n# Title"
    out = import_service.parse_markdown(md)
    assert out.frontmatter == {}
    assert out.title_h1 == "Title"


# ── POST /p/{slug}/init ─────────────────────────────────────────────────


@pytest.fixture
def web_no_db_client(tmp_path: Path):
    """Project registered in config but `.cod-doc/state.db` not yet created."""
    repo = tmp_path / "fresh-repo"
    repo.mkdir()
    entry = ProjectEntry(name="fresh", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)
    deps.set_config(cfg)
    Project(entry).init()  # creates tasks.yaml/state.yaml/MASTER.md but NOT state.db

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_init_creates_db_and_redirects(web_no_db_client) -> None:
    client, entry = web_no_db_client
    db_path = entry.cod_doc_dir / "state.db"
    assert not db_path.exists()
    r = client.post(
        f"/p/{entry.name}/init",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{entry.name}"
    assert db_path.exists(), "state.db should be created by alembic"
    cookies = " ".join(r.headers.get_list("set-cookie"))
    assert "flash_severity=info" in cookies


def test_init_is_idempotent(web_no_db_client) -> None:
    """Calling init twice must not error out."""
    client, entry = web_no_db_client
    r1 = client.post(f"/p/{entry.name}/init", follow_redirects=False)
    assert r1.status_code == 303
    r2 = client.post(f"/p/{entry.name}/init", follow_redirects=False)
    assert r2.status_code == 303
    cookies = " ".join(r2.headers.get_list("set-cookie"))
    # second call → "already initialised" wording (cookie URL-encoded)
    assert "flash_severity=info" in cookies


def test_init_unblocks_overview_dashboard(web_no_db_client) -> None:
    """After init, the empty-DB banner is gone and the live agg blocks render."""
    client, entry = web_no_db_client
    pre = client.get(f"/p/{entry.name}")
    assert "База проекта не инициализирована" in pre.text

    client.post(f"/p/{entry.name}/init", follow_redirects=False)

    post = client.get(f"/p/{entry.name}")
    assert "База проекта не инициализирована" not in post.text
    # The agg block headers appear when DB is live.
    assert "Ready to start" in post.text
    assert "Plan progress" in post.text


def test_init_unknown_project_404(web_no_db_client) -> None:
    client, _ = web_no_db_client
    r = client.post("/p/unknown/init", follow_redirects=False)
    assert r.status_code == 404


# ── POST /p/{slug}/docs/import ──────────────────────────────────────────


@pytest.fixture
def web_inited_client(tmp_path: Path, web_no_db_client):
    """Same as web_no_db_client but the project is already DB-initialised."""
    client, entry = web_no_db_client
    project_svc.init_project(entry)
    deps.dispose_all_engines()
    return client, entry


def test_import_creates_document_with_sections(web_inited_client) -> None:
    client, entry = web_inited_client
    md = b"""---
title: Imported Module
type: module-spec
status: active
owner: human:web
sensitivity: internal
---

# Imported Module

Top-level **summary** with `code`.

## Data Model

The `entities` table.

- one
- two

## API

POST /v1/foo
"""
    r = client.post(
        f"/p/{entry.name}/docs/import",
        data={"doc_key": "modules/imported/spec", "type": "module-spec"},
        files={"file": ("imported.md", md, "text/markdown")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{entry.name}/docs/modules/imported/spec"

    # GET the new doc — preamble + 2 sections rendered
    r2 = client.get(r.headers["location"])
    assert r2.status_code == 200
    body = r2.text
    assert "Imported Module" in body
    assert "<strong>summary</strong>" in body  # markdown render
    assert '<section id="section-data-model"' in body
    assert '<section id="section-api"' in body
    assert "<code>entities</code>" in body
    assert "<li>one</li>" in body


def test_import_falls_back_to_filename_for_title(web_inited_client) -> None:
    client, entry = web_inited_client
    md = b"plain body, no frontmatter, no h1"
    r = client.post(
        f"/p/{entry.name}/docs/import",
        data={"doc_key": "modules/plain/note", "type": "module-spec"},
        files={"file": ("plain-note.md", md, "text/markdown")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # The list page shows the title — derive from filename
    list_r = client.get(f"/p/{entry.name}/docs")
    assert "plain-note" in list_r.text


def test_import_rejects_missing_doc_key(web_inited_client) -> None:
    client, entry = web_inited_client
    r = client.post(
        f"/p/{entry.name}/docs/import",
        data={"doc_key": "", "type": "module-spec"},
        files={"file": ("x.md", b"x", "text/markdown")},
        headers={"HX-Request": "true"},
    )
    # WebError → ValidationWebError(400)
    assert r.status_code == 400
    assert "alert-warning" in r.text


def test_import_rejects_unknown_type(web_inited_client) -> None:
    client, entry = web_inited_client
    r = client.post(
        f"/p/{entry.name}/docs/import",
        data={"doc_key": "modules/x/y", "type": "garbage-type"},
        files={"file": ("x.md", b"x", "text/markdown")},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400


def test_import_rejects_non_utf8(web_inited_client) -> None:
    client, entry = web_inited_client
    # Latin-1 encoded bytes that aren't valid UTF-8 (e.g., 0xff alone)
    invalid = b"\xff\xfe not utf-8"
    r = client.post(
        f"/p/{entry.name}/docs/import",
        data={"doc_key": "modules/x/y", "type": "module-spec"},
        files={"file": ("x.md", invalid, "text/markdown")},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400
    assert "UTF-8" in r.text


def test_import_form_visible_on_docs_list(web_inited_client) -> None:
    """The «Import markdown» details block is part of the docs page UI."""
    client, entry = web_inited_client
    r = client.get(f"/p/{entry.name}/docs")
    assert "Import markdown" in r.text
    assert 'enctype="multipart/form-data"' in r.text
    # All document_types appear in the dropdown
    assert ">module-spec<" in r.text
