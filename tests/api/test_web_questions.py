"""OQM-004: web pages for open questions — list, card, write forms."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    QuestionLinkKind,
    QuestionRelation,
)
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service, question_service

if TYPE_CHECKING:
    from pathlib import Path

SLUG = "oq-demo"


@pytest.fixture
def q_client(tmp_path: Path, migrate_db):  # type: ignore[no-untyped-def]
    repo = tmp_path / SLUG
    (repo / ".cod-doc").mkdir(parents=True)
    (repo / "pay.py").write_text(
        "import x\n\ndef charge(amount):\n    return amount\n", encoding="utf-8"
    )
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name=SLUG, path=str(repo))
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
            ProjectEntity(slug=SLUG, title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()
        q = question_service.create(
            session,
            project_id=proj.row_id,
            title="Какой провайдер эквайринга?",
            question="**ЮKassa** или CloudPayments?",
            context="См. ADR-001",
            options=[("ЮKassa", "дешевле"), ("CloudPayments", None)],
            author="human:test",
        )
        doc_service.create(
            session,
            project_id=proj.row_id,
            doc_key="docs/billing",
            title="Billing",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            owner="core",
            author="human:test",
        )
        for kind, ref in (
            (QuestionLinkKind.CODE, "pay.py#charge"),
            (QuestionLinkKind.CODE, "gone.py"),
            (QuestionLinkKind.TASK, "PAY-001"),
            (QuestionLinkKind.DOCUMENT, "docs/billing"),
        ):
            question_service.link(
                session,
                project_id=proj.row_id,
                question_id=q.question_id,
                to_kind=kind,
                to_ref=ref,
                relation=QuestionRelation.ABOUT,
                author="human:test",
            )
        question_service.verify_links(session, project_id=proj.row_id)
        question_service.create(
            session,
            project_id=proj.row_id,
            title="Закрытый",
            question="?",
            author="human:test",
        )
        question_service.drop(
            session,
            project_id=proj.row_id,
            question_id="Q-002",
            resolution="неактуально",
            author="human:test",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_list_shows_open_by_default_with_counters(q_client) -> None:  # type: ignore[no-untyped-def]
    r = q_client.get(f"/p/{SLUG}/questions")
    assert r.status_code == 200
    assert "Q-001" in r.text
    assert "Q-002" not in r.text
    assert 'class="grid question-table"' in r.text
    assert "✗ 2" in r.text  # two broken links on Q-001

    r = q_client.get(f"/p/{SLUG}/questions?status=all")
    assert "Q-002" in r.text
    r = q_client.get(f"/p/{SLUG}/questions?status=bogus")
    assert r.status_code == 200


def test_card_renders_markdown_options_links_and_excerpt(q_client) -> None:  # type: ignore[no-untyped-def]
    r = q_client.get(f"/p/{SLUG}/questions/Q-001")
    assert r.status_code == 200
    assert "<strong>ЮKassa</strong>" in r.text  # markdown in the question body
    assert "дешевле" in r.text
    assert "file not found" in r.text
    assert "task not found" in r.text
    assert f'href="/p/{SLUG}/tasks/PAY-001"' in r.text
    assert "def charge(amount):" in r.text  # code excerpt around the symbol
    assert q_client.get(f"/p/{SLUG}/questions/Q-404").status_code == 404


def test_create_edit_resolve_reopen_via_forms(q_client) -> None:  # type: ignore[no-untyped-def]
    r = q_client.post(
        f"/p/{SLUG}/questions/new",
        data={"title": "Новый", "question": "Что?", "options": "A\n\nB\n", "priority": "high"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{SLUG}/questions/Q-003"

    r = q_client.post(
        f"/p/{SLUG}/questions/Q-003/edit",
        data={"title": "Новый 2", "question": "Что?", "owner": "alice", "priority": "low"},
        follow_redirects=True,
    )
    assert "Новый 2" in r.text
    assert "alice" in r.text

    r = q_client.post(
        f"/p/{SLUG}/questions/Q-003/resolve",
        data={"chosen_option": "1", "by_adr": "ADR-007", "resolution": ""},
        follow_redirects=True,
    )
    assert "✔ выбран" in r.text
    assert f'href="/p/{SLUG}/adr/ADR-007"' in r.text

    r = q_client.post(f"/p/{SLUG}/questions/Q-003/reopen", follow_redirects=True)
    assert "Закрыть с ответом" in r.text


def test_service_error_comes_back_as_alert_not_500(q_client) -> None:  # type: ignore[no-untyped-def]
    r = q_client.post(
        f"/p/{SLUG}/questions/Q-001/resolve",
        data={"resolution": "", "by_adr": "", "chosen_option": ""},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "alert-error" in r.text
    assert "resolve needs" in r.text

    r = q_client.post(
        f"/p/{SLUG}/questions/Q-001/links",
        data={"to_kind": "task", "to_ref": "not a task", "relation": "about"},
        follow_redirects=True,
    )
    assert "alert-error" in r.text

    r = q_client.post(f"/p/{SLUG}/questions/Q-404/reopen", follow_redirects=False)
    assert r.status_code == 404


def test_options_and_links_forms(q_client) -> None:  # type: ignore[no-untyped-def]
    q_client.post(f"/p/{SLUG}/questions/Q-001/options", data={"title": "Stripe", "body": "нет RU"})
    q_client.post(f"/p/{SLUG}/questions/Q-001/options/0", data={"op": "update", "title": "ЮKassa+"})
    q_client.post(f"/p/{SLUG}/questions/Q-001/options/1", data={"op": "delete"})
    r = q_client.post(
        f"/p/{SLUG}/questions/Q-001/links",
        data={"to_kind": "code", "to_ref": "pay.py#L3-L4", "relation": "about", "op": "attach"},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("#links")
    q_client.post(
        f"/p/{SLUG}/questions/Q-001/links",
        data={"to_kind": "code", "to_ref": "gone.py", "relation": "about", "op": "detach"},
    )
    r = q_client.get(f"/p/{SLUG}/questions/Q-001")
    assert "ЮKassa+" in r.text
    assert "<strong>CloudPayments</strong>" not in r.text
    assert "Stripe" in r.text
    assert "gone.py" not in r.text
    assert "pay.py#L3-L4" in r.text


def test_new_form_and_tab(q_client) -> None:  # type: ignore[no-untyped-def]
    r = q_client.get(f"/p/{SLUG}/questions/new")
    assert r.status_code == 200
    assert 'action="/p/oq-demo/questions/new"' in r.text
    r = q_client.post(
        f"/p/{SLUG}/questions/new", data={"title": " ", "question": "x"}, follow_redirects=True
    )
    assert "alert-error" in r.text
    assert f'href="/p/{SLUG}/questions"' in q_client.get(f"/p/{SLUG}").text


def test_document_page_lists_linked_questions(q_client) -> None:  # type: ignore[no-untyped-def]
    r = q_client.get(f"/p/{SLUG}/docs/docs/billing")
    assert r.status_code == 200
    assert 'id="questions"' in r.text
    assert f'href="/p/{SLUG}/questions/Q-001"' in r.text
