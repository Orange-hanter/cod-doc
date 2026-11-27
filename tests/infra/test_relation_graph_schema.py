"""Схема графа связей и её место в реестре синхронизации (RFC 39 §3.3, REL-005)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.domain.relations import ENTITY_KINDS
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import make_engine
from cod_doc.infra.models import Base, ProjectModel, RelationModel
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


def test_uid_refs_point_to_real_columns_and_tables() -> None:
    tables = Base.metadata.tables
    for (table, column), disc in reg.UID_REFS.items():
        assert column in tables[table].c
        assert disc in tables[table].c
    for target in reg.UID_REF_KIND_TABLES.values():
        assert target in reg.UID_TABLES


def test_uid_ref_kinds_are_exactly_the_entity_kinds() -> None:
    assert set(reg.UID_REF_KIND_TABLES) == {k.value for k in ENTITY_KINDS}


def test_uid_columns_are_not_translated_by_capture() -> None:
    """uid глобален: перевод row_id ↔ uid по этим колонкам испортил бы значение."""
    assert not {k for k in reg.UID_REFS} & set(reg.POLYMORPHIC_FKS)
    info = cap.table_info(cap._mapper_for("relation"))  # type: ignore[arg-type]
    assert info is not None
    assert not {"from_uid", "to_uid"} & (set(info.fk_targets) | set(info.polymorphic))
    assert {"from_uid", "to_uid", "to_key", "type"} <= info.shipped


def test_check_columns_are_local() -> None:
    for column in ("resolved", "broken_reason", "last_checked"):
        assert reg.column_class("relation", column) is reg.SyncClass.LOCAL
    assert reg.column_class("relation", "from_uid") is reg.SyncClass.AUTHORED


@pytest.mark.parametrize(
    ("from_kind", "to_kind", "local"),
    [
        ("story", "task", False),
        ("story", "url", False),
        ("scenario", "document", True),
        ("story", "section", True),
        ("document", "story", True),
        ("story", None, True),
    ],
)
def test_edges_touching_documents_stay_local(
    from_kind: str, to_kind: str | None, local: bool
) -> None:
    row = {"from_kind": from_kind, "to_kind": to_kind}
    assert (reg.row_class("relation", row) is reg.SyncClass.LOCAL) is local


def test_edge_uid_is_deterministic() -> None:
    row = {
        "project_id": "p",
        "from_uid": "a",
        "type": "depends_on",
        "to_kind": "story",
        "to_uid": "b",
        "to_key": None,
    }
    assert reg.edge_uid("relation", row) == reg.edge_uid("relation", dict(row))
    assert reg.edge_uid("relation", row) != reg.edge_uid("relation", {**row, "to_uid": "c"})


def _inspect(url: str) -> tuple[set[str], dict[str, set[str]]]:
    """Таблицы и индексы по свежему движку: пул не держит снимок до миграции."""
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        idx = {
            t: {i["name"] for i in insp.get_indexes(t)}
            for t in ("relation", "relation_type")
            if t in tables
        }
        return tables, idx
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    run_alembic("upgrade", "head", db_url=url)
    assert {"relation", "relation_type"} <= _inspect(url)[0]
    run_alembic("downgrade", "-1", db_url=url)
    assert not {"relation", "relation_type"} & _inspect(url)[0]
    run_alembic("upgrade", "head", db_url=url)
    _, idx = _inspect(url)
    assert {"uq_relation_uid", "uq_relation_edge_literal", "ix_relation_to"} <= idx["relation"]
    assert "uq_relation_type_uid" in idx["relation_type"]


def test_literal_edge_duplicates_are_rejected(tmp_path: Path) -> None:
    """NULL в UNIQUE не равен NULL: дубль code-конца ловит частичный индекс."""
    url = f"sqlite:///{tmp_path / 'd.db'}"
    run_alembic("upgrade", "head", db_url=url)
    engine = make_engine(url)
    with Session(engine) as s:
        p = ProjectModel(slug="p", title="p", root_path="/tmp/p")
        s.add(p)
        s.flush()

        def edge() -> RelationModel:
            return RelationModel(
                project_id=p.row_id,
                from_kind="scenario",
                from_uid="u1",
                type="exercised_by",
                to_kind="code",
                to_key="a.py::f",
                author="t",
            )

        s.add(edge())
        s.flush()
        s.add(edge())
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
        s.add(
            RelationModel(
                project_id=p.row_id, from_kind="a", from_uid="u", type="t", to_kind="b", author="t"
            )
        )
        with pytest.raises(IntegrityError):  # CHECK: нужен to_uid или to_key
            s.flush()
    engine.dispose()
