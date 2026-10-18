"""CLO-046: документ, ставший restricted, отзывается у получателей (RFC 29 §3.2, Q-015).

Acceptance: «Документ, ушедший как internal, после повышения до restricted и
push/pull на получателях имеет sensitivity=restricted, тело секций и
придержанные колонки очищены (или секции удалены — по RFC); verify совпадает у
владельца и получателей; новые правки документа не уезжают; CLI/MCP выдают
предупреждение о истории на remote».

Владелец — A, получатели — B и C. Отзыв — одна операция ``upsert`` документа
с единственным полем ``sensitivity``; тела секций и комментариев получатель
очищает сам, когда операция отзыва перевела документ в restricted. Понижение
обратно — первый upsert всего документа, его детей, ссылок на него и ревизий,
которых remote не видел.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    ScenarioKind,
    Sensitivity,
)
from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import (
    ActivityEventModel,
    DocumentModel,
    ScenarioModel,
    SectionModel,
    SyncOpModel,
)
from cod_doc.services import doc_service, scenario_service
from cod_doc.services.sync_service import digest, markers, pull, push
from cod_doc.services.validation import ValidationError
from tests.services.sync import _replicas as r
from tests.services.sync.test_apply_untrusted import _forge, _refused, _uid

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

DOC = "secret-spec"
SECTIONS = {"intro": "секрет один", "body": "секрет два", "tail": "секрет три"}
WARNING = "история документа остаётся на remote"
_TICK = 0.003


@pytest.fixture
def trio(tmp_path: Path) -> tuple[r.Replica, r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    c = r.replica_b(tmp_path, a, "c")
    store = r.remote(tmp_path)
    r.make_doc(a, DOC, SECTIONS)
    r.tag_doc(a, DOC, "secret")
    with transactional(a.sf) as s:
        scenario_service.create(
            s,
            project_id=a.project_id,
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
    _sync(a, b, c, store)
    assert _sections(b) == SECTIONS
    return a, b, c, store


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _sync(a: r.Replica, b: r.Replica, c: r.Replica, store: FileRemote) -> None:
    _push(a, store)
    _pull(b, store)
    _pull(c, store)


def _set_sensitivity(rep: r.Replica, value: Sensitivity) -> doc_service.MetadataUpdate:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        return doc_service.update_metadata(
            s,
            project_id=rep.project_id,
            document_id=r.doc_id(rep, DOC),
            author=r.AUTHOR,
            sensitivity=value,
            allow_declassify=True,
        )


def _patch(rep: r.Replica, anchor: str, body: str) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        doc_service.patch_section(
            s,
            project_id=rep.project_id,
            document_id=r.doc_id(rep, DOC),
            anchor=anchor,
            new_body=body,
            author=r.AUTHOR,
            reindex=False,
        )


def _sections(rep: r.Replica) -> dict[str, str]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.execute(
            select(SectionModel.anchor, SectionModel.body)
            .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
            .where(DocumentModel.project_id == rep.project_id, DocumentModel.doc_key == DOC)
        )
        return {str(k): str(v) for k, v in rows.all()}


def _sensitivity(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return str(
            s.scalars(
                select(DocumentModel.sensitivity).where(
                    DocumentModel.project_id == rep.project_id, DocumentModel.doc_key == DOC
                )
            ).one()
        )


def _scenario(rep: r.Replica) -> dict[str, object]:
    with transactional(rep.sf, commit=False) as s:
        row = s.scalars(select(ScenarioModel).where(ScenarioModel.scenario_id == "SCN-001")).one()
        return {
            "doc_key": row.doc_key,
            "section_anchor": row.section_anchor,
            "title": row.title,
        }


def _verified(*reps: r.Replica, store: FileRemote) -> None:
    for rep in reps:
        result = digest.verify(rep.sf, r.SLUG, store=store)
        assert result.equal, (rep.name, result)
    digests = set()
    for rep in reps:
        with transactional(rep.sf, commit=False) as s:
            digests.add(digest.compute(s, rep.project_id))
    assert len(digests) == 1
    state = r.authored_state(reps[0])
    for rep in reps[1:]:
        assert r.authored_state(rep) == state, rep.name


def _withdraw_ops(rep: r.Replica) -> list[dict[str, Any]]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.execute(
            select(SyncOpModel.entity_kind, SyncOpModel.fields, SyncOpModel.kind).where(
                SyncOpModel.entity_kind == "document"
            )
        ).all()
    out = []
    for kind, fields, op_kind in rows:
        payload = json.loads(fields) if isinstance(fields, str) else fields
        if op_kind == "upsert" and set(payload) == {"sensitivity"}:
            out.append({"entity_kind": kind, **payload})
    return out


# --- отзыв --------------------------------------------------------------------------


def test_withdrawn_document_is_restricted_and_blank_on_every_receiver(trio: tuple) -> None:
    a, b, c, store = trio

    result = _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)

    assert any(WARNING in w for w in result.warnings)
    assert _withdraw_ops(a) == [{"entity_kind": "document", "sensitivity": "restricted"}]
    for rep in (b, c):
        assert _sensitivity(rep) == "restricted", rep.name
        assert set(_sections(rep).values()) == {""}, rep.name
    # Владелец свой текст не теряет.
    assert _sections(a) == SECTIONS
    _verified(a, b, c, store=store)


def test_edits_after_withdrawal_do_not_ship(trio: tuple) -> None:
    a, b, c, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)

    _patch(a, "intro", "новый секрет")

    assert r.pending(a) == 0
    assert _push(a, store).changesets == []
    assert set(_sections(b).values()) == {""}
    _verified(a, b, c, store=store)


def test_unpushed_edit_before_raise_is_dropped_from_the_tail(trio: tuple) -> None:
    """Правка, не успевшая уехать до повышения, уезжать не должна: она — тоже секрет."""
    a, b, c, store = trio
    _patch(a, "body", "последний секрет")

    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)

    with transactional(a.sf, commit=False) as s:
        shipped = [json.loads(f) for f in s.scalars(select(SyncOpModel.fields)).all()]
    assert not any(op.get("body") == "последний секрет" for op in shipped)
    assert set(_sections(b).values()) == {""}
    _verified(a, b, c, store=store)


def test_receiver_tail_on_withdrawn_document_does_not_reach_the_owner(trio: tuple) -> None:
    """Неотправленная правка получателя после отзыва — local: иначе pull владельца
    упёрся бы в строку, которую его реестр держит local."""
    a, b, c, store = trio
    _patch(b, "intro", "правка B до отзыва")
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _push(a, store)

    _pull(b, store)
    assert r.pending(b) == 0
    _push(b, store)
    _pull(a, store)
    _pull(c, store)

    assert set(_sections(b).values()) == {""}
    _verified(a, b, c, store=store)


def test_owner_absorbs_peer_edits_made_before_the_withdrawal(trio: tuple) -> None:
    """Правка B ушла на remote до отзыва, A повысил, не видя её: pull A её поглощает.

    Отвергнуть её — навсегда остановить pull владельца: строка у него local.
    """
    a, b, c, store = trio
    _patch(b, "intro", "правка B в полёте")
    with transactional(b.sf) as s:
        doc_service.add_section(
            s,
            project_id=b.project_id,
            document_id=r.doc_id(b, DOC),
            anchor="from-b",
            heading="From B",
            level=2,
            position=3,
            body="секция B",
            author=r.AUTHOR,
            reindex=False,
        )
    _push(b, store)
    _set_sensitivity(a, Sensitivity.RESTRICTED)

    _sync(a, b, c, store)

    assert _sections(a) == SECTIONS
    for rep in (b, c):
        assert set(_sections(rep).values()) == {""}, rep.name
    _verified(a, b, c, store=store)

    _set_sensitivity(a, Sensitivity.INTERNAL)
    _sync(a, b, c, store)
    for rep in (b, c):
        assert _sections(rep) == SECTIONS, rep.name
    _verified(a, b, c, store=store)


def test_receiver_section_created_only_in_its_tail_is_dropped(trio: tuple) -> None:
    a, b, c, store = trio
    with transactional(b.sf) as s:
        doc_service.add_section(
            s,
            project_id=b.project_id,
            document_id=r.doc_id(b, DOC),
            anchor="local-b",
            heading="Local B",
            level=2,
            position=3,
            body="не успела уехать",
            author=r.AUTHOR,
            reindex=False,
        )
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _push(a, store)

    _pull(b, store)

    assert set(_sections(b)) == set(SECTIONS)
    _set_sensitivity(a, Sensitivity.INTERNAL)
    _sync(a, b, c, store)
    _push(b, store)
    assert _sections(b) == SECTIONS
    _verified(a, b, c, store=store)


def test_receiver_cannot_lower_a_document_withdrawn_by_the_owner(trio: tuple) -> None:
    """У получателя от документа — оболочка с пустым телом: понижение разослало бы пустоту."""
    a, b, c, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)

    with pytest.raises(ValidationError) as err:
        _set_sensitivity(b, Sensitivity.INTERNAL)

    assert err.value.code == "DM-005"
    assert _sensitivity(b) == "restricted"


# --- понижение ------------------------------------------------------------------------


def test_lowering_reships_the_document_and_converges(trio: tuple) -> None:
    a, b, c, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)
    # Пока документ restricted: правка, новая секция, удаление секции — всё local.
    _patch(a, "intro", "рассекреченный текст")
    with transactional(a.sf) as s:
        doc_service.add_section(
            s,
            project_id=a.project_id,
            document_id=r.doc_id(a, DOC),
            anchor="added",
            heading="Added",
            level=2,
            position=3,
            body="новая секция",
            author=r.AUTHOR,
            reindex=False,
        )
        doc_service.delete_section(
            s,
            project_id=a.project_id,
            document_id=r.doc_id(a, DOC),
            anchor="tail",
            author=r.AUTHOR,
            reindex=False,
        )
    assert r.pending(a) == 0

    _set_sensitivity(a, Sensitivity.INTERNAL)
    _sync(a, b, c, store)

    expected = {"intro": "рассекреченный текст", "body": "секрет два", "added": "новая секция"}
    assert _sections(a) == expected
    for rep in (b, c):
        assert _sensitivity(rep) == "internal", rep.name
        assert _sections(rep) == expected, rep.name
        assert _scenario(rep) == _scenario(a), rep.name
    _verified(a, b, c, store=store)

    # После понижения документ снова живёт обычной жизнью.
    _patch(b, "body", "правка B")
    _push(b, store)
    _pull(a, store)
    _pull(c, store)
    assert _sections(a)["body"] == "правка B"
    _verified(a, b, c, store=store)


def test_raise_and_lower_in_one_changeset_converge(trio: tuple) -> None:
    """Отзыв и понижение в одном changeset'е: понижение находит отзыв того же changeset'а."""
    a, b, c, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _set_sensitivity(a, Sensitivity.INTERNAL)

    _sync(a, b, c, store)

    for rep in (b, c):
        assert _sensitivity(rep) == "internal", rep.name
        assert _sections(rep) == SECTIONS, rep.name
    _verified(a, b, c, store=store)


def test_never_shipped_document_raised_to_restricted_writes_no_withdrawal(
    tmp_path: Path,
) -> None:
    a = r.replica_a(tmp_path)
    r.make_doc(a, DOC, SECTIONS)

    result = _set_sensitivity(a, Sensitivity.RESTRICTED)

    assert _withdraw_ops(a) == []
    assert result.warnings == ()


# --- недоверенный ввод ------------------------------------------------------------------


def test_forged_withdrawal_with_extra_fields_writes_nothing(trio: tuple) -> None:
    """Отзыв — только ``sensitivity``. Правка, после которой строка local, не пишется:
    ни метка, ни заголовок, ни затирание тела."""
    a, b, _, store = trio
    doc_uid = _uid(b, DocumentModel, doc_key=DOC)
    before = r.authored_state(b)
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "document",
                "entity_uid": doc_uid,
                "fields": {"sensitivity": "restricted", "title": "подмена"},
                "base": {"sensitivity": "internal", "title": DOC},
            }
        ],
    )

    _pull(b, store)

    assert _sensitivity(b) == "internal"
    assert _sections(b) == SECTIONS
    assert r.authored_state(b) == before


def test_remote_cannot_lower_a_document_restricted_only_here(trio: tuple) -> None:
    """Документ, который у B restricted и никогда не уезжал, remote не понизит."""
    a, b, _, store = trio
    secret_uid = str(uuid.uuid7())
    with Session(b.engine) as s:
        doc = doc_service.create(
            s,
            project_id=b.project_id,
            doc_key="b-only",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="b-only",
            author=r.AUTHOR,
            path="docs/b-only.md",
            sensitivity=Sensitivity.RESTRICTED,
            reindex=False,
        )
        s.get_one(DocumentModel, doc.row_id).uid = secret_uid
        s.commit()
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "document",
                "entity_uid": secret_uid,
                "fields": {"sensitivity": "internal"},
                "base": {"sensitivity": "restricted"},
            }
        ],
    )

    _refused(b, store, "local на этой реплике")


# --- security review: понижение, отзыв, хвост --------------------------------------


def _doc_fields(rep: r.Replica) -> dict[str, Any]:
    """Поля документа формой первого upsert'а (Q-015): все уезжающие колонки."""
    with transactional(rep.sf, commit=False) as s:
        row = (
            s.execute(
                select(DocumentModel.__table__).where(
                    DocumentModel.project_id == rep.project_id, DocumentModel.doc_key == DOC
                )
            )
            .mappings()
            .one()
        )
        fields = cap.shipped_fields(s, "document", {**row, "sensitivity": "internal"})
    assert fields is not None
    return {
        k: (v.isoformat().replace("+00:00", "Z") if hasattr(v, "isoformat") else v)
        for k, v in fields.items()
    }


