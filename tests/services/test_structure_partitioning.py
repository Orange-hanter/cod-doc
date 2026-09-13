"""Партиционирование: усечённый снапшот не закрывает находки, партиции — закрывают.

Причина ограничения не в величине лимитов, а в том, что сверка была глобальной:
частичный снапшот судил обо всех находках проекта. Со `scope` снапшот отвечает
только за свою партицию, и репозиторий, не влезающий в лимиты целиком,
разбивается на части — каждая полна внутри себя.
"""

from __future__ import annotations

import copy

from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models.structure import StructureFindingModel
from cod_doc.services.structure_service import ingest_structure
from tests.services.test_structure_service import _assessment, _facts, _hex, _project


def _statuses(session, project_id, scope: str) -> list[str]:
    return [
        row.status
        for row in session.execute(
            select(StructureFindingModel).where(
                StructureFindingModel.project_id == project_id,
                StructureFindingModel.scope == scope,
            )
        ).scalars()
    ]


def _closed_assessment(base: dict) -> dict:
    closed = copy.deepcopy(base)
    closed["assessments"]["contractScenarios"] = [
        {
            **closed["assessments"]["contractScenarios"][0],
            "status": "covered",
            "statusReason": "test link, execution and scenario-specific evidence satisfied",
            "missingEvidence": [],
        }
    ]
    closed["hints"] = []
    return closed


def _scoped(facts: dict, scope: str, fingerprint: str) -> dict:
    out = copy.deepcopy(facts)
    out["provenance"]["scope"] = scope
    out["fingerprint"] = fingerprint
    return out


def test_truncated_snapshot_still_refuses_to_close(engine_with_schema) -> None:
    """Усечение по-прежнему запрещает закрытие: снапшот неполон в своей партиции."""
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    facts["provenance"]["truncated"] = True
    with transactional(factory) as session:
        project_id = _project(session)
        ingest_structure(
            session,
            project_id,
            facts=facts,
            assessment=_assessment(),
            trust_tier="trusted_local",
            project_slug="demo",
        )
        assert set(_statuses(session, project_id, "src")) == {"open"}

        closed = _closed_assessment(_assessment())
        for tag_f, tag_a in (("a", "b"), ("c", "d")):
            nxt = copy.deepcopy(facts)
            nxt["fingerprint"] = _hex(tag_f)
            nxt_assess = copy.deepcopy(closed)
            nxt_assess["fingerprint"] = _hex(tag_a)
            nxt_assess["factsFingerprint"] = nxt["fingerprint"]
            ingest_structure(
                session,
                project_id,
                facts=nxt,
                assessment=nxt_assess,
                trust_tier="trusted_local",
                project_slug="demo",
            )
        # Ни один усечённый снапшот не вправе закрыть находку.
        assert set(_statuses(session, project_id, "src")) == {"pending_verify"}


def test_partitions_reconcile_independently_and_close(engine_with_schema) -> None:
    """Две полные партиции закрывают свои находки, не трогая чужие."""
    factory = make_session_factory(engine_with_schema)
    base = _facts()
    assessment = _assessment()
    with transactional(factory) as session:
        project_id = _project(session)

        for scope, fp in (("cod_doc/cli", _hex("a")), ("cod_doc/services", _hex("b"))):
            scoped_assessment = copy.deepcopy(assessment)
            scoped_assessment["fingerprint"] = _hex("c" if scope.endswith("cli") else "d")
            scoped_assessment["factsFingerprint"] = fp
            ingest_structure(
                session,
                project_id,
                facts=_scoped(base, scope, fp),
                assessment=scoped_assessment,
                trust_tier="trusted_local",
                project_slug="demo",
            )

        assert set(_statuses(session, project_id, "cod_doc/cli")) == {"open"}
        assert set(_statuses(session, project_id, "cod_doc/services")) == {"open"}

        # Чиним только партицию cli: вторая партиция обязана остаться нетронутой.
        closed = _closed_assessment(assessment)
        for tag_f, tag_a in (("e", "f"), ("0", "1")):
            fixed_assessment = copy.deepcopy(closed)
            fixed_assessment["fingerprint"] = _hex(tag_a)
            fixed_assessment["factsFingerprint"] = _hex(tag_f)
            ingest_structure(
                session,
                project_id,
                facts=_scoped(base, "cod_doc/cli", _hex(tag_f)),
                assessment=fixed_assessment,
                trust_tier="trusted_local",
                project_slug="demo",
            )

        assert set(_statuses(session, project_id, "cod_doc/cli")) == {"resolved"}
        assert set(_statuses(session, project_id, "cod_doc/services")) == {"open"}
