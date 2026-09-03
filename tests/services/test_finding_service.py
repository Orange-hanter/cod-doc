"""SYM-005D: finding_service — fingerprint / dedup / promote."""

from __future__ import annotations

import multiprocessing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.pool import NullPool

from cod_doc.infra.db import make_session_factory, register_sqlite_pragmas, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    FindingModel,
    FindingSourceRunModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services.finding_service import (
    FindingSeed,
    dismiss_finding,
    fingerprint_ai_review,
    fingerprint_routine,
    fingerprint_zairgrush,
    get_finding,
    ingest_findings,
    list_findings,
    promote_finding,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


def _seed_plan(session: Any) -> tuple[int, int, int]:
    """Return (project_id, plan_id, section_id)."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Data Core", slug="A-Data-Core", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


# --------------------------------------------------------------------------- #
# Fingerprint tests                                                           #
# --------------------------------------------------------------------------- #


class TestFingerprintAiReview:
    def test_uses_fp_when_present_and_basis_is_fp(self) -> None:
        fp, basis = fingerprint_ai_review(
            path="src/x.py", fp="abc123", severity="major", title="Bad thing"
        )
        assert len(fp) == 64
        assert basis["fp_basis"] == "fp"

    def test_degrades_to_title_when_fp_missing(self) -> None:
        fp, basis = fingerprint_ai_review(
            path="src/x.py", fp="", severity="major", title="Bad thing"
        )
        assert len(fp) == 64
        assert basis["fp_basis"] == "title"

    def test_degrades_to_title_when_fp_none(self) -> None:
        fp, basis = fingerprint_ai_review(
            path="src/x.py", fp=None, severity="major", title="Bad thing"
        )
        assert len(fp) == 64
        assert basis["fp_basis"] == "title"

    @given(
        path=st.text(),
        fp=st.text(min_size=1),
        severity=st.sampled_from(["critical", "major", "minor", "info"]),
        title=st.text(min_size=1),
    )
    @settings(max_examples=200)
    def test_stable_when_inputs_unchanged(
        self, path: str, fp: str, severity: str, title: str
    ) -> None:
        a, _ = fingerprint_ai_review(path=path, fp=fp, severity=severity, title=title)
        b, _ = fingerprint_ai_review(path=path, fp=fp, severity=severity, title=title)
        assert a == b

    @given(
        base_fp=st.text(min_size=1, max_size=30),
        other_fp=st.text(min_size=1, max_size=30),
    )
    @settings(max_examples=200)
    def test_different_fp_yields_different_hash(self, base_fp: str, other_fp: str) -> None:
        assume = pytest.importorskip("hypothesis").assume
        assume(base_fp != other_fp)
        a, _ = fingerprint_ai_review(path="p", fp=base_fp, severity="major", title="T")
        b, _ = fingerprint_ai_review(path="p", fp=other_fp, severity="major", title="T")
        assert a != b


class TestFingerprintZairgrush:
    def test_note_does_not_affect_fingerprint(self) -> None:
        a, _ = fingerprint_zairgrush(exp="E5", variant="C", kind="drift")
        b, _ = fingerprint_zairgrush(exp="E5", variant="C", kind="drift")
        assert a == b

    @given(
        exp=st.text(min_size=1),
        variant=st.text(min_size=1),
        kind=st.text(min_size=1),
    )
    @settings(max_examples=200)
    def test_stable(self, exp: str, variant: str, kind: str) -> None:
        a, _ = fingerprint_zairgrush(exp=exp, variant=variant, kind=kind)
        b, _ = fingerprint_zairgrush(exp=exp, variant=variant, kind=kind)
        assert a == b


class TestFingerprintRoutine:
    @given(
        check_name=st.text(min_size=1),
        scope_kind=st.sampled_from(["task", "doc", "section"]),
        scope_id=st.text(min_size=1),
    )
    @settings(max_examples=200)
    def test_stable(self, check_name: str, scope_kind: str, scope_id: str) -> None:
        a, _ = fingerprint_routine(check_name=check_name, scope_kind=scope_kind, scope_id=scope_id)
        b, _ = fingerprint_routine(check_name=check_name, scope_kind=scope_kind, scope_id=scope_id)
        assert a == b


# --------------------------------------------------------------------------- #
# Dedup tests                                                                 #
# --------------------------------------------------------------------------- #


def _ai_seeds(count: int, source_run_id: str) -> list[FindingSeed]:
    seeds: list[FindingSeed] = []
    for i in range(count):
        fp, basis = fingerprint_ai_review(
            path=f"src/file_{i}.py",
            fp=f"fp-{i}",
            severity="major",
            title=f"Issue {i}",
        )
        seeds.append(
            FindingSeed(
                fingerprint=fp,
                source="ai_review",
                title=f"Issue {i}",
                severity="major",
                path=f"src/file_{i}.py",
                payload=basis,
                raw={"source_run_id": source_run_id, "index": i},
            )
        )
    return seeds


def test_repeated_ingest_updates_times_seen(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _, _ = _seed_plan(session)
        seeds = _ai_seeds(3, "run-1")
        result = ingest_findings(session, project_id=project_id, source_run_id="run-1", seeds=seeds)
        assert result.created == 3
        assert result.updated == 0

    with transactional(factory) as session:
        seeds = _ai_seeds(3, "run-2")
        result = ingest_findings(session, project_id=project_id, source_run_id="run-2", seeds=seeds)
        assert result.created == 0
        assert result.updated == 3

    with transactional(factory) as session:
        findings = list(
            session.execute(
                select(FindingModel).where(FindingModel.project_id == project_id)
            ).scalars()
        )
        assert len(findings) == 3
        for f in findings:
            assert f.times_seen == 2

        runs = list(
            session.execute(
                select(FindingSourceRunModel)
                .join(FindingModel, FindingModel.row_id == FindingSourceRunModel.finding_id)
                .where(FindingModel.project_id == project_id)
            ).scalars()
        )
        assert len(runs) == 6


# --------------------------------------------------------------------------- #
# Concurrency test                                                            #
# --------------------------------------------------------------------------- #


def _concurrent_worker(db_path: Path, project_id: int, source_run_id: str) -> dict[str, int]:
    """Target function for multiprocessing concurrency test."""
    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
        future=True,
    )
    register_sqlite_pragmas(engine, url)
    factory = make_session_factory(engine)

    seeds = _ai_seeds(3, source_run_id)
    with transactional(factory) as session:
        result = ingest_findings(
            session, project_id=project_id, source_run_id=source_run_id, seeds=seeds
        )
    engine.dispose()
    return result.as_dict()


def test_concurrent_ingest_no_database_is_locked(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "concurrent.db"
    # Migrate the file DB that will be shared by worker processes.
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    engine = create_engine(
        f"sqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        project_id, _, _ = _seed_plan(session)
        seeds = _ai_seeds(3, "seed-run")
        ingest_findings(session, project_id=project_id, source_run_id="seed-run", seeds=seeds)
    engine.dispose()

    workers = 8
    with multiprocessing.Pool(workers) as pool:
        results = pool.starmap(
            _concurrent_worker,
            [(db_path, project_id, f"run-{i}") for i in range(workers)],
        )

    total_created = sum(r["created"] for r in results)
    total_updated = sum(r["updated"] for r in results)
    assert total_created == 0
    assert total_updated == workers * 3

    engine = create_engine(
        f"sqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        findings = list(
            session.execute(
                select(FindingModel).where(FindingModel.project_id == project_id)
            ).scalars()
        )
        assert len(findings) == 3
        for f in findings:
            assert f.times_seen == workers + 1  # seed + 8 workers
        runs = list(
            session.execute(
                select(FindingSourceRunModel)
                .join(FindingModel, FindingModel.row_id == FindingSourceRunModel.finding_id)
                .where(FindingModel.project_id == project_id)
            ).scalars()
        )
        assert len(runs) == (workers + 1) * 3
    engine.dispose()


# --------------------------------------------------------------------------- #
# Promote tests                                                               #
# --------------------------------------------------------------------------- #


def _ingest_single_finding(session: Any, project_id: int) -> FindingModel:
    fp, basis = fingerprint_ai_review(
        path="src/x.py", fp="fp-1", severity="major", title="Promote me"
    )
    result = ingest_findings(
        session,
        project_id=project_id,
        source_run_id="promote-run",
        seeds=[
            FindingSeed(
                fingerprint=fp,
                source="ai_review",
                title="Promote me",
                severity="major",
                path="src/x.py",
                body="This is the body",
                payload=basis,
            )
        ],
    )
    assert result.created == 1
    return session.execute(
        select(FindingModel).where(FindingModel.project_id == project_id)
    ).scalar_one()


def test_promote_create_task_emits_activity_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, plan_id, section_id = _seed_plan(session)
        finding = _ingest_single_finding(session, project_id)

        outcome = promote_finding(
            session,
            project_id=project_id,
            finding_id=finding.row_id,
            on_finding="create_task",
            plan_id=plan_id,
            section_id=section_id,
            author="test",
        )
        assert outcome["promoted"] is True
        assert outcome["task_id"].startswith("FND-")

        session.flush()
        refreshed = session.get(FindingModel, finding.row_id)
        assert refreshed.status == "promoted"
        assert refreshed.promoted_task_id == outcome["task_id"]

        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.project_id == project_id,
                    ActivityEventModel.kind == "finding.promoted",
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].scope_id == finding.finding_uid
        assert events[0].payload["promoted_task_id"] == outcome["task_id"]


def test_promote_comment_only_is_no_op(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _, _ = _seed_plan(session)
        finding = _ingest_single_finding(session, project_id)

        outcome = promote_finding(
            session,
            project_id=project_id,
            finding_id=finding.row_id,
            on_finding="comment_only",
            plan_id=0,
            section_id=0,
        )
        assert outcome["promoted"] is False

        refreshed = session.get(FindingModel, finding.row_id)
        assert refreshed.status == "open"
        assert refreshed.promoted_task_id is None


def test_promote_update_existing_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, plan_id, section_id = _seed_plan(session)
        finding = _ingest_single_finding(session, project_id)

        create_outcome = promote_finding(
            session,
            project_id=project_id,
            finding_id=finding.row_id,
            on_finding="create_task",
            plan_id=plan_id,
            section_id=section_id,
        )

        finding.body = "Updated body"
        session.flush()

        update_outcome = promote_finding(
            session,
            project_id=project_id,
            finding_id=finding.row_id,
            on_finding="update_existing_task",
            plan_id=plan_id,
            section_id=section_id,
        )
        assert update_outcome["task_id"] == create_outcome["task_id"]

        session.flush()
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.project_id == project_id,
                    ActivityEventModel.kind == "finding.promoted",
                )
            ).scalars()
        )
        assert len(events) == 2


# --------------------------------------------------------------------------- #
# Query / dismiss tests (SYM-006D)                                            #
# --------------------------------------------------------------------------- #


def test_list_and_get_findings(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _, _ = _seed_plan(session)
        finding = _ingest_single_finding(session, project_id)

        rows = list_findings(session, project_id)
        assert len(rows) == 1
        assert rows[0]["finding_uid"] == finding.finding_uid
        assert rows[0]["status"] == "open"

        assert list_findings(session, project_id, status="dismissed") == []
        assert list_findings(session, project_id, source="zairgrush") == []

        got = get_finding(session, project_id, finding.finding_uid)
        assert got is not None
        assert got["title"] == "Promote me"
        assert got["finding_id"] == finding.row_id

        assert get_finding(session, project_id, "no-such-uid") is None
        # Cross-project isolation: same uid, other project id → miss.
        assert get_finding(session, project_id + 1, finding.finding_uid) is None


def test_dismiss_emits_activity_event_once(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _, _ = _seed_plan(session)
        finding = _ingest_single_finding(session, project_id)

        out = dismiss_finding(
            session,
            project_id=project_id,
            finding_uid=finding.finding_uid,
            author="test",
            reason="noise",
        )
        assert out["status"] == "dismissed"

        # Second dismiss is a no-op — no duplicate activity event.
        again = dismiss_finding(
            session, project_id=project_id, finding_uid=finding.finding_uid, author="test"
        )
        assert again["status"] == "dismissed"

        session.flush()
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.project_id == project_id,
                    ActivityEventModel.kind == "finding.dismissed",
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].scope_id == finding.finding_uid
        assert events[0].payload["reason"] == "noise"


def test_dismiss_unknown_finding_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _, _ = _seed_plan(session)
        with pytest.raises(ValueError, match="not found"):
            dismiss_finding(session, project_id=project_id, finding_uid="missing-uid")
