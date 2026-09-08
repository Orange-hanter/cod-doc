"""Регрессия: неуникальные id продюсера не роняют ingest, blob остаётся полным.

Воспроизводит реальный случай: на снапшоте cod-doc (5000 сущностей) два
symbol-узла из JSON-схем схлопнулись в один ``observedId``, и ingest падал
с ``IntegrityError`` на UNIQUE(snapshot_id, observed_id).
"""

from __future__ import annotations

import copy

from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models.structure import CodeEntityModel
from cod_doc.services.structure_service import (
    get_snapshot_by_fingerprint,
    get_snapshot_payload,
    ingest_structure,
    replay_snapshot,
)
from tests.services.test_structure_service import _facts, _project


def _with_duplicate_entity() -> dict:
    """Два разных узла с одинаковым observedId — ровно как у продюсера."""
    facts = _facts()
    first = facts["facts"]["entities"][0]
    twin = copy.deepcopy(first)
    twin["name"] = "validate_twin"
    twin["label"] = "validate_twin()"
    facts["facts"]["entities"] = [first, twin]
    return facts


def test_duplicate_observed_id_is_dropped_not_fatal(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _with_duplicate_entity()
    with transactional(factory) as session:
        project_id = _project(session)
        result = ingest_structure(
            session,
            project_id,
            facts=facts,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        indexes = result["indexes"]
        assert isinstance(indexes, dict)
        # Обе записи пришли, в индекс легла одна, вторая отброшена.
        assert indexes["entities"] == 1
        assert indexes["duplicates"] == 1

        snapshot = get_snapshot_by_fingerprint(session, project_id, str(facts["fingerprint"]))
        assert snapshot is not None

        rows = list(
            session.execute(
                select(CodeEntityModel).where(CodeEntityModel.snapshot_id == snapshot.row_id)
            ).scalars()
        )
        assert len(rows) == 1
        # Первая запись выигрывает.
        assert rows[0].name == "validate"

        # Blob сохранён целиком: в payload по-прежнему обе сущности.
        stored = get_snapshot_payload(snapshot)
        entities = stored["facts"]["entities"]  # type: ignore[index]
        assert len(entities) == 2
        assert {item["name"] for item in entities} == {"validate", "validate_twin"}

        # Replay из блоба даёт тот же результат, а не падает.
        replayed = replay_snapshot(session, project_id, snapshot.row_id)
        assert replayed["indexes"] == indexes


def test_unique_ids_report_no_duplicates(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _project(session)
        result = ingest_structure(
            session,
            project_id,
            facts=_facts(),
            trust_tier="trusted_local",
            project_slug="demo",
        )
        indexes = result["indexes"]
        assert isinstance(indexes, dict)
        assert indexes["entities"] == 1
        assert indexes["duplicates"] == 0
