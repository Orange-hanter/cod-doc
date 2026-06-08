"""ADR-007: migrator parses legacy arch/architecture.md §5 → 5 ADRs in DB."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import adr_migrator, adr_service

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_MD = REPO_ROOT / "arch" / "architecture.md"


def _seed(session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="adr_mig", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


# ----------------------------------------------------------------- #
# Parser                                                              #
# ----------------------------------------------------------------- #


def test_parser_extracts_five_records_from_legacy_md() -> None:
    records = adr_migrator.parse_adrs_from_markdown(LEGACY_MD.read_text(encoding="utf-8"))
    assert len(records) == 5, (
        f"expected 5 ADRs in legacy arch/architecture.md §5, got {len(records)}"
    )
    ids = [r["adr_id"] for r in records]
    assert ids == ["ADR-001", "ADR-002", "ADR-003", "ADR-004", "ADR-005"]


def test_parser_normalizes_status_to_accepted() -> None:
    records = adr_migrator.parse_adrs_from_markdown(LEGACY_MD.read_text(encoding="utf-8"))
    statuses = {r["adr_id"]: r["status"] for r in records}
    # Legacy doc has ✅ Принято for all 5 — should map to "accepted".
    for adr_id, status in statuses.items():
        assert status == "accepted", f"{adr_id}: expected 'accepted', got {status!r}"


def test_parser_extracts_decided_at_date() -> None:
    records = adr_migrator.parse_adrs_from_markdown(LEGACY_MD.read_text(encoding="utf-8"))
    for r in records:
        assert r["decided_at"] == date(2026, 4, 5), (
            f"{r['adr_id']}: expected 2026-04-05, got {r['decided_at']!r}"
        )


def test_parser_captures_context_and_decision() -> None:
    records = adr_migrator.parse_adrs_from_markdown(LEGACY_MD.read_text(encoding="utf-8"))
    by_id = {r["adr_id"]: r for r in records}
    # ADR-001: 4-layer architecture; context mentions LLM providers.
    assert "LLM" in (by_id["ADR-001"]["context"] or "")
    assert "DIP" in (by_id["ADR-001"]["decision"] or "") or "4-слойная" in (
        by_id["ADR-001"]["decision"] or ""
    )
    # ADR-005: PostgreSQL decision.
    assert "PostgreSQL" in (by_id["ADR-005"]["decision"] or "")


# ----------------------------------------------------------------- #
# DB migration                                                       #
# ----------------------------------------------------------------- #


def test_migrate_from_file_creates_five_adrs(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        result = adr_migrator.migrate_from_file(
            session,
            project_id=pid,
            md_path=LEGACY_MD,
        )
    assert len(result["created"]) == 5
    assert result["skipped"] == []

    with transactional(factory) as session:
        rows = adr_service.list_for_project(session, 1)
    assert len(rows) == 5
    for row in rows:
        assert row.status == "accepted"
        assert row.author == "human:migration"


def test_migrate_idempotent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_migrator.migrate_from_file(session, project_id=pid, md_path=LEGACY_MD)
    with transactional(factory) as session:
        result = adr_migrator.migrate_from_file(session, project_id=1, md_path=LEGACY_MD)
    assert result["created"] == []
    assert len(result["skipped"]) == 5
