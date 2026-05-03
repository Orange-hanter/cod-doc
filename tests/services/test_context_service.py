"""COD-041: ContextService L0/L1 — assemble minimal-sufficient context."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Priority,
    Sensitivity,
    TaskType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    LinkModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import context_service, doc_service, task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _seed_plan(session: Session, project_id: int, scope: str = "p-plan") -> tuple[int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope=scope, created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
    )
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id


def _seed_doc(
    session: Session, project_id: int, doc_key: str = "modules/M1/overview"
) -> int:
    doc = doc_service.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="M1 Overview",
        author="human:test",
        owner="human:test",
        sensitivity=Sensitivity.INTERNAL,
        preamble="Intro.",
    )
    return doc.row_id  # type: ignore[return-value]


# ===========================================================================
# Validation
# ===========================================================================


def test_invalid_depth_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="Invalid depth"):
            context_service.context_get(
                session, proj_id, "document", "x", depth="L9"
            )


def test_unknown_target_kind_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="Unknown target_kind"):
            context_service.context_get(session, proj_id, "alien", "x")


def test_missing_document_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="Document not found"):
            context_service.context_get(session, proj_id, "document", "no/such/key")


def test_missing_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="Task not found"):
            context_service.context_get(session, proj_id, "task", "NX-001")


def test_missing_plan_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="Plan not found"):
            context_service.context_get(session, proj_id, "plan", "ghost-plan")


# ===========================================================================
# L0 — metadata only
# ===========================================================================


def test_l0_document_metadata_only(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_doc(session, proj_id, "modules/M1/overview")

        result = context_service.context_get(
            session, proj_id, "document", "modules/M1/overview", depth="L0"
        )

        assert result["target_summary"]["title"] == "M1 Overview"
        assert result["target_summary"]["type"] == "module-spec"
        # L0: no section bodies
        assert "sections" not in result["core"]
        # L0: empty related
        assert result["related"]["tasks"] == []
        assert result["related"]["stories"] == []
        assert result["meta"]["depth"] == "L0"
        assert result["meta"]["effective_depth"] == "L0"


def test_l0_task_metadata_only(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        plan_id, sec_id = _seed_plan(session, proj_id)
        task_service.create(
            session,
            project_id=proj_id,
            plan_id=plan_id,
            section_id=sec_id,
            task_id="PR-001",
            title="Implement: foo",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:test",
            description="some long description here",
            acceptance="acceptance text",
        )

        result = context_service.context_get(
            session, proj_id, "task", "PR-001", depth="L0"
        )

        assert result["target_summary"]["task_id"] == "PR-001"
        assert result["target_summary"]["status"] == "pending"
        # L0: no description body in core
        assert "description" not in result["core"]
        assert "acceptance" not in result["core"]


# ===========================================================================
# L1 — body + direct relations
# ===========================================================================


def test_l1_document_includes_sections(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        doc_id = _seed_doc(session, proj_id, "modules/M1/overview")

        doc_service.add_section(
            session,
            document_id=doc_id,
            anchor="data-model",
            heading="Data Model",
            level=2,
            position=0,
            body="Some data model description.",
            author="human:test",
        )

        result = context_service.context_get(
            session, proj_id, "document", "modules/M1/overview", depth="L1"
        )

        assert result["meta"]["effective_depth"] == "L1"
        sections = result["core"]["sections"]
        assert len(sections) == 1
        assert sections[0]["anchor"] == "data-model"
        assert sections[0]["heading"] == "Data Model"
        assert "Some data model description" in sections[0]["excerpt"]


def test_l1_task_includes_description_and_acceptance(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        plan_id, sec_id = _seed_plan(session, proj_id)
        task_service.create(
            session,
            project_id=proj_id,
            plan_id=plan_id,
            section_id=sec_id,
            task_id="PR-001",
            title="Implement: foo",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:test",
            description="some long description here",
            acceptance="acceptance text",
        )

        result = context_service.context_get(
            session, proj_id, "task", "PR-001", depth="L1"
        )

        assert result["core"]["description"] == "some long description here"
        assert result["core"]["acceptance"] == "acceptance text"


def test_l1_task_includes_siblings(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        plan_id, sec_id = _seed_plan(session, proj_id)
        for tid in ("PR-001", "PR-002", "PR-003"):
            task_service.create(
                session,
                project_id=proj_id,
                plan_id=plan_id,
                section_id=sec_id,
                task_id=tid,
                title=f"Implement: {tid}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )

        result = context_service.context_get(
            session, proj_id, "task", "PR-002", depth="L1"
        )

        sibling_ids = {t["task_id"] for t in result["related"]["tasks"]}
        assert sibling_ids == {"PR-001", "PR-003"}
        for t in result["related"]["tasks"]:
            assert t["why"] == "sibling"


def test_l1_plan_progress_and_open_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        plan_id, sec_id = _seed_plan(session, proj_id, scope="my-plan")
        for tid in ("PR-001", "PR-002"):
            task_service.create(
                session,
                project_id=proj_id,
                plan_id=plan_id,
                section_id=sec_id,
                task_id=tid,
                title=f"Implement: {tid}",
                type=TaskType.FEATURE,
                priority=Priority.HIGH,
                author="human:test",
            )

        result = context_service.context_get(
            session, proj_id, "plan", "my-plan", depth="L1"
        )

        assert result["target_summary"]["scope"] == "my-plan"
        assert result["target_summary"]["total"] == 2
        assert result["target_summary"]["done"] == 0
        assert {t["task_id"] for t in result["related"]["tasks"]} == {"PR-001", "PR-002"}


# ===========================================================================
# Master excerpt + meta
# ===========================================================================


def test_master_content_included_as_excerpt(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_doc(session, proj_id, "modules/M1/overview")

        master = "# Project\n\nMaster content here.\n"
        result = context_service.context_get(
            session,
            proj_id,
            "document",
            "modules/M1/overview",
            depth="L0",
            master_content=master,
        )

        assert result["core"]["master_excerpt"] == master


def test_master_content_truncated_at_limit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_doc(session, proj_id, "modules/M1/overview")

        master = "x" * 5000  # exceeds _MASTER_EXCERPT_CHARS=1200
        result = context_service.context_get(
            session,
            proj_id,
            "document",
            "modules/M1/overview",
            depth="L0",
            master_content=master,
        )

        excerpt = result["core"]["master_excerpt"]
        assert excerpt.endswith("…")
        assert len(excerpt) < len(master)


def test_meta_contains_depth_and_timestamp(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_doc(session, proj_id, "modules/M1/overview")

        result = context_service.context_get(
            session, proj_id, "document", "modules/M1/overview", depth="L1"
        )

        meta = result["meta"]
        assert meta["depth"] == "L1"
        assert meta["effective_depth"] == "L1"
        assert "tokens_used" in meta
        assert "truncated" in meta
        assert "T" in meta["generated_at"]  # ISO format


def test_l2_l3_meta_reflects_requested_depth(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """L2/L3 are now real depths (COD-042), not silently downgraded to L1."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_doc(session, proj_id, "modules/M1/overview")

        for d in ("L2", "L3"):
            result = context_service.context_get(
                session, proj_id, "document", "modules/M1/overview", depth=d
            )
            assert result["meta"]["depth"] == d
            assert result["meta"]["effective_depth"] == d


