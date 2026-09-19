"""OBI-040: FTS5 unified search across tasks/docs/stories/ADRs."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

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
    TaskModel,
    UserStoryModel,
)
from cod_doc.services import adr_service, finding_service, search_service, task_service
from cod_doc.services.story_service import acceptance as story_acceptance
from cod_doc.services.story_service import crud as story_crud


def _seed_project(session, slug: str) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    """Проект + план + секция; возвращает (project_id, plan_id, section_id)."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _seed(session) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    return _seed_project(session, "srp")


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


def _make_task_raw(session, pid, plid, sid, tid, title):
    """Insert a task straight into the table, bypassing ``task_service``.

    CUR-012 made every ``task_service`` mutation refresh the FTS row, so a
    task created through the service is *already* indexed. The tests that
    need an unindexed task — a row that predates incremental indexing, or
    one written around the service — build it here instead.
    """
    now = datetime.now(UTC)
    t = TaskModel(
        project_id=pid,
        task_id=tid,
        plan_id=plid,
        section_id=sid,
        title=title,
        status="todo",
        type="feature",
        priority="medium",
        created=now,
        last_updated=now,
    )
    session.add(t)
    session.flush()
    return t


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
# ensure_index (RFC 25 §3.2, CUR-007: lazy reindex for ctx_search)   #
# ----------------------------------------------------------------- #


def test_ensure_index_reindexes_when_empty(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task_raw(session, pid, plid, sid, "SRP-030", "Implement lazy reindex")

    with transactional(factory) as session:
        n = session.execute(
            text("SELECT COUNT(*) FROM db_search_idx WHERE project_id=1")
        ).scalar_one()
    assert int(n) == 0  # nothing indexed yet — reindex_all never ran

    with transactional(factory) as session:
        meta = search_service.ensure_index(session, project_id=1)
    assert meta["reindexed"] is True
    assert meta["total"] == 1
    assert meta["by_kind"]["task"] == 1

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="reindex")
    assert result["total"] == 1
    assert result["by_kind"]["task"][0]["ref"] == "SRP-030"


def test_ensure_index_is_noop_when_populated(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task_raw(session, pid, plid, sid, "SRP-031", "Already indexed task")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)

    # A task added *after* the reindex is deliberately left unindexed — it
    # is the marker that proves ensure_index did not touch the index below.
    # It has to bypass task_service, which would index it (CUR-012).
    with transactional(factory) as session:
        _make_task_raw(session, pid, plid, sid, "SRP-032", "Added after reindex")

    with transactional(factory) as session:
        meta = search_service.ensure_index(session, project_id=1)
    assert meta["reindexed"] is False
    assert meta["total"] == 1
    assert meta["by_kind"] == {"task": 1}

    with transactional(factory) as session:
        n = session.execute(
            text("SELECT COUNT(*) FROM db_search_idx WHERE project_id=1")
        ).scalar_one()
    assert int(n) == 1  # untouched — the late-added task is still not indexed


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


# ----------------------------------------------------------------- #
# CUR-011: per-kind limit + title-weighted bm25                      #
# ----------------------------------------------------------------- #

_MANY_DOCS = 30
_MANY_TASKS = 3
_PER_KIND_LIMIT = 5


