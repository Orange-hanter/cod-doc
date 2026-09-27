"""OQM-002: question_service — CRUD, lifecycle, options, links, verify, write path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, text

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Priority,
    QuestionLinkKind,
    QuestionRelation,
    QuestionStatus,
    TaskType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DocumentModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    SectionModel,
)
from cod_doc.services import doc_service, question_service, search_service, task_service
from cod_doc.services.question_service import (
    QuestionAlreadyExistsError,
    QuestionNotFoundError,
    QuestionOptionNotFoundError,
    QuestionStateError,
)
from cod_doc.services.validation import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

AUTHOR = "human:test"


def _seed(session: Session, root: str = "/tmp/oq") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="oq", title="P", root_path=root, config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _task(session: Session, pid: int, task_id: str) -> None:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=pid, scope="oq-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
    session.add(sec)
    session.flush()
    task_service.create(
        session,
        project_id=pid,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=task_id,
        title="Answer it",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=AUTHOR,
    )


def _create(session: Session, pid: int, **kw: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {
        "title": "Какой платёжный провайдер для RU?",
        "question": "ЮKassa или CloudPayments?",
        "author": AUTHOR,
        **kw,
    }
    return question_service.create(session, project_id=pid, **params)  # type: ignore[arg-type]


@pytest.fixture
def factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


# --------------------------------------------------------------------------- #
# create / get / list                                                          #
# --------------------------------------------------------------------------- #


def test_create_allocates_sequential_ids_with_options(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        first = _create(session, pid, options=[("ЮKassa", "дешевле"), ("CloudPayments", None)])
        second = _create(session, pid, title="Второй")
        opts = question_service.list_options(session, first.row_id)
    assert (first.question_id, second.question_id) == ("Q-001", "Q-002")
    assert first.status is QuestionStatus.OPEN
    assert [(o.position, o.title, o.body) for o in opts] == [
        (1, "ЮKassa", "дешевле"),
        (2, "CloudPayments", None),
    ]


def test_create_rejects_duplicate_and_empty(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        _create(session, pid, question_id="Q-010")
        with pytest.raises(QuestionAlreadyExistsError):
            _create(session, pid, question_id="Q-010")
        with pytest.raises(ValidationError):
            _create(session, pid, question="   ")
        with pytest.raises(ValidationError):
            _create(session, pid, question_id="OQ-1")
        # id allocation continues after an explicit id
        assert _create(session, pid).question_id == "Q-011"


def test_list_orders_open_first_then_priority_and_filters(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        low = _create(session, pid, priority=Priority.LOW, owner="alice")
        high = _create(session, pid, priority=Priority.HIGH)
        done = _create(session, pid, priority=Priority.CRITICAL)
        question_service.resolve(
            session, project_id=pid, question_id=done.question_id, author=AUTHOR, resolution="ok"
        )
        ordered = [q.question_id for q in question_service.list_for_project(session, pid)]
        only_open = question_service.list_for_project(session, pid, status=QuestionStatus.OPEN)
        by_owner = question_service.list_for_project(session, pid, owner="alice")
    assert ordered == [high.question_id, low.question_id, done.question_id]
    assert {q.question_id for q in only_open} == {high.question_id, low.question_id}
    assert [q.question_id for q in by_owner] == [low.question_id]


def test_update_patches_and_clears(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid, owner="alice", context="old")
        updated = question_service.update(
            session,
            project_id=pid,
            question_id=q.question_id,
            author=AUTHOR,
            owner="",
            context="",
            priority=Priority.HIGH,
        )
    assert updated.owner is None
    assert updated.context is None
    assert updated.priority is Priority.HIGH


# --------------------------------------------------------------------------- #
# lifecycle                                                                    #
# --------------------------------------------------------------------------- #


def test_resolve_by_adr_records_edge_and_chosen_option(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid, options=[("A", None), ("B", None)])
        resolved = question_service.resolve(
            session,
            project_id=pid,
            question_id=q.question_id,
            author=AUTHOR,
            by_adr="ADR-014",
            chosen_option=2,
        )
        links = question_service.list_links(session, q.row_id)
        opts = question_service.list_options(session, q.row_id)
        via_adr = question_service.list_for_project(
            session, pid, linked_to=(QuestionLinkKind.ADR, "ADR-014")
        )
    assert resolved.status is QuestionStatus.RESOLVED
    assert resolved.resolved_by_adr == "ADR-014"
    assert resolved.resolved_at is not None
    assert [(lk.to_kind, lk.to_ref, lk.relation) for lk in links] == [
        (QuestionLinkKind.ADR, "ADR-014", QuestionRelation.RESOLVED_BY)
    ]
    assert [o.chosen for o in opts] == [False, True]
    assert [x.question_id for x in via_adr] == [q.question_id]


def test_resolve_requires_an_answer_and_open_status(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid)
        with pytest.raises(QuestionStateError):
            question_service.resolve(
                session, project_id=pid, question_id=q.question_id, author=AUTHOR
            )
        with pytest.raises(QuestionOptionNotFoundError):
            question_service.resolve(
                session,
                project_id=pid,
                question_id=q.question_id,
                author=AUTHOR,
                chosen_option=5,
            )
        question_service.drop(
            session, project_id=pid, question_id=q.question_id, author=AUTHOR, resolution="неважно"
        )
        with pytest.raises(QuestionStateError):
            question_service.resolve(
                session, project_id=pid, question_id=q.question_id, author=AUTHOR, resolution="x"
            )


def test_reopen_clears_answer_but_keeps_edges(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid, options=[("A", None)])
        question_service.resolve(
            session,
            project_id=pid,
            question_id=q.question_id,
            author=AUTHOR,
            by_adr="ADR-001",
            chosen_option=1,
            resolution="A",
        )
        reopened = question_service.reopen(
            session, project_id=pid, question_id=q.question_id, author=AUTHOR
        )
        opts = question_service.list_options(session, q.row_id)
        links = question_service.list_links(session, q.row_id)
        with pytest.raises(QuestionStateError):
            question_service.reopen(
                session, project_id=pid, question_id=q.question_id, author=AUTHOR
            )
    assert reopened.status is QuestionStatus.OPEN
    assert (reopened.resolution, reopened.resolved_by_adr, reopened.resolved_at) == (
        None,
        None,
        None,
    )
    assert not opts[0].chosen
    assert len(links) == 1


def test_missing_question_raises(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(QuestionNotFoundError):
            question_service.update(
                session, project_id=pid, question_id="Q-404", author=AUTHOR, title="x"
            )


# --------------------------------------------------------------------------- #
# options                                                                      #
# --------------------------------------------------------------------------- #


def test_option_positions_are_stable_ids(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid, options=[("A", None), ("B", None)])
        question_service.remove_option(
            session, project_id=pid, question_id=q.question_id, position=1, author=AUTHOR
        )
        added = question_service.add_option(
            session, project_id=pid, question_id=q.question_id, title="C", author=AUTHOR
        )
        question_service.update_option(
            session,
            project_id=pid,
            question_id=q.question_id,
            position=2,
            title="B2",
            body="плюсы",
            author=AUTHOR,
        )
        opts = question_service.list_options(session, q.row_id)
    assert added.position == 3
    assert [(o.position, o.title, o.body) for o in opts] == [(2, "B2", "плюсы"), (3, "C", None)]


# --------------------------------------------------------------------------- #
# links                                                                        #
# --------------------------------------------------------------------------- #


def test_link_is_idempotent_and_unlink_reports(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid)
        for _ in range(2):
            question_service.link(
                session,
                project_id=pid,
                question_id=q.question_id,
                to_kind=QuestionLinkKind.CODE,
                to_ref="cod_doc/app.py#L10-L20",
                relation=QuestionRelation.ABOUT,
                author=AUTHOR,
            )
        assert len(question_service.list_links(session, q.row_id)) == 1
        kw = {
            "project_id": pid,
            "question_id": q.question_id,
            "to_kind": QuestionLinkKind.CODE,
            "to_ref": "cod_doc/app.py#L10-L20",
            "relation": QuestionRelation.ABOUT,
            "author": AUTHOR,
        }
        assert question_service.unlink(session, **kw) is True  # type: ignore[arg-type]
        assert question_service.unlink(session, **kw) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kind", "ref"),
    [
        (QuestionLinkKind.TASK, "not a task"),
        (QuestionLinkKind.ADR, "ADR14"),
        (QuestionLinkKind.SECTION, "docs/x"),
        (QuestionLinkKind.CODE, "/etc/passwd"),
        (QuestionLinkKind.CODE, "../outside.py"),
        (QuestionLinkKind.URL, "ftp://x"),
        (QuestionLinkKind.DOCUMENT, " padded "),
    ],
)
def test_link_rejects_malformed_refs(factory, kind: QuestionLinkKind, ref: str) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid)
        with pytest.raises(ValidationError):
            question_service.link(
                session,
                project_id=pid,
                question_id=q.question_id,
                to_kind=kind,
                to_ref=ref,
                relation=QuestionRelation.ABOUT,
                author=AUTHOR,
            )


def test_questions_for_targets_batches_open_only(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        a = _create(session, pid)
        b = _create(session, pid)
        for q in (a, b):
            question_service.link(
                session,
                project_id=pid,
                question_id=q.question_id,
                to_kind=QuestionLinkKind.TASK,
                to_ref="OQ-001",
                relation=QuestionRelation.BLOCKS,
                author=AUTHOR,
            )
        question_service.drop(
            session, project_id=pid, question_id=b.question_id, author=AUTHOR, resolution="x"
        )
        found = question_service.questions_for_targets(
            session, pid, [(QuestionLinkKind.TASK, "OQ-001"), (QuestionLinkKind.TASK, "OQ-002")]
        )
    assert {k: [q.question_id for q in v] for k, v in found.items()} == {
        ("task", "OQ-001"): [a.question_id]
    }


# --------------------------------------------------------------------------- #
# verify                                                                       #
# --------------------------------------------------------------------------- #


def test_verify_resolves_each_kind(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def pay():\n    return 1\n", encoding="utf-8")
    with transactional(factory) as session:
        pid = _seed(session, root=str(tmp_path))
        _task(session, pid, "OQ-001")
        doc = doc_service.create(
            session,
            project_id=pid,
            doc_key="docs/billing",
            title="Billing",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            author=AUTHOR,
            path="docs/billing.md",
            owner="core",
        )
        assert doc.row_id is not None
        doc_service.add_section(
            session,
            document_id=doc.row_id,
            anchor="providers",
            heading="Providers",
            level=2,
            position=0,
            body="text",
            author=AUTHOR,
        )
        q = _create(session, pid)
        edges = {
            (QuestionLinkKind.CODE, "pkg/mod.py"): None,
            (QuestionLinkKind.CODE, "pkg/mod.py#pay"): None,
            (QuestionLinkKind.CODE, "pkg/mod.py#L1-L2"): None,
            (QuestionLinkKind.CODE, "pkg/mod.py#L1-L9"): "outside",
            (QuestionLinkKind.CODE, "pkg/mod.py#refund"): "not found",
            (QuestionLinkKind.CODE, "pkg/gone.py"): "file not found",
            (QuestionLinkKind.TASK, "OQ-001"): None,
            (QuestionLinkKind.TASK, "OQ-999"): "task not found",
            (QuestionLinkKind.DOCUMENT, "docs/billing"): None,
            (QuestionLinkKind.SECTION, "docs/billing#providers"): None,
            (QuestionLinkKind.SECTION, "docs/billing#nope"): "section #nope",
            (QuestionLinkKind.ADR, "ADR-001"): "adr not found",
            (QuestionLinkKind.URL, "https://example.com"): None,
        }
        for kind, ref in edges:
            question_service.link(
                session,
                project_id=pid,
                question_id=q.question_id,
                to_kind=kind,
                to_ref=ref,
                relation=QuestionRelation.ABOUT,
                author=AUTHOR,
            )
        report = question_service.verify_links(session, project_id=pid)
        stamped = {
            (lk.to_kind, lk.to_ref): (lk.resolved, lk.broken_reason, lk.last_checked)
            for lk in question_service.list_links(session, q.row_id)
        }
        broken = question_service.broken_links(session, project_id=pid)

    assert (report.checked, report.ok, report.broken, report.unchecked) == (13, 6, 6, 1)
    for key, expected in edges.items():
        resolved, reason, checked = stamped[key]
        assert checked is not None
        if key[0] is QuestionLinkKind.URL:
            assert resolved is None
        elif expected is None:
            assert resolved is True, (key, reason)
        else:
            assert resolved is False
            assert reason is not None
            assert expected in reason, (key, reason)
    assert len(broken) == report.broken


# --------------------------------------------------------------------------- #
# write path + invariants                                                      #
# --------------------------------------------------------------------------- #


def test_every_mutation_writes_revision_and_event(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid)
        qid = q.question_id
        common = {"project_id": pid, "question_id": qid, "author": AUTHOR}
        question_service.update(session, **common, title="T2")  # type: ignore[arg-type]
        question_service.add_option(session, **common, title="A")  # type: ignore[arg-type]
        question_service.update_option(session, **common, position=1, body="b")  # type: ignore[arg-type]
        question_service.link(
            session,
            **common,  # type: ignore[arg-type]
            to_kind=QuestionLinkKind.TASK,
            to_ref="AB-001",
            relation=QuestionRelation.ADDRESSED_BY,
        )
        question_service.unlink(
            session,
            **common,  # type: ignore[arg-type]
            to_kind=QuestionLinkKind.TASK,
            to_ref="AB-001",
            relation=QuestionRelation.ADDRESSED_BY,
        )
        question_service.remove_option(session, **common, position=1)  # type: ignore[arg-type]
        question_service.resolve(session, **common, resolution="да")  # type: ignore[arg-type]
        question_service.reopen(session, **common)  # type: ignore[arg-type]
        question_service.drop(session, **common, resolution="снят")  # type: ignore[arg-type]

        kinds = list(
            session.execute(
                select(ActivityEventModel.kind)
                .where(ActivityEventModel.scope_id == qid)
                .order_by(ActivityEventModel.row_id)
            ).scalars()
        )
        revisions = session.execute(
            select(func.count())
            .select_from(RevisionModel)
            .where(RevisionModel.entity_kind == EntityKind.QUESTION.value)
        ).scalar_one()
    assert kinds == [
        "question.created",
        "question.updated",
        "question.option_added",
        "question.option_updated",
        "question.linked",
        "question.unlinked",
        "question.option_removed",
        "question.resolved",
        "question.reopened",
        "question.dropped",
    ]
    assert revisions == len(kinds)


def test_questions_never_create_documents_or_sections(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        before = (
            session.execute(select(func.count()).select_from(DocumentModel)).scalar_one(),
            session.execute(select(func.count()).select_from(SectionModel)).scalar_one(),
        )
        q = _create(session, pid, context="## Контекст\n\nтекст", options=[("A", "b")])
        question_service.resolve(
            session, project_id=pid, question_id=q.question_id, author=AUTHOR, resolution="A"
        )
        after = (
            session.execute(select(func.count()).select_from(DocumentModel)).scalar_one(),
            session.execute(select(func.count()).select_from(SectionModel)).scalar_one(),
        )
    assert before == after


def test_search_indexes_open_questions_only(factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session)
        q = _create(session, pid, title="Провайдер эквайринга", options=[("Tinkoff", None)])

        def indexed() -> list[str]:
            return list(
                session.execute(
                    text("SELECT ref FROM db_search_idx WHERE project_id = :p AND kind='question'"),
                    {"p": pid},
                ).scalars()
            )

        assert indexed() == [q.question_id]
        hits = search_service.search(session, project_id=pid, query="Tinkoff", scope="question")
        assert [h["ref"] for h in hits["by_kind"]["question"]] == [q.question_id]
        question_service.resolve(
            session, project_id=pid, question_id=q.question_id, author=AUTHOR, resolution="T"
        )
        assert indexed() == []
        counts = search_service.reindex_all(session, pid)
        assert counts["question"] == 0
        question_service.reopen(session, project_id=pid, question_id=q.question_id, author=AUTHOR)
        assert search_service.reindex_all(session, pid)["question"] == 1
