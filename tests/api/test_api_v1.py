"""SYM-006C: REST API v1 — findings ingest, context, search."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

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
from cod_doc.services import search_service

if TYPE_CHECKING:
    from pathlib import Path

_AI_REVIEW_EXPORT = {
    "version": 1,
    "headSha": "abc123",
    "reviewMode": "pr",
    "pr": {"number": 42},
    "findings": [
        {
            "title": "SQL injection risk in query builder",
            "severity": "major",
            "file": "cod_doc/infra/db.py",
            "line": 10,
            "fp": "fp-1",
            "body": "User input reaches raw SQL.",
            "confidence": 0.9,
        },
        {
            "title": "Unhandled exception in parser",
            "severity": "minor",
            "file": "cod_doc/services/ingest_service/ai_review.py",
            "line": 25,
            "fp": "fp-2",
        },
    ],
    "blockers": [],
}


@pytest.fixture
def v1_client(tmp_path: Path, migrate_db):
    """Project with `.cod-doc/state.db` migrated and seeded (doc + project row)."""
    repo = tmp_path / "demo-repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)  # persists config; lifespan's Config.load() picks it up

    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        session.flush()

        docs.create(
            session,
            project_id=proj.row_id,
            doc_key="modules/M1-auth/overview",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="Auth Module Overview",
            author="human:test",
            owner="human:test",
            sensitivity=Sensitivity.INTERNAL,
            preamble="Top preamble.",
        )
        search_service.reindex_all(session, proj.row_id)
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ── POST /api/v1/projects/{slug}/findings ─────────────────────────────────────


def test_post_findings_happy_path(v1_client) -> None:
    r = v1_client.post(
        "/api/v1/projects/demo/findings",
        json={"adapter": "ai_review", "export": _AI_REVIEW_EXPORT},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["adapter"] == "ai_review"
    assert data["created"] == 2
    assert data["updated"] == 0
    assert data["total"] == 2
    assert data["dry_run"] is False
    assert data["source_run_id"].startswith("api-ingest-ai_review-")


def test_post_findings_repeat_ingest_dedups(v1_client) -> None:
    payload = {"adapter": "ai_review", "export": _AI_REVIEW_EXPORT}
    r1 = v1_client.post("/api/v1/projects/demo/findings", json=payload)
    assert r1.json()["created"] == 2
    r2 = v1_client.post("/api/v1/projects/demo/findings", json=payload)
    assert r2.status_code == 200
    assert r2.json()["created"] == 0
    assert r2.json()["updated"] == 2


def test_post_findings_dry_run_writes_nothing(v1_client) -> None:
    payload = {"adapter": "ai_review", "export": _AI_REVIEW_EXPORT, "dry_run": True}
    r = v1_client.post("/api/v1/projects/demo/findings", json=payload)
    assert r.status_code == 200
    assert r.json()["created"] == 2
    assert r.json()["dry_run"] is True
    # Dry-run rollback: a real ingest afterwards still creates, not updates.
    r2 = v1_client.post(
        "/api/v1/projects/demo/findings",
        json={"adapter": "ai_review", "export": _AI_REVIEW_EXPORT},
    )
    assert r2.json()["created"] == 2


def test_post_findings_emits_activity_event(v1_client, tmp_path: Path) -> None:
    v1_client.post(
        "/api/v1/projects/demo/findings",
        json={"adapter": "ai_review", "export": _AI_REVIEW_EXPORT},
    )
    import cod_doc.api.deps as deps

    engine = deps.get_engine_for_slug("demo")
    assert engine is not None
    factory = make_session_factory(engine)
    session = factory()
    try:
        from sqlalchemy import text

        rows = session.execute(
            text("SELECT kind, actor_kind FROM activity_event WHERE kind = 'finding.ingested'")
        ).all()
    finally:
        session.close()
    assert len(rows) == 1
    assert rows[0].actor_kind == "api"


def test_post_findings_invalid_payload_version(v1_client) -> None:
    # ADO-069: раньше здесь стояла version=2 — и тест ломался ровно в тот
    # коммит, в котором SYM-009 научил адаптер понимать v2. Негативный кейс
    # обязан брать версию вне _KNOWN_VERSIONS, а не ту, которую завтра внедрят.
    bad = {**_AI_REVIEW_EXPORT, "version": 99}
    r = v1_client.post(
        "/api/v1/projects/demo/findings",
        json={"adapter": "ai_review", "export": bad},
    )
    assert r.status_code == 400
    assert "version" in r.json()["detail"]


def test_post_findings_accepts_export_version_2(v1_client) -> None:
    """v2 аддитивна к v1: fp/verifierStatus/actionabilityScore (ai-reviewer#5).

    Приём v2 — контракт upstream-PR; до ADO-069 он проверялся только через CLI
    ingest, а API v1 держал ассерт «v2 отвергается».
    """
    export_v2 = {
        **_AI_REVIEW_EXPORT,
        "version": 2,
        "findings": [
            {**_AI_REVIEW_EXPORT["findings"][0], "verifierStatus": "confirmed"},
            {**_AI_REVIEW_EXPORT["findings"][1], "actionabilityScore": 0.7},
        ],
    }
    r = v1_client.post(
        "/api/v1/projects/demo/findings",
        json={"adapter": "ai_review", "export": export_v2},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["created"] == 2


def test_post_findings_unknown_adapter(v1_client) -> None:
    r = v1_client.post(
        "/api/v1/projects/demo/findings",
        json={"adapter": "no_such_adapter", "export": {}},
    )
    assert r.status_code == 400
    assert "no_such_adapter" in r.json()["detail"]


def test_post_findings_unknown_project(v1_client) -> None:
    r = v1_client.post(
        "/api/v1/projects/ghost/findings",
        json={"adapter": "ai_review", "export": _AI_REVIEW_EXPORT},
    )
    assert r.status_code == 404


# ── GET /api/v1/projects/{slug}/context ───────────────────────────────────────


def test_get_context_document_l1(v1_client) -> None:
    r = v1_client.get(
        "/api/v1/projects/demo/context",
        params={"target_kind": "document", "target_id": "modules/M1-auth/overview"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["target_summary"]["title"] == "Auth Module Overview"
    assert data["meta"]["depth"] == "L1"
    assert data["meta"]["tokens_used"] >= 1


def test_get_context_l0_has_no_body(v1_client) -> None:
    r = v1_client.get(
        "/api/v1/projects/demo/context",
        params={
            "target_kind": "document",
            "target_id": "modules/M1-auth/overview",
            "depth": "L0",
        },
    )
    assert r.status_code == 200
    assert r.json()["core"] == {}


def test_get_context_target_not_found(v1_client) -> None:
    r = v1_client.get(
        "/api/v1/projects/demo/context",
        params={"target_kind": "document", "target_id": "no/such/doc"},
    )
    assert r.status_code == 404


def test_get_context_invalid_depth_rejected(v1_client) -> None:
    r = v1_client.get(
        "/api/v1/projects/demo/context",
        params={
            "target_kind": "document",
            "target_id": "modules/M1-auth/overview",
            "depth": "L9",
        },
    )
    assert r.status_code == 422


def test_get_context_unknown_project(v1_client) -> None:
    r = v1_client.get(
        "/api/v1/projects/ghost/context",
        params={"target_kind": "document", "target_id": "x"},
    )
    assert r.status_code == 404


# ── GET /api/v1/projects/{slug}/search ────────────────────────────────────────


def test_search_finds_seeded_doc(v1_client) -> None:
    r = v1_client.get("/api/v1/projects/demo/search", params={"q": "Auth Module"})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    refs = [hit["ref"] for hit in data["by_kind"]["doc"]]
    assert "modules/M1-auth/overview" in refs


def test_search_scope_filter(v1_client) -> None:
    # Ingest findings, reindex, then search with scope=finding.
    v1_client.post(
        "/api/v1/projects/demo/findings",
        json={"adapter": "ai_review", "export": _AI_REVIEW_EXPORT},
    )
    import cod_doc.api.deps as deps

    engine = deps.get_engine_for_slug("demo")
    assert engine is not None
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug("demo")
        assert proj is not None and proj.row_id is not None
        search_service.reindex_all(session, proj.row_id)

    r = v1_client.get(
        "/api/v1/projects/demo/search",
        params={"q": "SQL injection", "scope": "finding"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert len(data["by_kind"]["finding"]) >= 1
    assert data["by_kind"]["doc"] == []


def test_search_invalid_scope_rejected(v1_client) -> None:
    r = v1_client.get(
        "/api/v1/projects/demo/search",
        params={"q": "auth", "scope": "bogus"},
    )
    assert r.status_code == 422


def test_search_empty_query_returns_zero(v1_client) -> None:
    r = v1_client.get("/api/v1/projects/demo/search", params={"q": ""})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_search_unknown_project(v1_client) -> None:
    r = v1_client.get("/api/v1/projects/ghost/search", params={"q": "auth"})
    assert r.status_code == 404
