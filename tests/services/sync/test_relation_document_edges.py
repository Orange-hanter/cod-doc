"""REL-018: рёбра графа связей с концом-документом/секцией едут по метке документа.

Раньше такие рёбра были local целиком: метку по ``uid`` захват не разрешал.
Теперь ребро наследует ограничение документа, как остальные ссылки
``DOC_REFS``: к public/internal-документу уезжает, к restricted — нет (ни само
ребро, ни его ``note``), и следует за документом при повышении и понижении.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.domain.entities import Priority, Sensitivity
from cod_doc.infra.db import transactional
from cod_doc.infra.models import RelationModel
from cod_doc.services import adr_service, relation_service, story_service
from cod_doc.services.sync_service import digest
from tests.services.sync import _replicas as r
from tests.services.sync.test_restricted_never_leaves import _remote_bytes
from tests.services.sync.test_restricted_withdraw import (
    DOC,
    SECTIONS,
    _pull,
    _push,
    _set_sensitivity,
)

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

NOTE = "СЕКРЕТНАЯ-ЗАМЕТКА-РЕБРА"


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    r.make_doc(a, DOC, SECTIONS)
    with transactional(a.sf) as s:
        story_service.create_section(
            s, project_id=a.project_id, key="core", title="Core", author=r.AUTHOR
        )
        story_service.create(
            s,
            project_id=a.project_id,
            story_id="US-001",
            persona="dev",
            narrative="n",
            priority=Priority.MEDIUM,
            author=r.AUTHOR,
            acceptance=["first"],
        )
        adr_service.create(
            s, project_id=a.project_id, title="Decision", author=r.AUTHOR, adr_id="ADR-001"
        )
    return a, b, store


def _relate(a: r.Replica, frm: str, type_: str, to: str, note: str | None = None) -> None:
    with transactional(a.sf) as s:
        relation_service.relate(
            s, project_id=a.project_id, from_ref=frm, type=type_, to_ref=to, author=r.AUTHOR,
            note=note,
        )  # fmt: skip


def _count(rep: r.Replica) -> int:
    with transactional(rep.sf, commit=False) as s:
        return s.scalar(select(func.count()).select_from(RelationModel)) or 0


def _verified(*reps: r.Replica, store: FileRemote) -> None:
    for rep in reps:
        assert digest.verify(rep.sf, r.SLUG, store=store).equal, rep.name


def test_edges_to_document_replicate(pair: tuple) -> None:
    a, b, store = pair
    _relate(a, "story:US-001", "specified_in", f"document:{DOC}", NOTE)
    _relate(a, "adr:ADR-001", "decided_in", f"document:{DOC}")

    _push(a, store)
    _pull(b, store)

    assert _count(b) == 2
    assert r.authored_state(b) == r.authored_state(a)
    assert NOTE.encode() in _remote_bytes(store)
    _verified(a, b, store=store)


@pytest.mark.parametrize("to", [f"document:{DOC}", f"section:{DOC}#intro"])
def test_edge_to_restricted_document_never_leaves(pair: tuple, to: str) -> None:
    a, b, store = pair
    _push(a, store)  # документ уехал internal
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _relate(a, "story:US-001", "specified_in", to, NOTE)

    _push(a, store)
    _pull(b, store)

    assert _count(a) == 1
    assert _count(b) == 0
    assert NOTE.encode() not in _remote_bytes(store)
    _verified(a, b, store=store)


@pytest.mark.parametrize("to", [f"document:{DOC}", f"section:{DOC}#intro"])
def test_restricting_document_withdraws_synced_edge_and_lowering_publishes(
    pair: tuple, to: str
) -> None:
    a, b, store = pair
    _relate(a, "story:US-001", "specified_in", to, NOTE)
    _push(a, store)
    _pull(b, store)
    assert "relation" in r.authored_state(b)

    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _push(a, store)
    _pull(b, store)
    assert "relation" not in r.authored_state(a)
    assert "relation" not in r.authored_state(b)
    _verified(a, b, store=store)

    _set_sensitivity(a, Sensitivity.INTERNAL)
    _push(a, store)
    _pull(b, store)
    assert "relation" in r.authored_state(a)
    assert r.authored_state(b) == r.authored_state(a)
    _verified(a, b, store=store)


def test_edge_made_while_restricted_ships_with_its_revision_after_lowering(pair: tuple) -> None:
    a, b, store = pair
    _push(a, store)
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _relate(a, "story:US-001", "specified_in", f"document:{DOC}", NOTE)
    _push(a, store)
    _pull(b, store)
    assert _count(b) == 0

    _set_sensitivity(a, Sensitivity.INTERNAL)
    _push(a, store)
    _pull(b, store)

    assert _count(b) == 1
    assert r.authored_state(b) == r.authored_state(a)
    _verified(a, b, store=store)