def _replica_uid(rep: r.Replica) -> str:
    with transactional(rep.sf) as s:
        return self_replica(s, rep.project_id)


def _lowering(rep_from: r.Replica, doc_uid: str) -> dict[str, Any]:
    fields = _doc_fields(rep_from)
    fields["sensitivity"] = "internal"
    return {"entity_kind": "document", "entity_uid": doc_uid, "fields": fields, "base": {}}


def test_remote_lowering_does_not_open_a_document_restricted_here(trio: tuple) -> None:
    """B сам повысил уехавший документ: чужое понижение его не открывает."""
    a, b, _, store = trio
    _set_sensitivity(b, Sensitivity.RESTRICTED)  # отзыв B в хвосте, не отправлен
    doc_uid = _uid(b, DocumentModel, doc_key=DOC)
    _forge(a, store, [_lowering(a, doc_uid)])

    _pull(b, store)

    assert _sensitivity(b) == "restricted"
    assert _sections(b) == SECTIONS


def test_lowering_is_accepted_only_from_the_replica_that_withdrew(trio: tuple) -> None:
    a, b, c, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)
    doc_uid = _uid(b, DocumentModel, doc_key=DOC)
    _forge(a, store, [_lowering(a, doc_uid)], replica=_replica_uid(c))

    _pull(b, store)

    assert _sensitivity(b) == "restricted"


