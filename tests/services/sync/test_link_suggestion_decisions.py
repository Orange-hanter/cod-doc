"""CLO-025: решения по подсказкам ссылок уезжают по ключу (RFC 29 §3.2, Q-017).

Acceptance: «link_suggestion.state ≠ pending уезжает по ключу (uid
секции-источника, to_doc_key, uid секции-цели); отклонённая на A подсказка
после pull и пересчёта на B не всплывает, принятая остаётся принятой.»

Подсказка — derived: каждая реплика считает её сама. Пересчёт здесь —
``semantic.suggest_for_section`` с подменённой коллекцией ChromaDB: она
всегда находит документ ``target``, как нашла бы его на любой реплике с тем же
корпусом.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import transactional
from cod_doc.infra.models import SectionModel, SyncOpModel
from cod_doc.infra.models.link_suggestions import LinkSuggestionModel
from cod_doc.services.link_service import semantic
from cod_doc.services.sync_service import digest, pull, push
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.config import Config
    from cod_doc.infra.remote.file import FileRemote

SOURCE = "source"
TARGET = "target"


class _Collection:
    """ChromaDB, которая на любой запрос находит ``docs/target.md``."""

    def count(self) -> int:
        return 1

    def query(self, **_kwargs: object) -> dict[str, Any]:
        return {
            "documents": [["target"]],
            "metadatas": [[{"path": f"docs/{TARGET}.md"}]],
            "distances": [[0.05]],
        }


@pytest.fixture
def chroma(monkeypatch: pytest.MonkeyPatch) -> Config:
    from cod_doc.core import embeddings, reindex

    monkeypatch.setattr(reindex, "get_collection", lambda *_a, **_k: _Collection())
    monkeypatch.setattr(embeddings, "settings_from_config", lambda _cfg: None)
    config: Any = SimpleNamespace(chroma_path="unused")
    return config  # type: ignore[no-any-return]


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    r.make_doc(a, SOURCE, {"intro": "Про target и его устройство.\n"})
    r.make_doc(a, TARGET, {"body": "Цель ссылки.\n"})
    _push(a, store)
    _pull(b, store)
    return a, b, store


def _push(rep: r.Replica, store: FileRemote) -> None:
    push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _source_section(rep: r.Replica) -> int:
    with transactional(rep.sf, commit=False) as s:
        return s.scalars(
            select(SectionModel.row_id).where(
                SectionModel.document_id == r.doc_id(rep, SOURCE), SectionModel.anchor == "intro"
            )
        ).one()


def _suggest(rep: r.Replica, config: Config) -> list[dict[str, Any]]:
    """Пересчёт подсказок секции-источника на реплике."""
    with transactional(rep.sf) as s:
        return semantic.suggest_for_section(
            s, _source_section(rep), config, project_id=rep.project_id
        )


def _state(rep: r.Replica) -> str | None:
    with transactional(rep.sf, commit=False) as s:
        return s.scalars(
            select(LinkSuggestionModel.state).where(
                LinkSuggestionModel.from_section_id == _source_section(rep),
                LinkSuggestionModel.to_doc_key == TARGET,
            )
        ).one_or_none()


def _decide(rep: r.Replica, state: str) -> None:
    with transactional(rep.sf) as s:
        row_id = s.scalars(
            select(LinkSuggestionModel.row_id).where(
                LinkSuggestionModel.from_section_id == _source_section(rep)
            )
        ).one()
        assert semantic.update_suggestion_state(
            s, row_id, state, project_id=rep.project_id, author=r.AUTHOR
        )


def _pending(rep: r.Replica) -> list[dict[str, Any]]:
    with transactional(rep.sf, commit=False) as s:
        return semantic.list_suggestions(s, project_id=rep.project_id, state="pending")


def test_rejection_rides_by_key_and_does_not_resurface_after_recompute(pair, chroma) -> None:
    a, b, store = pair
    assert [s["to_doc_key"] for s in _suggest(a, chroma)] == [TARGET]
    _decide(a, "rejected")
    # B посчитала ту же подсказку сама — она pending и не уезжала.
    assert [s["to_doc_key"] for s in _suggest(b, chroma)] == [TARGET]
    _push(a, store)

    _pull(b, store)

    assert _state(b) == "rejected"
    assert _suggest(b, chroma) == [], "отклонённая подсказка всплыла при пересчёте"
    assert _pending(b) == []
    assert _state(b) == "rejected"


def test_rejection_pulled_before_the_replica_computed_it(pair, chroma) -> None:
    a, b, store = pair
    _suggest(a, chroma)
    _decide(a, "rejected")
    _push(a, store)
    _pull(b, store)
    assert _state(b) == "rejected"

    assert _suggest(b, chroma) == []
    assert _pending(b) == []


def test_acceptance_stays_accepted_after_recompute(pair, chroma) -> None:
    a, b, store = pair
    _suggest(a, chroma)
    _decide(a, "accepted")
    _push(a, store)
    _pull(b, store)

    _suggest(b, chroma)

    assert _state(b) == "accepted"
    with transactional(b.sf, commit=False) as s:
        accepted = semantic.list_suggestions(s, project_id=b.project_id, state="accepted")
    assert [x["to_doc_key"] for x in accepted] == [TARGET]
    # Пересчёт на B решения не трогает: в хвосте B нет операции подсказки.
    with transactional(b.sf, commit=False) as s:
        tail = s.scalars(
            select(SyncOpModel.entity_kind).where(SyncOpModel.changeset.is_(None))
        ).all()
    assert "link_suggestion" not in tail


def test_decision_key_is_the_deterministic_uid_on_both_replicas(pair, chroma) -> None:
    """Операция решения адресована uuid5 ключа — одинаковым на A и B."""
    a, b, store = pair
    _suggest(a, chroma)
    _decide(a, "rejected")
    with transactional(a.sf, commit=False) as s:
        op_uid = s.scalars(
            select(SyncOpModel.entity_uid).where(SyncOpModel.entity_kind == "link_suggestion")
        ).one()
    _push(a, store)
    _pull(b, store)
    with transactional(b.sf, commit=False) as s:
        sec_uid = s.get_one(SectionModel, _source_section(b)).uid
    key = {"from_section_id": sec_uid, "to_doc_key": TARGET, "to_section_id": None}
    assert op_uid == reg.edge_uid("link_suggestion", key)
    with transactional(a.sf, commit=False) as sa, transactional(b.sf, commit=False) as sb:
        assert digest.compute(sa, a.project_id) == digest.compute(sb, b.project_id)
