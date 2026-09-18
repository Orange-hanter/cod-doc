"""Регрессия: ретеншн-GC не должен падать на снапшотах с оценками.

`_gc_branch` удаляет снапшот через `session.delete`. У связи
``CodeStructureSnapshotModel.assessments`` не было ни ``cascade``, ни
``passive_deletes``, поэтому ORM подгружал дочерние записи и обнулял им FK
вместо того, чтобы дать сработать ``ondelete="CASCADE"`` на уровне БД, — а
``structure_assessment.snapshot_id`` объявлен NOT NULL. Ingest переставал
работать навсегда, начиная с RETENTION_PER_BRANCH+1 снапшота на ветке.
"""

from __future__ import annotations

import copy

from sqlalchemy import func, select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models.structure import (
    CodeStructureSnapshotModel,
    StructureAssessmentModel,
)
from cod_doc.services.structure_protocol import RETENTION_PER_BRANCH
from cod_doc.services.structure_service import ingest_structure
from tests.services.test_structure_service import _assessment, _facts, _project


def _hex_at(index: int) -> str:
    """Валидный уникальный sha256-подобный фингерпринт по индексу."""
    return f"{index:064x}"


def test_gc_past_retention_does_not_crash_on_assessments(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    base_facts = _facts()
    base_assessment = _assessment()
    total = RETENTION_PER_BRANCH + 3
    with transactional(factory) as session:
        project_id = _project(session)
        for index in range(total):
            facts = copy.deepcopy(base_facts)
            facts["fingerprint"] = _hex_at(index + 1)
            assessment = copy.deepcopy(base_assessment)
            assessment["fingerprint"] = _hex_at(1000 + index)
            assessment["factsFingerprint"] = facts["fingerprint"]
            ingest_structure(
                session,
                project_id,
                facts=facts,
                assessment=assessment,
                trust_tier="trusted_local",
                project_slug="demo",
            )

        snapshots = session.execute(
            select(func.count()).select_from(CodeStructureSnapshotModel)
        ).scalar_one()
        orphans = session.execute(
            select(func.count())
            .select_from(StructureAssessmentModel)
            .where(StructureAssessmentModel.snapshot_id.is_(None))
        ).scalar_one()

    # Часть снапшотов удержана ссылками (current, открытые находки), но GC
    # обязан отработать без падения и не оставить осиротевших оценок.
    assert snapshots <= total
    assert orphans == 0