def test_search_limit_applies_per_kind_not_globally(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """30 doc hits must not crowd out the 3 task hits under a shared LIMIT."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        for i in range(_MANY_DOCS):
            _make_doc(session, pid, f"guide/{i:02d}", f"Doc {i}", "widget appears in every doc")
        for i in range(_MANY_TASKS):
            _make_task(session, pid, plid, sid, f"WDG-{i:03d}", f"widget task {i}")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="widget", limit=_PER_KIND_LIMIT)
    assert len(result["by_kind"]["task"]) == _MANY_TASKS
    assert len(result["by_kind"]["doc"]) == _PER_KIND_LIMIT
    assert result["total"] == _MANY_TASKS + _PER_KIND_LIMIT


_FILLER_DOC_COUNT = 50  # raises idf enough that rounded bm25 scores don't tie at -0.0


def test_search_title_hit_ranks_above_body_hit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A term in a doc's title must score better (lower) than a body-only hit."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _make_doc(session, pid, "guide/title-hit", "gadget overview", "unrelated content here")
        _make_doc(session, pid, "guide/body-hit", "unrelated title", "discusses gadget at length")
        # Filler corpus: without it the two matching docs dominate the index
        # and bm25's magnitude is small enough to round to -0.0 for both,
        # hiding the title-weight effect this test exists to catch.
        for i in range(_FILLER_DOC_COUNT):
            _make_doc(
                session, pid, f"guide/filler-{i}", f"filler {i}", "nothing to see here at all"
            )
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="gadget")
    hits = result["by_kind"]["doc"]
    refs = [h["ref"] for h in hits]
    assert refs == ["guide/title-hit", "guide/body-hit"]
    scores = {h["ref"]: h["score"] for h in hits}
    assert scores["guide/title-hit"] < scores["guide/body-hit"]


def test_search_scope_finding_is_valid(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _make_finding(session, pid, "F-010", "Timeout bug", "Request times out under load.")
    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="timeout", scope="finding")
    refs = [h["ref"] for h in result["by_kind"]["finding"]]
    assert "F-010" in refs
    assert result["by_kind"]["task"] == []


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


# ----------------------------------------------------------------- #
# CUR-010: SearchIndexMissing when db_search_idx doesn't exist yet   #
# ----------------------------------------------------------------- #


def _drop_search_index(factory) -> None:  # type: ignore[no-untyped-def]
    """Simulate a DB that predates migration 0023 (no ``db_search_idx``)."""
    with transactional(factory) as session:
        session.execute(text("DROP TABLE db_search_idx"))


def test_search_raises_search_index_missing_without_table(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    _drop_search_index(factory)

    with (
        pytest.raises(search_service.SearchIndexMissing, match="db_search_idx"),
        transactional(factory) as session,
    ):
        search_service.search(session, project_id=1, query="anything")


def test_ensure_index_raises_search_index_missing_without_table(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    _drop_search_index(factory)

    with (
        pytest.raises(search_service.SearchIndexMissing, match="db_search_idx"),
        transactional(factory) as session,
    ):
        search_service.ensure_index(session, project_id=1)


def test_reindex_all_raises_search_index_missing_without_table(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    _drop_search_index(factory)

    with (
        pytest.raises(search_service.SearchIndexMissing, match="db_search_idx"),
        transactional(factory) as session,
    ):
        search_service.reindex_all(session, project_id=1)


def test_other_operational_errors_are_not_swallowed(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A different table-missing error must propagate untouched (not our guard)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
        session.execute(text("DROP TABLE task"))

    with (
        pytest.raises(OperationalError, match="task") as excinfo,
        transactional(factory) as session,
    ):
        search_service.reindex_all(session, project_id=1)
    assert not isinstance(excinfo.value, search_service.SearchIndexMissing)


# ----------------------------------------------------------------- #
# CUR-013: кросс-проектный поиск в одной hub-БД (RFC 22 §3.6)        #
# ----------------------------------------------------------------- #

_CROSS_DOCS_PER_PROJECT = 4
_CROSS_LIMIT = 5
_HUB_URL = "sqlite:////tmp/hub.db"


def _seed_two_projects(factory) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """Два проекта в одной БД, у каждого — задача со словом ``widget``."""
    with transactional(factory) as session:
        pid_a, plan_a, sec_a = _seed_project(session, "alpha")
        pid_b, plan_b, sec_b = _seed_project(session, "beta")
        _make_task(session, pid_a, plan_a, sec_a, "ALP-001", "widget wiring in alpha")
        _make_task(session, pid_b, plan_b, sec_b, "BET-001", "widget wiring in beta")
    with transactional(factory) as session:
        search_service.reindex_all(session, pid_a)
        search_service.reindex_all(session, pid_b)
    return pid_a, pid_b


def test_search_without_project_ids_stays_single_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Без ``project_ids`` соседний проект той же БД не виден — как и раньше."""
    factory = make_session_factory(engine_with_schema)
    pid_a, _pid_b = _seed_two_projects(factory)

    with transactional(factory) as session:
        result = search_service.search(session, project_id=pid_a, query="widget")

    assert [h["ref"] for h in result["by_kind"]["task"]] == ["ALP-001"]
    assert result["by_kind"]["task"][0]["project"] == "alpha"


def test_search_with_project_ids_returns_hits_of_both(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """``project_ids`` расширяет выдачу и помечает каждый хит слагом владельца."""
    factory = make_session_factory(engine_with_schema)
    pid_a, pid_b = _seed_two_projects(factory)

    with transactional(factory) as session:
        result = search_service.search(
            session, project_id=pid_a, query="widget", project_ids=[pid_b]
        )

    assert {h["ref"]: h["project"] for h in result["by_kind"]["task"]} == {
        "ALP-001": "alpha",
        "BET-001": "beta",
    }
    assert result["total"] == 2


def test_search_per_kind_limit_applies_to_merged_result(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Лимит на kind режет ОБЪЕДИНЁННУЮ выдачу, а не каждый проект отдельно."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid_a, _, _ = _seed_project(session, "alpha")
        pid_b, _, _ = _seed_project(session, "beta")
        for pid, prefix in ((pid_a, "a"), (pid_b, "b")):
            for i in range(_CROSS_DOCS_PER_PROJECT):
                _make_doc(session, pid, f"guide/{prefix}{i}", f"Doc {prefix}{i}", "widget corpus")
    with transactional(factory) as session:
        search_service.reindex_all(session, pid_a)
        search_service.reindex_all(session, pid_b)

    with transactional(factory) as session:
        result = search_service.search(
            session,
            project_id=pid_a,
            query="widget",
            limit=_CROSS_LIMIT,
            project_ids=[pid_b],
        )

    # 4 + 4 совпадения при потолке 5 — значит окно одно на оба проекта.
    assert len(result["by_kind"]["doc"]) == _CROSS_LIMIT
    assert result["total"] == _CROSS_LIMIT
    assert {h["project"] for h in result["by_kind"]["doc"]} <= {"alpha", "beta"}


def test_resolve_cross_project_ids_requires_shared_db_url(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Проект с другим ``db_url`` — ошибка, а не молчаливо пустая выдача."""
    from cod_doc.config import Config, ProjectEntry

    factory = make_session_factory(engine_with_schema)
    _seed_two_projects(factory)
    cfg = Config(
        projects=[
            ProjectEntry(name="alpha", path=str(tmp_path / "alpha"), db_url=_HUB_URL).model_dump(),
            ProjectEntry(name="beta", path=str(tmp_path / "beta")).model_dump(),
        ]
    )

    with transactional(factory) as session, pytest.raises(ValueError, match="shared db_url"):
        search_service.resolve_cross_project_ids(
            session, project="alpha", projects=["beta"], config=cfg
        )


def test_resolve_cross_project_ids_maps_slugs_in_shared_db(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Общий ``db_url`` → слаг превращается в ``project.row_id``; базовый исключён."""
    from cod_doc.config import Config, ProjectEntry

    factory = make_session_factory(engine_with_schema)
    _pid_a, pid_b = _seed_two_projects(factory)
    cfg = Config(
        projects=[
            ProjectEntry(name=name, path=str(tmp_path / name), db_url=_HUB_URL).model_dump()
            for name in ("alpha", "beta")
        ]
    )

    with transactional(factory) as session:
        resolved = search_service.resolve_cross_project_ids(
            session, project="alpha", projects=["beta", "alpha", "beta"], config=cfg
        )

    assert resolved == {"beta": pid_b}


# ----------------------------------------------------------------- #
# CUR-012: incremental index maintenance from the write path         #
# ----------------------------------------------------------------- #


def _index_rows(factory):  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        return [
            tuple(r)
            for r in session.execute(
                text(
                    "SELECT kind, ref, title, body FROM db_search_idx "
                    "WHERE project_id = 1 ORDER BY kind, ref"
                )
            ).all()
        ]


def _ingest_finding(session, pid, title, body):  # type: ignore[no-untyped-def]
    return finding_service.ingest_findings(
        session,
        project_id=pid,
        source_run_id="run-1",
        seeds=[
            finding_service.FindingSeed(
                fingerprint=f"fp-{title}",
                source="ai_review",
                title=title,
                severity="major",
                body=body,
            )
        ],
    )


def test_task_create_is_indexed_without_reindex(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "INC-001", "Incremental upsert of the index")

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="incremental", scope="task")
    assert [h["ref"] for h in result["by_kind"]["task"]] == ["INC-001"]


def test_task_description_update_is_indexed(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "INC-002", "Placeholder title")

    with transactional(factory) as session:
        task_service.update_description(
            session,
            task_id="INC-002",
            new_description="Now mentions rendezvous in the body.",
            author="t",
        )

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="rendezvous")
    assert [h["ref"] for h in result["by_kind"]["task"]] == ["INC-002"]


def test_task_acceptance_and_blocker_are_indexed(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "INC-003", "Placeholder title")

    with transactional(factory) as session:
        task_service.update_acceptance(
            session, task_id="INC-003", new_acceptance="zeppelin lands", author="t"
        )
        task_service.set_blocker(
            session, task_id="INC-003", reason="waiting on kryptonite", author="t"
        )

    with transactional(factory) as session:
        assert search_service.search(session, project_id=1, query="zeppelin")["total"] == 1
        assert search_service.search(session, project_id=1, query="kryptonite")["total"] == 1

    # Clearing the blocker drops that text from the indexed body again.
    with transactional(factory) as session:
        task_service.clear_blocker(session, task_id="INC-003", author="t")
    with transactional(factory) as session:
        assert search_service.search(session, project_id=1, query="kryptonite")["total"] == 0


def test_task_complete_keeps_the_row_indexed(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "INC-004", "Finish the telemetry export")

    with transactional(factory) as session:
        task_service.complete(session, task_id="INC-004", author="t")

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="telemetry")
    assert [h["ref"] for h in result["by_kind"]["task"]] == ["INC-004"]


def test_story_create_and_criterion_are_indexed(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        story_crud.create(
            session,
            project_id=pid,
            story_id="US-100",
            persona="curator",
            narrative="As a curator I want to find a decision by phrase.",
            priority=Priority.MEDIUM,
            author="t",
        )

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="curator", scope="story")
    assert [h["ref"] for h in result["by_kind"]["story"]] == ["US-100"]

    with transactional(factory) as session:
        story_acceptance.add_criterion(
            session, story_id="US-100", criterion="search returns the marmalade", author="t"
        )

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="marmalade")
    assert [h["ref"] for h in result["by_kind"]["story"]] == ["US-100"]


