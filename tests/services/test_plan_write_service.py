"""Unit tests for plan_write_service (duplicate scope / letter)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import plan_write_service


def _seed_project(session, slug: str = "pws") -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_create_plan_seeds_sections_and_uppercases_letter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        out = plan_write_service.create_plan(
            session,
            project_id=pid,
            scope="hardening-2026-09",
            principle="from-rfc",
            sections=[{"letter": "a", "title": "One"}],
            author="human:test",
        )
    assert out["scope"] == "hardening-2026-09"
    assert out["plan_id"] is not None
    assert out["sections"][0]["letter"] == "A"
    assert out["sections"][0]["title"] == "One"
    assert out["sections"][0]["slug"] == "One"
    assert out["sections"][0]["position"] == 0


def test_create_plan_duplicate_scope_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        plan_write_service.create_plan(
            session,
            project_id=pid,
            scope="dup-plan",
            principle="from-rfc",
            sections=None,
            author="human:test",
        )
    with transactional(factory) as session, pytest.raises(ValueError, match="already exists"):
        plan_write_service.create_plan(
            session,
            project_id=1,
            scope="dup-plan",
            principle="from-rfc",
            sections=None,
            author="human:test",
        )


def test_create_plan_rejects_section_without_letter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        with pytest.raises(ValueError, match="letter"):
            plan_write_service.create_plan(
                session,
                project_id=pid,
                scope="bad-sec",
                principle="from-rfc",
                sections=[{"title": "No letter"}],
                author="human:test",
            )


def test_add_section_duplicate_letter_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        created = plan_write_service.create_plan(
            session,
            project_id=pid,
            scope="sec-plan",
            principle="from-rfc",
            sections=[{"letter": "A", "title": "Alpha"}],
            author="human:test",
        )
        plan_id = created["plan_id"]
        assert plan_id is not None
        with pytest.raises(ValueError, match="already exists"):
            plan_write_service.add_section(
                session,
                plan_id=plan_id,
                letter="a",
                title="Again",
                slug=None,
                position=None,
                author="human:test",
            )


def test_add_section_appends_at_tail(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        created = plan_write_service.create_plan(
            session,
            project_id=pid,
            scope="tail-plan",
            principle="from-rfc",
            sections=[{"letter": "A", "title": "Alpha"}],
            author="human:test",
        )
        plan_id = created["plan_id"]
        assert plan_id is not None
        sec = plan_write_service.add_section(
            session,
            plan_id=plan_id,
            letter="B",
            title="Beta",
            slug=None,
            position=None,
            author="human:test",
        )
    assert sec["letter"] == "B"
    assert sec["slug"] == "Beta"
    assert sec["position"] == 1