# ===========================================================================
# COD-042: L2 — dependency chains + cross-document links
# ===========================================================================


def test_l2_task_includes_forward_and_reverse_chains(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        plan_id, sec_id = _seed_plan(session, proj_id)

        for tid in ("PR-001", "PR-002", "PR-003"):
            task_service.create(
                session,
                project_id=proj_id,
                plan_id=plan_id,
                section_id=sec_id,
                task_id=tid,
                title=f"Implement: {tid}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )

        # Build chain: PR-002 depends on PR-001 (PR-001 blocks PR-002),
        # PR-003 depends on PR-002 (PR-002 blocks PR-003)
        rows = {t.task_id: t.row_id for t in task_service.list_for_project(session, proj_id)}
        session.add(
            DependencyModel(
                from_task_id=rows["PR-002"], to_task_id=rows["PR-001"], kind="blocks"
            )
        )
        session.add(
            DependencyModel(
                from_task_id=rows["PR-003"], to_task_id=rows["PR-002"], kind="blocks"
            )
        )
        session.flush()

        result = context_service.context_get(
            session, proj_id, "task", "PR-002", depth="L2"
        )

        deps = result["related"]["dependencies"]
        # forward: prerequisites → PR-001 (depth=1)
        # reverse: dependents → PR-003 (depth=1)
        directions = {(d["task_id"], d["direction"]) for d in deps}
        assert ("PR-001", "blocks") in directions
        assert ("PR-003", "blocked_by") in directions


def test_l1_task_does_not_include_chains(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """L1 stays cheap — no recursive chain queries."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        plan_id, sec_id = _seed_plan(session, proj_id)

        for tid in ("PR-001", "PR-002"):
            task_service.create(
                session,
                project_id=proj_id,
                plan_id=plan_id,
                section_id=sec_id,
                task_id=tid,
                title=f"Implement: {tid}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )
        rows = {t.task_id: t.row_id for t in task_service.list_for_project(session, proj_id)}
        session.add(
            DependencyModel(
                from_task_id=rows["PR-002"], to_task_id=rows["PR-001"], kind="blocks"
            )
        )
        session.flush()

        result = context_service.context_get(
            session, proj_id, "task", "PR-002", depth="L1"
        )
        # L1 → no dependencies populated (default empty list)
        assert result["related"]["dependencies"] == []


def test_l2_document_includes_cross_doc_links(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        src_id = _seed_doc(session, proj_id, "modules/M1/overview")
        _seed_doc(session, proj_id, "modules/M2/api")

        # Section in source doc with an outgoing link to M2
        sec = doc_service.add_section(
            session,
            document_id=src_id,
            anchor="see-also",
            heading="See Also",
            level=2,
            position=0,
            body="See [M2 API](modules/M2/api).",
            author="human:test",
        )
        session.add(
            LinkModel(
                project_id=proj_id,
                from_section_id=sec.row_id,
                raw="modules/M2/api",
                kind="markdown",
                to_doc_key="modules/M2/api",
                resolved=True,
            )
        )
        session.flush()

        result = context_service.context_get(
            session, proj_id, "document", "modules/M1/overview", depth="L2"
        )
        related_docs = result["related"]["documents"]
        assert any(d["doc_key"] == "modules/M2/api" for d in related_docs)


# ===========================================================================
# COD-042: L3 — semantic search (graceful no-op when backend not configured)
# ===========================================================================


def test_l3_semantic_returns_empty_when_no_api_key(
    engine_with_schema, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    """L3 must not fail when ChromaDB / embedding backend is unavailable."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_doc(session, proj_id, "modules/M1/overview")

        # Force Config.api_key to be empty — simulates placeholder/unset key
        from cod_doc.config import Config

        original_load = Config.load

        def _empty_cfg() -> Config:
            cfg = original_load()
            cfg.api_key = ""
            return cfg

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _empty_cfg()))

        result = context_service.context_get(
            session, proj_id, "document", "modules/M1/overview", depth="L3"
        )
        # graceful: empty list, no exception
        assert result["related"]["semantic"] == []
        assert result["meta"]["depth"] == "L3"
