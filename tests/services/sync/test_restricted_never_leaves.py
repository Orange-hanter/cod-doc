"""CLO-033: restricted не покидает реплику (RFC 29 §3.2, Q-015; SCN-096).

Acceptance: «Строки document sensitivity=restricted и его секций не попадают
ни в один changeset и снапшот; понижение до internal уезжает upsert'ом всего
документа; повышение уже уехавшего — предупреждение «история на remote»; sync
status показывает счётчик; тест на SCN-096».

Захват (CLO-006) и отзыв (CLO-046) закрывают почти всё; здесь — оставшаяся
дыра и защита в глубину. Дыра: документ, который ни разу не уезжал и повышен до
push, отзыва не пишет, а хвост его тела, захваченный, пока он был internal,
уезжал первым же push'ем. Упаковка теперь пересматривает хвост по текущим
меткам (``gate.shippable_tail``), а перед кодированием changeset'а и в каждой
строке снапшота стоит растяжка (``gate.check_ops``/``check_row``).

Проверка «на remote нет» — по байтам: каждый объект remote (changeset'ы
``CDC1`` и снапшоты ``CDS1`` распаковываются) ищется на uid документа и
секций и на тексты секций.
"""

from __future__ import annotations

import time
from compression import zstd
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType, ScenarioKind, Sensitivity
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_withdraw
from cod_doc.infra.db import transactional
from cod_doc.infra.models import DocumentModel, ScenarioModel, SectionModel
from cod_doc.services import doc_service, scenario_service
from cod_doc.services.sync_service import _local, digest, gate, snapshot, status
from tests.services.sync import _replicas as r
from tests.services.sync.test_restricted_withdraw import (
    DOC,
    SECTIONS,
    WARNING,
    _patch,
    _pull,
    _push,
    _sections,
    _sensitivity,
    _set_sensitivity,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.infra.remote.file import FileRemote

_MAGICS = (b"CDC1", b"CDS1")
_TICK = 0.003
OPEN_DOC = "open-spec"


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    return a, b, r.remote(tmp_path)


def _remote_bytes(store: FileRemote) -> bytes:
    """Всё, что лежит на remote, с распакованными changeset'ами и снапшотами."""
    out: list[bytes] = []
    for path in sorted(store.root.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if data[:4] in _MAGICS:
            data = zstd.decompress(data[4:])
        out.append(data)
    return b"\n".join(out)


def _doc_uids(rep: r.Replica, doc_key: str = DOC) -> set[str]:
    with transactional(rep.sf, commit=False) as s:
        doc = s.scalars(
            select(DocumentModel).where(
                DocumentModel.project_id == rep.project_id, DocumentModel.doc_key == doc_key
            )
        ).one()
        sections = s.scalars(select(SectionModel.uid).where(SectionModel.document_id == doc.row_id))
        return {str(doc.uid), *(str(u) for u in sections)}


def _assert_absent(store: FileRemote, uids: set[str], texts: list[str]) -> None:
    blob = _remote_bytes(store)
    assert blob, "remote пуст — проверять нечего"
    leaked = [u for u in uids if u.encode() in blob]
    leaked += [t for t in texts if t.encode() in blob]
    assert leaked == []


def _has_doc(rep: r.Replica, doc_key: str = DOC) -> bool:
    with transactional(rep.sf, commit=False) as s:
        return (
            s.scalars(
                select(DocumentModel.row_id).where(
                    DocumentModel.project_id == rep.project_id, DocumentModel.doc_key == doc_key
                )
            ).one_or_none()
            is not None
        )


def _scenario_on_doc(rep: r.Replica) -> None:
    with transactional(rep.sf) as s:
        scenario_service.create(
            s,
            project_id=rep.project_id,
            title="сценарий на документе",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            scenario_id="SCN-001",
            doc_key=DOC,
            section_anchor="intro",
            steps=["шаг"],
        )


def _scenario_ref(rep: r.Replica) -> tuple[object, object]:
    with transactional(rep.sf, commit=False) as s:
        row = s.scalars(select(ScenarioModel).where(ScenarioModel.scenario_id == "SCN-001")).one()
        return row.doc_key, row.section_anchor


def _verified(*reps: r.Replica, store: FileRemote) -> None:
    for rep in reps:
        assert digest.verify(rep.sf, r.SLUG, store=store).equal, rep.name


def _raised_before_push(a: r.Replica) -> None:
    """Документ с телом, тегом и сценарием — internal, в хвосте; потом restricted."""
    r.make_doc(a, DOC, SECTIONS)
    r.tag_doc(a, DOC, "secret")
    _scenario_on_doc(a)
    _patch(a, "intro", "секрет один, правка")
    result = _set_sensitivity(a, Sensitivity.RESTRICTED)
    assert result.warnings == ()  # не уезжал — отзывать нечего, предупреждать не о чем


# --- дыра: никогда не уезжавший документ, повышенный до push ---------------------------


def test_never_shipped_document_raised_before_push_does_not_ship(pair: tuple) -> None:
    a, b, store = pair
    _raised_before_push(a)
    uids = _doc_uids(a)

    pushed = _push(a, store)
    _pull(b, store)

    assert pushed.snapshot is not None  # первый push пишет и снапшот — он тоже проверен
    _assert_absent(store, uids, [*SECTIONS.values(), "секрет один, правка"])
    assert not _has_doc(b)
    assert _scenario_ref(b) == (None, None)  # ссылка придержана, сценарий уехал
    assert r.pending(a) == 0
    _verified(a, b, store=store)


def test_never_shipped_document_lowered_later_ships_whole(pair: tuple) -> None:
    a, b, store = pair
    _raised_before_push(a)
    _push(a, store)
    _pull(b, store)

    _set_sensitivity(a, Sensitivity.INTERNAL)
    _push(a, store)
    _pull(b, store)

    assert _sensitivity(b) == "internal"
    assert _sections(b) == _sections(a)
    assert _scenario_ref(b) == (DOC, "intro")
    assert r.authored_state(b) == r.authored_state(a)
    _verified(a, b, store=store)


def test_regate_strips_withheld_columns_of_referrers_in_the_tail(pair: tuple) -> None:
    """Сценарий на документе, ставшем restricted, уезжает без ссылки на него."""
    a, _b, store = pair
    r.make_doc(a, DOC, SECTIONS)
    _push(a, store)  # документ уехал internal
    _scenario_on_doc(a)  # сценарий — в хвосте, со ссылкой
    doc_id = r.doc_id(a, DOC)
    # Повышение мимо захвата: ни отзыва, ни чистки хвоста — работает только
    # пересверка при упаковке.
    with transactional(a.sf) as s, cap.suspended(s):
        s.get_one(DocumentModel, doc_id).sensitivity = Sensitivity.RESTRICTED.value

    with transactional(a.sf) as s:
        dropped = sync_withdraw.hold_restricted(s, a.project_id)
        ops = gate.shippable_tail(s, a.project_id)

    scenario_ops = [op for op in ops if op.entity_kind == "scenario"]
    assert scenario_ops, "сценарий не должен пропасть целиком"
    for op in scenario_ops:
        assert not {"doc_key", "section_anchor", "doc_content_hash"} & set(op.fields)
    assert all(op["entity_kind"] != "scenario" for op in dropped)


# --- растяжка ---------------------------------------------------------------------------


def test_tripwire_stops_push_when_the_gate_lets_a_restricted_op_through(
    pair: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _b, store = pair
    _raised_before_push(a)
    pending = r.pending(a)
    monkeypatch.setattr(sync_withdraw, "hold_restricted", lambda *_a, **_k: [])

    with pytest.raises(cap.RestrictedLeakError, match=r"^SYNC-001 changeset несёт строки"):
        _push(a, store)

    assert store.get_ref(_project_uid(a), "main") is None
    assert r.pending(a) == pending  # упаковка откатилась, хвост цел


def test_tripwire_lets_the_withdrawal_op_through(pair: tuple) -> None:
    """Операция отзыва называет restricted-документ — это единственное исключение."""
    a, b, store = pair
    r.make_doc(a, DOC, SECTIONS)
    _push(a, store)
    _pull(b, store)

    result = _set_sensitivity(a, Sensitivity.RESTRICTED)
    _push(a, store)
    _pull(b, store)

    assert any(WARNING in w for w in result.warnings)
    assert _sensitivity(b) == "restricted"
    assert set(_sections(b).values()) == {""}


def test_tripwire_stops_snapshot_with_a_restricted_row(
    pair: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _b, _store = pair
    r.make_doc(a, DOC, SECTIONS)
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    section_uid = sorted(_doc_uids(a) - {_doc_uid(a)})[0]
    real = cap.shipped_rows

    def leaky(session, project_id, tables):  # type: ignore[no-untyped-def]
        yield from real(session, project_id, tables)
        yield "section", section_uid, '{"body": "x"}'

    monkeypatch.setattr(cap, "shipped_rows", leaky)
    with (
        transactional(a.sf, commit=False) as s,
        pytest.raises(cap.RestrictedLeakError, match=r"^SYNC-001 снапшот несёт строку"),
    ):
        ref = r_project_ref(s)
        snapshot.build(s, ref, head="0" * 64, digest_hex="0" * 64)


def test_snapshot_builder_skips_restricted_rows(pair: tuple) -> None:
    a, _b, _store = pair
    r.make_doc(a, DOC, SECTIONS)
    r.make_doc(a, OPEN_DOC, {"x": "открыто"})
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    with transactional(a.sf, commit=False) as s:
        data = snapshot.build(s, r_project_ref(s), head="0" * 64, digest_hex="0" * 64)
    text = zstd.decompress(data[4:])
    assert all(u.encode() not in text for u in _doc_uids(a))
    assert all(u.encode() in text for u in _doc_uids(a, OPEN_DOC))


# --- sync status ------------------------------------------------------------------------


def test_status_counts_restricted_documents_and_their_rows(pair: tuple) -> None:
    a, _b, store = pair
    r.make_doc(a, DOC, SECTIONS)
    r.tag_doc(a, DOC, "secret")
    r.make_doc(a, OPEN_DOC, {"x": "открыто"})
    _set_sensitivity(a, Sensitivity.RESTRICTED)

    st = status.status(a.sf, r.SLUG, store=store)

    assert st.restricted_local == 1
    assert st.restricted_rows == 1 + len(SECTIONS) + 1  # документ, секции, тег


# --- SCN-096 ------------------------------------------------------------------------------


def _make_restricted(a: r.Replica) -> None:
    with transactional(a.sf) as s:
        doc = doc_service.create(
            s,
            project_id=a.project_id,
            doc_key=DOC,
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title=DOC,
            author=r.AUTHOR,
            path=f"docs/{DOC}.md",
            sensitivity=Sensitivity.RESTRICTED,
            reindex=False,
        )
        for i, (anchor, body) in enumerate(SECTIONS.items()):
            doc_service.add_section(
                s,
                project_id=a.project_id,
                document_id=doc.row_id,
                anchor=anchor,
                heading=anchor.title(),
                level=2,
                position=i,
                body=body,
                author=r.AUTHOR,
                reindex=False,
            )


def test_scn_096_restricted_document_never_reaches_any_changeset_or_snapshot(
    pair: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCN-096: restricted-документ с тремя секциями — ни один объект remote его не несёт.

    Шаги сценария (правка секции → push → sync status) повторены несколько
    раз; каждый push пишет снапшот (``SNAPSHOT_EVERY = 1``). Затем — понижение
    (документ приезжает целиком) и повторное повышение (отзыв с
    предупреждением, у получателя тело затёрто).
    """
    a, b, store = pair
    monkeypatch.setattr(snapshot, "SNAPSHOT_EVERY", 1)
    _make_restricted(a)
    r.make_doc(a, OPEN_DOC, {"x": "открыто"})
    uids = _doc_uids(a)
    texts = list(SECTIONS.values())
    snapshots: set[str | None] = set()

    for i in range(3):
        _patch(a, "body", f"секрет правка {i}")
        texts.append(f"секрет правка {i}")
        r.make_task(a, f"DEM-{i:03d}")  # уезжающая правка рядом — push не пустой
        pushed = _push(a, store)
        snapshots.add(pushed.snapshot)
        st = status.status(a.sf, r.SLUG, store=store)
        assert (st.restricted_local, st.pending_ops) == (1, 0)
        _assert_absent(store, uids, texts)

    assert None not in snapshots
    assert len(snapshots) == 3
    _pull(b, store)
    assert not _has_doc(b)
    assert _has_doc(b, OPEN_DOC)
    _verified(a, b, store=store)

    # Понижение: документ уезжает первым upsert'ом целиком.
    _set_sensitivity(a, Sensitivity.INTERNAL)
    _push(a, store)
    _pull(b, store)
    assert _sensitivity(b) == "internal"
    assert _sections(b) == _sections(a)
    assert _sections(b)["body"] == "секрет правка 2"
    _verified(a, b, store=store)

    # Повторное повышение: отзыв, предупреждение, у получателя тело затёрто.
    time.sleep(_TICK)
    result = _set_sensitivity(a, Sensitivity.RESTRICTED)
    _push(a, store)
    _pull(b, store)
    assert any(WARNING in w for w in result.warnings)
    assert _sensitivity(b) == "restricted"
    assert set(_sections(b).values()) == {""}
    assert status.status(b.sf, r.SLUG, store=store).restricted_local == 1
    _verified(a, b, store=store)


# --- обвязка ------------------------------------------------------------------------------


def _project_uid(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return r_project_ref(s).uid


def _doc_uid(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return str(s.get_one(DocumentModel, r.doc_id(rep, DOC)).uid)


def r_project_ref(session: Session) -> _local.ProjectRef:
    return _local.project_ref(session, r.SLUG)
