"""ACU-011: веб-страница «Предложения куратора» — diff, одобрение, отказ."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import approval_service, curator_ops, doc_service, question_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

SLUG = "prop-demo"
_ARGS: dict[str, Any] = {"doc_key": "guide", "anchor": "setup", "old": "old.md", "new": "new.md"}


@pytest.fixture
def stand(tmp_path: Path, migrate_db) -> Iterator[tuple[TestClient, sessionmaker]]:  # type: ignore[no-untyped-def]
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
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug=SLUG, title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()
        doc = doc_service.create(
            session,
            project_id=proj.row_id,
            doc_key="guide",
            title="Guide",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            owner="core",
            author="human:test",
        )
        assert doc.row_id is not None
        doc_service.add_section(
            session,
            document_id=doc.row_id,
            anchor="setup",
            heading="Setup",
            level=2,
            position=0,
            body="См. [гайд](old.md).\n",
            author="human:test",
        )
        question_service.create(
            session,
            project_id=proj.row_id,
            title="Док или код?",
            question="Символ исчез.",
            author="agent:curator",
        )
    from cod_doc.api.server import app

    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client, factory
    finally:
        engine.dispose()


def _pid(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(SLUG)
    assert row is not None and row.row_id is not None
    return row.row_id


def _propose(factory: sessionmaker) -> str:
    with transactional(factory) as session:
        pid = _pid(session)
        result = approval_service.request_doc_patch(
            session,
            pid,
            op="link_retarget",
            args=_ARGS,
            diff="-См. [гайд](old.md).\n+См. [гайд](new.md).\n",
            rationale="old.md переехал в new.md",
            base_revision_id=curator_ops.CURATOR_OPS["link_retarget"].head(session, pid, _ARGS),
        )
        assert result.approval is not None
        return result.approval.approval_id


def _body(factory: sessionmaker) -> str:
    with transactional(factory, commit=False) as session:
        doc = doc_service.get(session, _pid(session), "guide")
        assert doc is not None and doc.row_id is not None
        return next(s.body for s in doc_service.get_sections(session, doc.row_id))


def test_the_page_shows_the_diff_rationale_and_curator_questions(stand) -> None:  # type: ignore[no-untyped-def]
    client, factory = stand
    approval_id = _propose(factory)

    r = client.get(f"/p/{SLUG}/proposals")

    assert r.status_code == 200
    assert 'class="pd-add"' in r.text and "[гайд](new.md)" in r.text
    assert 'class="pd-del"' in r.text
    assert "old.md переехал в new.md" in r.text
    assert f"/proposals/{approval_id}/approve" in r.text
    assert "Док или код?" in r.text


def test_approve_applies_the_patch_and_reports_it(stand) -> None:  # type: ignore[no-untyped-def]
    client, factory = stand
    approval_id = _propose(factory)

    r = client.post(f"/p/{SLUG}/proposals/{approval_id}/approve", follow_redirects=True)

    assert r.status_code == 200
    assert "Применено" in r.text
    assert "[гайд](new.md)" in _body(factory)
    assert f"/proposals/{approval_id}/approve" not in r.text


def test_deny_keeps_the_text_and_stores_the_reason(stand) -> None:  # type: ignore[no-untyped-def]
    client, factory = stand
    approval_id = _propose(factory)

    r = client.post(
        f"/p/{SLUG}/proposals/{approval_id}/deny",
        data={"reason": "ссылка верная"},
        follow_redirects=True,
    )

    assert "Отклонено" in r.text
    assert "[гайд](old.md)" in _body(factory)
    with transactional(factory, commit=False) as session:
        denied = approval_service.get(session, _pid(session), approval_id)
    assert denied is not None and denied.decision_comment == "ссылка верная"


def test_a_stale_patch_says_so_and_writes_nothing(stand) -> None:  # type: ignore[no-untyped-def]
    client, factory = stand
    approval_id = _propose(factory)
    with transactional(factory) as session:
        doc = doc_service.get(session, _pid(session), "guide")
        assert doc is not None and doc.row_id is not None
        doc_service.patch_section(
            session,
            document_id=doc.row_id,
            anchor="setup",
            new_body="Человек переписал. [гайд](old.md)\n",
            author="human:dakh",
        )

    r = client.post(f"/p/{SLUG}/proposals/{approval_id}/approve", follow_redirects=True)

    assert "устарела" in r.text
    assert "Человек переписал" in _body(factory)


def test_an_unknown_proposal_is_404(stand) -> None:  # type: ignore[no-untyped-def]
    client, _factory = stand

    r = client.post(f"/p/{SLUG}/proposals/nope/approve")

    assert r.status_code == 404