def test_lowering_must_have_the_shape_of_a_first_upsert(trio: tuple) -> None:
    a, b, c, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)
    doc_uid = _uid(b, DocumentModel, doc_key=DOC)
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "document",
                "entity_uid": doc_uid,
                "fields": {"sensitivity": "internal"},
                "base": {"sensitivity": "restricted"},
            }
        ],
    )

    _pull(b, store)

    assert _sensitivity(b) == "restricted"


def test_peer_withdrawal_does_not_take_lowering_away_from_the_replica_that_restricted_first(
    trio: tuple,
) -> None:
    a, b, _, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)  # A первым, в хвосте
    _set_sensitivity(b, Sensitivity.RESTRICTED)
    _push(b, store)
    _pull(a, store)  # отзыв B у A поглощается: документ и так restricted A

    result = _set_sensitivity(a, Sensitivity.INTERNAL)

    assert result.changes["sensitivity"]["to"] == "internal"
    assert _sections(a) == SECTIONS


def test_withdrawal_quarantines_unpushed_receiver_edits(trio: tuple) -> None:
    a, b, _, store = trio
    _patch(b, "intro", "черновик B")
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _push(a, store)

    _pull(b, store)

    with transactional(b.sf, commit=False) as s:
        payloads = s.scalars(
            select(ActivityEventModel.payload).where(
                ActivityEventModel.kind == "sync.withdraw_quarantined"
            )
        ).all()
    assert len(payloads) == 1
    assert "черновик B" in json.dumps(payloads[0], ensure_ascii=False)


