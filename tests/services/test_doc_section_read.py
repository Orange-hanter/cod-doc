"""AFT-009: doc_service.read_sections и revision_service.heads_for_entities."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from sqlalchemy import event, select

from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind, Sensitivity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel, RevisionModel, SectionModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


class Seed(NamedTuple):
    doc_id: int
    section_ids: dict[str, int]
    # revision_id последней записи каждой секции, снятый сразу после записи.
    heads: dict[str, str | None]
    # revision_id записи add_section у секции, которой потом сделан patch.
    alpha_first_revision: str


def _last_written_revision(session: Session, section_id: int) -> str:
    """revision_id строки, которую только что вставила запись сида."""
    return session.execute(
        select(RevisionModel.revision_id)
        .where(
            RevisionModel.entity_kind == EntityKind.SECTION.value,
            RevisionModel.entity_id == section_id,
        )
        .order_by(RevisionModel.row_id.desc())
        .limit(1)
    ).scalar_one()


def _seed(engine: Engine) -> Seed:
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        doc = docs.create(
            session,
            project_id=proj.row_id,
            doc_key="spec",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="Spec",
            author="human:dakh",
            owner="human:dakh",
            sensitivity=Sensitivity.INTERNAL,
            preamble="Intro.",
        )
        assert doc.row_id is not None
        section_ids: dict[str, int] = {}
        heads: dict[str, str | None] = {}
        for position, anchor in enumerate(("alpha", "beta", "gamma")):
            sec = docs.add_section(
                session,
                document_id=doc.row_id,
                anchor=anchor,
                heading=anchor.title(),
                level=2,
                position=position,
                body=f"BODY-{anchor.upper()}",
                author="human:dakh",
            )
            assert sec.row_id is not None
            section_ids[anchor] = sec.row_id
            heads[anchor] = _last_written_revision(session, sec.row_id)
        alpha_first = heads["alpha"]
        assert alpha_first is not None
        docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor="alpha",
            new_body="BODY-ALPHA",
            author="human:dakh",
        )
        # patch с тем же телом — no-op; настоящая правка ниже.
        docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor="alpha",
            new_body="BODY-ALPHA-V2",
            author="human:dakh",
        )
        heads["alpha"] = _last_written_revision(session, section_ids["alpha"])
        # Секция без ревизий — вставка мимо сервиса.
        bare = SectionModel(
            document_id=doc.row_id,
            anchor="delta",
            heading="Delta",
            level=2,
            position=3,
            body="BODY-DELTA",
            content_hash="0" * 64,
        )
        session.add(bare)
        session.flush()
        section_ids["delta"] = bare.row_id
        heads["delta"] = None
        return Seed(doc.row_id, section_ids, heads, alpha_first)


@contextmanager
def _capture_sql(engine: Engine) -> Iterator[list[str]]:
    statements: list[str] = []

    def _on_execute(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, executemany: bool
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)


def _read(engine: Engine, doc_id: int, anchors: list[str]) -> list[dict[str, Any]]:
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        return docs.read_sections(session, doc_id, anchors)


def test_read_one_section_returns_contract_keys(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    seed = _seed(engine_with_schema)
    result = _read(engine_with_schema, seed.doc_id, ["beta"])
    assert len(result) == 1
    assert set(result[0]) == {
        "anchor",
        "heading",
        "level",
        "body",
        "content_hash",
        "head_revision_id",
    }
    assert result[0]["anchor"] == "beta"
    assert result[0]["heading"] == "Beta"
    assert result[0]["level"] == 2
    assert result[0]["body"] == "BODY-BETA"
    assert result[0]["content_hash"] == docs.content_hash("BODY-BETA")


def test_read_many_sections_keeps_request_order(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    seed = _seed(engine_with_schema)
    result = _read(engine_with_schema, seed.doc_id, ["gamma", "alpha", "gamma"])
    assert [r["anchor"] for r in result] == ["gamma", "alpha"]
    assert [r["body"] for r in result] == ["BODY-GAMMA", "BODY-ALPHA-V2"]


def test_head_revision_id_after_patch(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    seed = _seed(engine_with_schema)
    result = _read(engine_with_schema, seed.doc_id, ["alpha", "beta", "gamma", "delta"])
    by_anchor = {r["anchor"]: r["head_revision_id"] for r in result}
    # patch сдвинул head с ревизии add_section на ревизию правки.
    assert by_anchor["alpha"] == seed.heads["alpha"]
    assert by_anchor["alpha"] != seed.alpha_first_revision
    # add_section сам пишет ревизию — head у beta/gamma её revision_id.
    assert by_anchor["beta"] == seed.heads["beta"]
    assert by_anchor["gamma"] == seed.heads["gamma"]
    assert by_anchor["delta"] is None

    # Сквозная проверка: head годится как expected_parent_revision_id.
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        docs.patch_section(
            session,
            document_id=seed.doc_id,
            anchor="alpha",
            new_body="BODY-ALPHA-V3",
            author="human:dakh",
            expected_parent_revision_id=by_anchor["alpha"],
        )


def test_unknown_anchor_is_structured_miss(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    seed = _seed(engine_with_schema)
    result = _read(engine_with_schema, seed.doc_id, ["alpha", "nope"])
    assert len(result) == 2
    assert result[0]["anchor"] == "alpha"
    assert result[0]["body"] == "BODY-ALPHA-V2"
    miss = result[1]
    assert set(miss) == {"anchor", "found", "hint", "available_anchors", "related_tools"}
    assert miss["anchor"] == "nope"
    assert miss["found"] is False
    assert miss["available_anchors"] == ["alpha", "beta", "gamma", "delta"]
    assert miss["related_tools"] == ["doc_get"]
    assert "nope" in miss["hint"]
    assert "spec" in miss["hint"]


def test_read_does_not_load_other_bodies(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    seed = _seed(engine_with_schema)
    with _capture_sql(engine_with_schema) as statements:
        result = _read(engine_with_schema, seed.doc_id, ["beta"])
    assert result[0]["body"] == "BODY-BETA"
    assert statements
    assert not any("document_body" in s for s in statements)
    body_reads = [s for s in statements if "section.body" in s]
    assert body_reads
    for s in body_reads:
        assert "section.anchor IN" in s
    # miss нет — нет и запроса за списком якорей.
    assert not any("ORDER BY section.position" in s for s in statements)


def test_empty_anchors_and_missing_doc_raise(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    seed = _seed(engine_with_schema)
    with pytest.raises(ValueError, match="anchors"):
        _read(engine_with_schema, seed.doc_id, [])
    with pytest.raises(ValueError, match="#999999"):
        _read(engine_with_schema, 999999, ["alpha"])


def test_heads_for_entities_batch(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    seed = _seed(engine_with_schema)
    ids = [seed.section_ids["alpha"], seed.section_ids["beta"], seed.section_ids["delta"]]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        with _capture_sql(engine_with_schema) as statements:
            heads = rev.heads_for_entities(session, EntityKind.SECTION, ids)
        assert heads == {
            seed.section_ids["alpha"]: seed.heads["alpha"],
            seed.section_ids["beta"]: seed.heads["beta"],
            seed.section_ids["delta"]: None,
        }
        assert len([s for s in statements if "FROM revision" in s]) == 1
        with _capture_sql(engine_with_schema) as statements:
            assert rev.heads_for_entities(session, EntityKind.SECTION, []) == {}
        assert statements == []
