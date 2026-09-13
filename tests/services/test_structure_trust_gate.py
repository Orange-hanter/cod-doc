"""Регрессия: недоверенный вызов не двигает находки, даже по доверенному снапшоту.

Гейт оценки проверял тир СОХРАНЁННОГО снапшота. Заголовок снапшота
(fingerprint / headSha / branchRef) читается публично из `/structure/latest`, а
REST-ingest смонтирован без аутентификации, поэтому достаточно было отправить
те же факты с подделанной оценкой: `_get_or_create_snapshot` возвращал
доверенную строку, гейт проходил по ней, и две публикации закрывали все находки
проекта. Доверять обязаны оба — и снапшот, и текущий вызов.
"""

from __future__ import annotations

import copy

from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models.structure import StructureFindingModel
from cod_doc.services.structure_service import ingest_structure
from tests.services.test_structure_service import _assessment, _facts, _project


def _statuses(session, project_id) -> set[str]:
    return {
        row.status
        for row in session.execute(
            select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
        ).scalars()
    }


def test_untrusted_call_cannot_close_findings_of_a_trusted_snapshot(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
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
        assert _statuses(session, project_id) == {"open"}

        # Подделанная «чистая» оценка тех же фактов, отправленная недоверенно.
        forged = copy.deepcopy(_assessment())
        forged["assessments"]["contractScenarios"] = []
        forged["hints"] = []
        forged["temporalAlignment"] = "aligned"

        for _ in range(2):
            ingest_structure(
                session,
                project_id,
                facts=copy.deepcopy(facts),
                assessment=copy.deepcopy(forged),
                trust_tier="untrusted",
                project_slug="demo",
            )

        # Ни pending_verify, ни resolved: недоверенный вызов не считает drift.
        assert _statuses(session, project_id) == {"open"}
