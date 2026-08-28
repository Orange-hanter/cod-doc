"""OBI-040: FTS5 unified search across tasks/docs/stories/ADRs."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    FindingModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    SectionModel,
    UserStoryModel,
)
from cod_doc.services import search_service, task_service


def _seed(session) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="srp", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="srp-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make_task(session, pid, plid, sid, tid, title, **fields):
    return task_service.create(
        session,
        project_id=pid,
        plan_id=plid,
        section_id=sid,
        task_id=tid,
        title=title,
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="t",
        **fields,
    )


def _make_doc(session, pid, key, title, body):
    now = datetime.now(UTC)
    d = DocumentModel(
        project_id=pid,
        doc_key=key,
        path=f"{key}.md",
        type="guide",
        status="active",
        title=title,
        sensitivity="internal",
        preamble=body,
    )
    d.created = now
    d.last_updated = now
    session.add(d)
    session.flush()
    sec = SectionModel(
        document_id=d.row_id,
        anchor="main",
        heading="Main",
        level=2,
        position=0,
        body=body,
        content_hash="0",
    )
    session.add(sec)
    session.flush()
    return d


def _make_story(session, pid, sid, persona, narrative):
    now = datetime.now(UTC)
    s = UserStoryModel(
        project_id=pid,
        story_id=sid,
        persona=persona,
        narrative=narrative,
        status="draft",
        priority="medium",
    )
    s.created = now
    s.last_updated = now
    session.add(s)
    session.flush()
    return s


def _make_adr(session, pid, aid, title, decision):
    now = datetime.now(UTC)
    a = ADRModel(
        project_id=pid,
        adr_id=aid,
        title=title,
        status="accepted",
        context="ctx",
        decision=decision,
    )
    a.created = now
    a.last_updated = now
    session.add(a)
    session.flush()
    return a


def _make_finding(session, pid, uid, title, body):
    now = datetime.now(UTC)
    f = FindingModel(
        project_id=pid,
        finding_uid=uid,
        source="ai_review",
        fingerprint=f"fp-{uid}",
        severity="major",
        title=title,
        body=body,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(f)
    session.flush()
    return f


# ----------------------------------------------------------------- #
# Index population                                                   #
# ----------------------------------------------------------------- #


def test_reindex_counts_all_kinds(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "SRP-001", "Implement search")
        _make_doc(session, pid, "guide/x", "Search guide", "FTS5 over SQLite for unified search.")
        _make_story(session, pid, "US-7", "dev", "I want to find anything by phrase")
        _make_adr(session, pid, "ADR-001", "FTS5 chosen", "FTS5 over Chroma for embedded queries.")
    with transactional(factory) as session:
        counts = search_service.reindex_all(session, project_id=1)
    assert counts["task"] == 1
    assert counts["doc"] == 1
    assert counts["story"] == 1
    assert counts["adr"] == 1
    assert counts["total"] == 4


def test_reindex_is_idempotent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "SRP-010", "alpha beta")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        n = session.execute(
            text("SELECT COUNT(*) FROM db_search_idx WHERE project_id=1")
        ).scalar_one()
    assert int(n) == 1


# ----------------------------------------------------------------- #
# Search                                                              #
# ----------------------------------------------------------------- #


def test_search_finds_task_by_title(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "SRP-020", "Implement checkout endpoint")
        _make_task(session, pid, plid, sid, "SRP-021", "Fix login redirect")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="checkout")
    assert result["total"] == 1
    assert result["by_kind"]["task"][0]["ref"] == "SRP-020"


def test_search_finds_doc_by_body_word(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _make_doc(session, pid, "ops/db", "DB ops", "Postgres vacuum schedule and WAL settings.")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="vacuum")
    refs = [h["ref"] for h in result["by_kind"]["doc"]]
    assert "ops/db" in refs


def test_search_finds_story_by_narrative(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _make_story(session, pid, "US-30", "user", "Receive email after signup")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="signup")
    assert any(h["ref"] == "US-30" for h in result["by_kind"]["story"])


def test_search_finds_adr_by_decision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _make_adr(session, pid, "ADR-040", "Use FTS5", "FTS5 chosen because no daemon dependency.")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="daemon")
    refs = [h["ref"] for h in result["by_kind"]["adr"]]
    assert "ADR-040" in refs


def test_reindex_counts_findings(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _make_finding(session, pid, "F-001", "Race in checkout", "Found a data race in checkout.")
    with transactional(factory) as session:
        counts = search_service.reindex_all(session, project_id=1)
    assert counts["finding"] == 1
    assert counts["total"] == 1


def test_search_finds_finding_by_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _make_finding(session, pid, "F-002", "Memory leak", "Buffer is never released.")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="buffer")
    refs = [h["ref"] for h in result["by_kind"]["finding"]]
    assert "F-002" in refs


def test_search_scope_filters_to_one_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "SRP-050", "duplicate detection")
        _make_doc(session, pid, "guide/dup", "Dup detection", "How dedup works")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(
            session,
            project_id=1,
            query="dup",
            scope="task",
        )
    # task hit only — doc not in scope.
    assert any(h["ref"] == "SRP-050" for h in result["by_kind"]["task"])
    assert result["by_kind"]["doc"] == []


def test_search_invalid_scope_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with pytest.raises(ValueError, match="invalid scope"), transactional(factory) as session:
        search_service.search(
            session,
            project_id=1,
            query="x",
            scope="weird",
        )


def test_search_empty_query_returns_zero_hits(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="   ")
    assert result["total"] == 0


def test_search_snippet_marks_match(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "SRP-060", "Investigate edge case in checkout")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="checkout")
    hit = result["by_kind"]["task"][0]
    assert "<mark>" in hit["snippet"]
    assert "</mark>" in hit["snippet"]


# ----------------------------------------------------------------- #
# Performance                                                         #
# ----------------------------------------------------------------- #


def test_search_under_200ms_with_moderate_corpus(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Acceptance: search ≤ 200ms. Seed 200 tasks + 50 docs."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        for i in range(200):
            _make_task(
                session,
                pid,
                plid,
                sid,
                f"PRF-{i:03d}",
                f"Task {i} about indexing and retrieval",
            )
        for i in range(50):
            _make_doc(
                session,
                pid,
                f"docs/{i:03d}",
                f"Doc {i}",
                f"This document discusses topic {i} indexing patterns.",
            )
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)

    start = time.monotonic()
    with transactional(factory) as session:
        result = search_service.search(
            session,
            project_id=1,
            query="indexing",
        )
    elapsed = time.monotonic() - start
    assert result["total"] > 0
    assert elapsed < 0.2, f"search took {elapsed:.3f}s, must be <0.2s"
