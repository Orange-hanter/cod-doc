"""WEB-080: GET /standards renders the shipped skill catalog."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config


@pytest.fixture
def standards_client():
    """Minimal Config — /standards is project-independent."""
    Config(
        api_key="sk-test",
        model="test/model",
        base_url="https://x",
    ).save()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_standards_list_renders_catalog(standards_client) -> None:
    """The list page renders every shipped SKILL.md as a collapsible item."""
    r = standards_client.get("/standards")
    assert r.status_code == 200
    # New skills we just shipped:
    assert "task-standard" in r.text
    assert "module-audit" in r.text
    # Existing skills are still there:
    assert "orchestrator" in r.text
    assert "audit-cadence" in r.text
    # Page chrome:
    assert "Default standards" in r.text
    # Nav link present.
    assert 'href="/standards"' in r.text


def test_standards_show_renders_one_skill(standards_client) -> None:
    """Deep-link to one skill returns its body markdown."""
    r = standards_client.get("/standards/module-audit")
    assert r.status_code == 200
    # Frontmatter trigger block present on detail page.
    assert "module audit" in r.text.lower()
    # Markdown body rendered: H1 / section headers.
    assert "Module audit" in r.text
    assert "drift" in r.text.lower()
    # 5-dimension mention from the body.
    assert "Code drift" in r.text or "code drift" in r.text.lower()


def test_standards_show_unknown_returns_404(standards_client) -> None:
    r = standards_client.get("/standards/does-not-exist")
    assert r.status_code == 404