def test_withdrawal_of_a_document_never_shared_here_is_refused(trio: tuple) -> None:
    a, b, _, store = trio
    hidden_uid = str(uuid.uuid7())
    with Session(b.engine) as s:  # мимо захвата: документ B, которого журнал не знает
        doc = doc_service.create(
            s,
            project_id=b.project_id,
            doc_key="b-hidden",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="b-hidden",
            author=r.AUTHOR,
            path="docs/b-hidden.md",
            sensitivity=Sensitivity.INTERNAL,
            reindex=False,
        )
        s.get_one(DocumentModel, doc.row_id).uid = hidden_uid
        s.commit()
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "document",
                "entity_uid": hidden_uid,
                "fields": {"sensitivity": "restricted"},
                "base": {"sensitivity": "internal"},
            }
        ],
    )

    _refused(b, store, "не бывал")


def test_tail_purge_finds_body_ops_ordered_before_the_op_naming_the_document(
    trio: tuple,
) -> None:
    """Правка секции в хвосте раньше её удаления: только удаление называет документ."""
    a, b, c, store = trio
    _patch(a, "tail", "утечка из хвоста")
    with transactional(a.sf) as s:
        doc_service.delete_section(
            s,
            project_id=a.project_id,
            document_id=r.doc_id(a, DOC),
            anchor="tail",
            author=r.AUTHOR,
            reindex=False,
        )

    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)

    with transactional(a.sf, commit=False) as s:
        shipped = s.scalars(select(SyncOpModel.fields)).all()
    assert not any("утечка из хвоста" in f for f in shipped)
    assert "утечка из хвоста" not in _sections(b).values()


