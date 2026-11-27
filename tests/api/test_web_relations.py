"""REL-012: панель «Связи» (`_frag/relations.html`) на страницах сущностей."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ADRModel, ScenarioModel
from cod_doc.services import doc_service
from tests.services.test_relation_service import W

if TYPE_CHECKING:
    from pathlib import Path

SLUG = "p"  # слаг проекта в `W`

#: (путь страницы, ref) — у «linked» есть ребро к story:US-007, у «lonely» нет ни одного.
LINKED = [
    (f"/p/{SLUG}/tasks/ADO-001", "task:ADO-001"),
    (f"/p/{SLUG}/stories/US-064", "story:US-064"),
    (f"/p/{SLUG}/docs/docs/a", "document:docs/a"),
    (f"/p/{SLUG}/adr/ADR-001", "adr:ADR-001"),
    (f"/p/{SLUG}/questions/Q-001", "question:Q-001"),
    (f"/p/{SLUG}/scenarios/SCN-001", "scenario:SCN-001"),
]
LONELY = [
    f"/p/{SLUG}/tasks/ADO-002",
    f"/p/{SLUG}/stories/US-100",
    f"/p/{SLUG}/docs/docs/b",
    f"/p/{SLUG}/adr/ADR-002",
    f"/p/{SLUG}/questions/Q-002",
    f"/p/{SLUG}/scenarios/SCN-002",
]


@pytest.fixture
def client(tmp_path: Path, migrate_db):  # type: ignore[no-untyped-def]
    repo = tmp_path / SLUG
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)
    entry = ProjectEntry(name=SLUG, path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    with transactional(make_session_factory(engine)) as session:
        w = W(session, repo)
        w.question("Q-002")
        w.scenario("SCN-002")
        # `W` кладёт устаревший kind; страница сценария читает его как enum.
        session.execute(update(ScenarioModel).values(kind="happy_path"))
        for adr in ("ADR-001", "ADR-002"):
            session.add(ADRModel(project_id=w.pid, adr_id=adr, title=adr, uid=str(uuid.uuid4())))
        for key in ("docs/a", "docs/b"):
            doc_service.create(
                session,
                project_id=w.pid,
                doc_key=key,
                title=key,
                type=DocumentType.MODULE_SPEC,
                status=DocumentStatus.ACTIVE,
                owner="core",
                author="human:test",
            )
        session.flush()
        for _, ref in LINKED:
            w.relate(ref, "relates_to", "story:US-007", note="связано")
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.mark.parametrize(("path", "ref"), LINKED)
def test_panel_lists_relations_grouped_with_badge(client, path: str, ref: str) -> None:  # type: ignore[no-untyped-def]
    r = client.get(path)
    assert r.status_code == 200
    assert 'id="relations"' in r.text
    # другой конец — одна и та же история; у самой US-007 он был бы US-064
    assert "story:US-007" in r.text
    assert f'href="/p/{SLUG}/stories/US-007"' in r.text
    assert "<code>relates_to</code>" in r.text
    assert "l-tag-neutral" in r.text and ">relation<" in r.text  # бейдж источника
    assert "Добавить связь" in r.text


@pytest.mark.parametrize("path", LONELY)
def test_panel_without_relations_shows_empty_state_and_form(client, path: str) -> None:  # type: ignore[no-untyped-def]
    r = client.get(path)
    assert r.status_code == 200
    assert "Связей пока нет." in r.text
    assert f'href="/p/{SLUG}/stories/US-007"' not in r.text
    assert "Добавить связь" in r.text


def test_add_relation_form_writes_edge_and_redirects_back(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        f"/p/{SLUG}/relations",
        data={
            "from_ref": "story:US-100",
            "type": "depends_on",
            "to_ref": "story:US-064",
            "back": f"/p/{SLUG}/stories/US-100#relations",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{SLUG}/stories/US-100#relations"
    page = client.get(f"/p/{SLUG}/stories/US-100").text
    assert "story:US-064" in page
    assert "<code>depends_on</code>" in page


def test_add_relation_reports_hint_and_rejects_foreign_back(client) -> None:  # type: ignore[no-untyped-def]
    bad = client.post(
        f"/p/{SLUG}/relations",
        data={
            "from_ref": "story:US-100",
            "type": "depend_on",
            "to_ref": "story:US-064",
            "back": f"/p/{SLUG}/stories/US-100",
        },
    )
    assert bad.status_code == 422
    assert "Похоже: depends_on" in bad.text
    evil = client.post(
        f"/p/{SLUG}/relations",
        data={
            "from_ref": "story:US-100",
            "type": "relates_to",
            "to_ref": "story:US-064",
            "back": "//evil.example/",
        },
        follow_redirects=False,
    )
    assert evil.status_code == 400