def test_adr_create_and_title_update_are_indexed(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        adr_service.create(
            session,
            project_id=pid,
            title="Choose the quicksilver transport",
            decision="Tungsten was rejected here.",
            author="t",
        )

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="quicksilver", scope="adr")
    assert [h["ref"] for h in result["by_kind"]["adr"]] == ["ADR-001"]

    # Retitling replaces the row rather than adding a second one.
    with transactional(factory) as session:
        adr_service.update(
            session, project_id=pid, adr_id="ADR-001", title="Choose the obsidian transport"
        )

    with transactional(factory) as session:
        assert search_service.search(session, project_id=1, query="quicksilver")["total"] == 0
        result = search_service.search(session, project_id=1, query="obsidian")
    assert [h["ref"] for h in result["by_kind"]["adr"]] == ["ADR-001"]


def test_adr_deprecate_keeps_one_row(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        adr_service.create(session, project_id=pid, title="Retire the obsidian cache", author="t")
    with transactional(factory) as session:
        adr_service.deprecate(session, project_id=pid, adr_id="ADR-001", author="t")

    with transactional(factory) as session:
        n = session.execute(
            text("SELECT COUNT(*) FROM db_search_idx WHERE project_id=1 AND kind='adr'")
        ).scalar_one()
    assert int(n) == 1


def test_finding_ingest_is_indexed_and_dismiss_removes_it(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _ingest_finding(session, pid, "Pomegranate leak", "The pomegranate handle is never closed.")

    with transactional(factory) as session:
        result = search_service.search(session, project_id=1, query="pomegranate", scope="finding")
    hits = result["by_kind"]["finding"]
    assert len(hits) == 1
    uid = hits[0]["ref"]

    with transactional(factory) as session:
        finding_service.dismiss_finding(session, project_id=pid, finding_uid=uid, author="human:t")

    with transactional(factory) as session:
        assert search_service.search(session, project_id=1, query="pomegranate")["total"] == 0


def test_reindex_skips_dismissed_findings(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A full rebuild agrees with the incremental hook about dismissed rows."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        _ingest_finding(session, pid, "Tangerine leak", "Tangerine handle left open.")

    with transactional(factory) as session:
        hits = search_service.search(session, project_id=1, query="tangerine")["by_kind"]["finding"]
        uid = hits[0]["ref"]
    with transactional(factory) as session:
        finding_service.dismiss_finding(session, project_id=pid, finding_uid=uid, author="human:t")

    with transactional(factory) as session:
        counts = search_service.reindex_all(session, project_id=1)
    assert counts["finding"] == 0
    with transactional(factory) as session:
        assert search_service.search(session, project_id=1, query="tangerine")["total"] == 0


def test_incremental_rows_match_a_full_reindex(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The anti-drift guarantee: both paths share the same payload builders."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(
            session,
            pid,
            plid,
            sid,
            "INC-010",
            "Task with everything",
            description="desc body",
            acceptance="acc body",
        )
        story_crud.create(
            session,
            project_id=pid,
            story_id="US-200",
            persona="dev",
            narrative="As a dev I want parity.",
            priority=Priority.MEDIUM,
            author="t",
            acceptance=["first criterion"],
        )
        adr_service.create(
            session,
            project_id=pid,
            title="Parity decision",
            context="ctx",
            decision="dec",
            author="t",
        )
        _ingest_finding(session, pid, "Parity finding", "finding body")

    incremental = _index_rows(factory)
    assert {row[0] for row in incremental} == {"task", "story", "adr", "finding"}

    with transactional(factory) as session:
        search_service.reindex_all(session, project_id=1)
    assert _index_rows(factory) == incremental


def test_write_path_survives_a_missing_index_table(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A DB predating migration 0023 must still accept writes (CUR-012).

    The FTS index is a derived artefact: losing it degrades search until
    the next ``--reindex``; it must not make ``task_create`` fail.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
    _drop_search_index(factory)

    with transactional(factory) as session:
        _make_task(session, pid, plid, sid, "INC-020", "Survives a missing index")

    with transactional(factory) as session:
        t = task_service.get(session, "INC-020")
    assert t is not None


def test_upsert_entity_raises_search_index_missing_without_table(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    _drop_search_index(factory)

    with (
        pytest.raises(search_service.SearchIndexMissing, match="db_search_idx"),
        transactional(factory) as session,
    ):
        search_service.upsert_entity(
            session, kind="task", ref="X-1", project_id=1, title="t", body="b"
        )


def test_delete_entity_raises_search_index_missing_without_table(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    _drop_search_index(factory)

    with (
        pytest.raises(search_service.SearchIndexMissing, match="db_search_idx"),
        transactional(factory) as session,
    ):
        search_service.delete_entity(session, kind="task", ref="X-1", project_id=1)