def _section_uid(rep: r.Replica, anchor: str) -> str:
    with transactional(rep.sf, commit=False) as s:
        return str(
            s.scalars(
                select(SectionModel.uid).where(
                    SectionModel.document_id == r.doc_id(rep, DOC), SectionModel.anchor == anchor
                )
            ).one()
        )


def test_marker_about_a_withdrawn_section_is_absorbed(trio: tuple) -> None:
    """Маркер разрешения (CLO-045) о секции отозванного документа — как правка: поглощается."""
    a, b, c, store = trio
    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)
    signature = {
        "entity_kind": "section",
        "entity_uid": _section_uid(b, "intro"),
        "field": "body",
        "kind": "text",
    }
    _forge(
        a,
        store,
        [
            {
                "entity_kind": markers.MARKER_KIND,
                "entity_uid": str(uuid.uuid7()),
                "fields": signature,
            }
        ],
    )

    _pull(b, store)

    assert set(_sections(b).values()) == {""}


def test_own_unpushed_marker_about_the_document_body_is_purged(trio: tuple) -> None:
    a, b, c, store = trio
    signature = ("section", _section_uid(a, "intro"), "body", "text")
    with transactional(a.sf) as s:
        marker_uid = markers.record(s, a.project_id, signature, author=r.AUTHOR)

    _set_sensitivity(a, Sensitivity.RESTRICTED)
    _sync(a, b, c, store)

    with transactional(a.sf, commit=False) as s:
        left = s.scalars(select(SyncOpModel.op_id).where(SyncOpModel.entity_uid == marker_uid))
        assert left.all() == []


# --- поверхности --------------------------------------------------------------------------


def test_mcp_doc_update_warns_about_remote_history(trio: tuple, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import doc_tools

    a, *_ = trio
    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (a.sf, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: a.project_id)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    tool = mcp._tool_manager._tools["doc_update"].fn

    out = tool(project=r.SLUG, doc_key=DOC, sensitivity="restricted")

    assert any(WARNING in w for w in out["warnings"]), out


def test_cli_doc_update_warns_about_remote_history(trio: tuple, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.cli.doc import cmd_update, doc

    a, *_ = trio
    monkeypatch.setattr(cmd_update, "_make_session", lambda project, cfg: a.sf)
    monkeypatch.setattr(cmd_update, "_require_project_id", lambda session, project: a.project_id)

    plain = CliRunner().invoke(
        doc, ["update", DOC, "-p", r.SLUG, "--sensitivity", "restricted"], obj={"config": None}
    )
    assert plain.exit_code == 0, plain.output
    assert WARNING in plain.output

    as_json = CliRunner().invoke(
        doc,
        ["update", DOC, "-p", r.SLUG, "--sensitivity", "confidential", "--json"],
        obj={"config": None},
    )
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output)["warnings"] == []
