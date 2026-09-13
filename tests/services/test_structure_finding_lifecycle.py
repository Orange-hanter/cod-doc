"""Регрессия: закрытая находка не должна переоткрываться на следующих снапшотах."""

from __future__ import annotations

import copy

from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models.structure import StructureFindingModel
from cod_doc.services.structure_service import ingest_structure
from tests.services.test_structure_service import (
    _assessment,
    _facts,
    _hex,
    _project,
)


def _statuses(session, project_id) -> list[str]:
    return [
        row.status
        for row in session.execute(
            select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
        ).scalars()
    ]


def test_resolved_finding_does_not_reopen_on_later_clean_snapshots(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    assessment = _assessment()
    seen: list[list[str]] = []
    with transactional(factory) as session:
        project_id = _project(session)
        ingest_structure(
            session,
            project_id,
            facts=facts,
            assessment=assessment,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        seen.append(_statuses(session, project_id))

        closed = copy.deepcopy(assessment)
        closed["assessments"]["contractScenarios"] = [
            {
                **closed["assessments"]["contractScenarios"][0],
                "status": "covered",
                "statusReason": "test link, execution and scenario-specific evidence satisfied",
                "missingEvidence": [],
            }
        ]
        closed["hints"] = []

        # Снапшоты 2..5: пробел устранён и остаётся устранённым, все aligned.
        for tag_facts, tag_assess in (("a", "b"), ("c", "d"), ("e", "f"), ("0", "1")):
            nxt_facts = copy.deepcopy(facts)
            nxt_facts["fingerprint"] = _hex(tag_facts)
            nxt_assess = copy.deepcopy(closed)
            nxt_assess["fingerprint"] = _hex(tag_assess)
            nxt_assess["factsFingerprint"] = nxt_facts["fingerprint"]
            ingest_structure(
                session,
                project_id,
                facts=nxt_facts,
                assessment=nxt_assess,
                trust_tier="trusted_local",
                project_slug="demo",
            )
            seen.append(_statuses(session, project_id))

    print("\nstatus per snapshot:")
    for i, statuses in enumerate(seen, start=1):
        print(f"  snapshot {i}: {sorted(set(statuses))}")

    # Once resolved, a finding whose gap stays gone must not reopen.
    assert seen[2] and all(s == "resolved" for s in seen[2]), seen[2]
    assert all(s == "resolved" for s in seen[3]), f"4th clean snapshot flipped: {seen[3]}"
    assert all(s == "resolved" for s in seen[4]), f"5th clean snapshot flipped: {seen[4]}"


def test_promoted_finding_stays_in_progress_on_later_clean_snapshots(
    engine_with_schema,
) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    assessment = _assessment()
    with transactional(factory) as session:
        project_id = _project(session)
        ingest_structure(
            session,
            project_id,
            facts=facts,
            assessment=assessment,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        rows = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        assert rows
        for row in rows:
            row.status = "in_progress"
            row.promoted_task_id = "STR-001"
        session.flush()

        closed = copy.deepcopy(assessment)
        closed["assessments"]["contractScenarios"] = [
            {
                **closed["assessments"]["contractScenarios"][0],
                "status": "covered",
                "statusReason": "test link, execution and scenario-specific evidence satisfied",
                "missingEvidence": [],
            }
        ]
        closed["hints"] = []
        nxt_facts = copy.deepcopy(facts)
        nxt_facts["fingerprint"] = _hex("a")
        nxt_assess = copy.deepcopy(closed)
        nxt_assess["fingerprint"] = _hex("b")
        nxt_assess["factsFingerprint"] = nxt_facts["fingerprint"]
        ingest_structure(
            session,
            project_id,
            facts=nxt_facts,
            assessment=nxt_assess,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        after = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        assert after
        assert all(row.status == "in_progress" for row in after)
        assert all(row.promoted_task_id == "STR-001" for row in after)
