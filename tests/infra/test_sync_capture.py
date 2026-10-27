"""CLO-006: хук захвата пишет ``sync_op`` по контракту RFC 29 §10.3.

Acceptance: «в sync_op попадают только authored-колонки (fields/base), FK
сериализованы как uid, строки restricted и local-колонки не пишутся,
suspended() глушит хук; тест на SCN-088».
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Priority,
    ScenarioKind,
    Sensitivity,
    TaskType,
)
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.models import (
    Base,
    DependencyModel,
    DocumentModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    ReplicaModel,
    ScenarioModel,
    SectionModel,
    SyncOpModel,
    TagModel,
    TaskModel,
    TaskTagModel,
)
from cod_doc.services import doc_service, scenario_service, task_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import sessionmaker

AUTHOR = "human:dakh"


# --- фикстуры и хелперы -------------------------------------------------------


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    url = f"sqlite:///{tmp_path / 'capture.db'}"
    run_alembic("upgrade", "head", db_url=url)
    engine = make_engine(url)
    yield make_session_factory(engine)
    engine.dispose()


def ops(session: Session, **where: Any) -> list[dict[str, Any]]:
    """Операции журнала по порядку HLC; ``fields``/``base`` разобраны."""
    stmt = select(SyncOpModel).order_by(SyncOpModel.hlc)
    for column, value in where.items():
        stmt = stmt.where(getattr(SyncOpModel, column) == value)
    out = []
    for row in session.scalars(stmt):
        out.append(
            {
                "op_id": row.op_id,
                "project_id": row.project_id,
                "replica_uid": row.replica_uid,
                "hlc": row.hlc,
                "entity_kind": row.entity_kind,
                "entity_uid": row.entity_uid,
                "kind": row.kind,
                "fields": json.loads(row.fields),
                "base": json.loads(row.base),
                "author": row.author,
                "changeset": row.changeset,
            }
        )
    return out


def seed_plan(session: Session, slug: str = "p") -> tuple[int, int, int]:
    """Проект, план и секция плана — ORM, как в tests/services/test_task_create.py."""
    cap.set_author(session, AUTHOR)
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def make_task(session: Session, ids: tuple[int, int, int], task_id: str = "PLN-001") -> TaskModel:
    p, pl, s = ids
    task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=task_id,
        title=f"Task {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=AUTHOR,
        description="old",
    )
    return session.scalars(
        select(TaskModel).where(TaskModel.project_id == p, TaskModel.task_id == task_id)
    ).one()


def make_doc(
    session: Session,
    project_id: int,
    doc_key: str,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> DocumentModel:
    doc = doc_service.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        title=doc_key,
        author=AUTHOR,
        path=f"docs/{doc_key}.md",
        sensitivity=sensitivity,
        reindex=False,
    )
    doc_service.add_section(
        session,
        project_id=project_id,
        document_id=doc.row_id,
        anchor="intro",
        heading="Intro",
        level=2,
        position=0,
        body="hello",
        author=AUTHOR,
        reindex=False,
    )
    return session.get_one(DocumentModel, doc.row_id)


def uid_of(session: Session, model: type[Any], row_id: int) -> str:
    return str(session.get_one(model, row_id).uid)


def authored(table: str) -> frozenset[str]:
    return reg.SHIPPED_COLUMNS[table] - {"uid"}


# --- статика -----------------------------------------------------------------


def test_doc_ref_targets_cover_registry() -> None:
    """Каждая ссылка на документ захватываемой таблицы знает, где брать метку."""
    expected = {
        (table, ref.column)
        for table, refs in reg.DOC_REFS.items()
        if table in cap.CAPTURED_TABLES
        for ref in refs
    }
    assert set(cap.DOC_REF_TARGETS) == expected


def test_every_captured_table_reaches_a_project() -> None:
    """Проект строки выводим: ``project_id`` или цепочка FK до таблицы с ним."""
    import cod_doc.infra.models.link_suggestions  # noqa: F401 — регистрирует link_suggestion

    unresolved = []
    for table in sorted(cap.CAPTURED_TABLES):
        seen = set()
        current: str | None = table
        while current is not None and current not in seen:
            seen.add(current)
            mapper = cap._mapper_for(current)
            assert mapper is not None, current
            info = cap.table_info(mapper)
            assert info is not None, current
            if current == "project" or "project_id" in Base.metadata.tables[current].c:
                break
            current = info.parent[1] if info.parent else None
        else:
            unresolved.append(table)
    assert unresolved == []


def test_captured_tables_are_authored_or_carry_authored_columns() -> None:
    assert "link_suggestion" in cap.CAPTURED_TABLES
    # Журнал (CLO-024): ревизии уезжают операциями на вставку, события — нет.
    assert "revision" in cap.CAPTURED_TABLES
    assert "activity_event" not in cap.CAPTURED_TABLES
    assert "sync_op" not in cap.CAPTURED_TABLES
    assert {s.table for s in reg.authored_tables()} <= cap.CAPTURED_TABLES


# --- установка ---------------------------------------------------------------


def test_install_is_idempotent_and_bare_session_is_not_captured(
    factory: sessionmaker[Session],
) -> None:
    cap.install(factory)
    cap.install(factory)
    with factory() as session:
        ids = seed_plan(session)
        session.commit()
        assert len(ops(session, entity_kind="project")) == 1, "один слушатель — одна операция"
    bind = factory.kw["bind"]
    with Session(bind) as bare:
        bare.add(ProjectModel(slug="bare", title="B", root_path="/tmp/b", config_json={}))
        bare.commit()
    with factory() as session:
        assert [o["fields"]["slug"] for o in ops(session, entity_kind="project")] == ["p"]
        assert ids[0]


# --- контракт ----------------------------------------------------------------


def test_new_row_gets_uid_and_upsert_with_fk_as_uid(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        task = make_task(session, ids)
        session.commit()

        assert task.uid, "хук назначил uid новой строке"
        [op] = ops(session, entity_kind="task")
        assert op["entity_uid"] == task.uid
        assert op["kind"] == "upsert"
        assert op["base"] == {}
        assert op["author"] == AUTHOR
        assert set(op["fields"]) <= authored("task")
        assert op["fields"]["task_id"] == "PLN-001"
        assert op["fields"]["project_id"] == uid_of(session, ProjectModel, ids[0])
        assert op["fields"]["plan_id"] == uid_of(session, PlanModel, ids[1])
        assert op["fields"]["section_id"] == uid_of(session, PlanSectionModel, ids[2])
        assert "row_id" not in op["fields"] and "uid" not in op["fields"]


def test_scn_088_service_update_writes_changed_authored_fields(
    factory: sessionmaker[Session],
) -> None:
    """SCN-088: мутация через сервис → операция с изменёнными полями и их прежними."""
    with factory() as session:
        ids = seed_plan(session)
        task = make_task(session, ids)
        doc = make_doc(session, ids[0], "spec-a")
        session.commit()
        before = {o["op_id"] for o in ops(session)}

        task_service.update_description(
            session, project_id=ids[0], task_id="PLN-001", new_description="new", author=AUTHOR
        )
        doc_service.patch_section(
            session,
            project_id=ids[0],
            document_id=doc.row_id,
            anchor="intro",
            new_body="hello, world",
            author=AUTHOR,
            reindex=False,
        )
        session.commit()

        fresh = [o for o in ops(session, changeset=None) if o["op_id"] not in before]
        by_kind = {o["entity_kind"]: o for o in fresh}
        task_op = by_kind["task"]
        assert task_op["entity_uid"] == task.uid
        assert task_op["fields"]["description"] == "new"
        assert task_op["base"]["description"] == "old"
        assert set(task_op["fields"]) == set(task_op["base"])
        assert set(task_op["fields"]) <= authored("task")

        section = session.scalars(
            select(SectionModel).where(SectionModel.document_id == doc.row_id)
        ).one()
        section_op = by_kind["section"]
        assert section_op["entity_uid"] == section.uid
        assert section_op["fields"]["body"] == "hello, world"
        assert section_op["base"]["body"] == "hello"
        assert set(section_op["fields"]) <= authored("section")
        # derived/local не попали: position, projection_hash, content_sha256_head
        assert "position" not in section_op["fields"]
        doc_ops = [o for o in fresh if o["entity_kind"] == "document"]
        for op in doc_ops:
            assert not {"projection_hash", "content_sha256_head"} & set(op["fields"])
        assert {o["author"] for o in fresh} == {AUTHOR}


def test_local_only_change_writes_nothing(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        doc = make_doc(session, ids[0], "spec-a")
        session.commit()
        count = len(ops(session))

        doc.projection_hash = "deadbeef"
        doc.content_sha256_head = "cafe"
        session.commit()
        assert len(ops(session)) == count, "только local-колонки — операции нет"


def test_restricted_rows_are_not_written(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        secret = make_doc(session, ids[0], "secret", Sensitivity.RESTRICTED)
        session.commit()
        kinds = {(o["entity_kind"], o["entity_uid"]) for o in ops(session)}
        assert ("document", secret.uid) not in kinds
        section = session.scalars(
            select(SectionModel).where(SectionModel.document_id == secret.row_id)
        ).one()
        assert ("section", section.uid) not in kinds

        doc_service.patch_section(
            session,
            project_id=ids[0],
            document_id=secret.row_id,
            anchor="intro",
            new_body="still secret",
            author=AUTHOR,
            reindex=False,
        )
        session.commit()
        assert not ops(session, entity_uid=section.uid)


def test_declassified_document_ships_whole_row(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        doc = make_doc(session, ids[0], "secret", Sensitivity.RESTRICTED)
        session.commit()

        doc.sensitivity = Sensitivity.INTERNAL.value
        session.commit()
        [op] = ops(session, entity_uid=doc.uid)
        assert op["base"] == {}, "первый upsert уехавшего документа — целиком"
        assert op["fields"]["doc_key"] == "secret"
        assert op["fields"]["sensitivity"] == "internal"


def test_raising_to_restricted_writes_nothing_and_warns(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    with factory() as session:
        ids = seed_plan(session)
        doc = make_doc(session, ids[0], "spec-a")
        session.commit()
        count = len(ops(session, entity_uid=doc.uid))

        doc.sensitivity = Sensitivity.RESTRICTED.value
        with caplog.at_level(logging.WARNING, logger="cod_doc.infra.sync_capture"):
            session.commit()
        assert len(ops(session, entity_uid=doc.uid)) == count
        assert "на remote осталась история" in caplog.text


def test_reference_to_restricted_document_is_withheld(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        make_doc(session, ids[0], "secret", Sensitivity.RESTRICTED)
        session.commit()
        scenario = scenario_service.create(
            session,
            project_id=ids[0],
            title="t",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=AUTHOR,
            scenario_id="SCN-001",
            group_key="g",
            doc_key="secret",
            section_anchor="intro",
            steps=["one"],
        )
        session.commit()
        model = session.scalars(
            select(ScenarioModel).where(ScenarioModel.row_id == scenario.row_id)
        ).one()
        [op] = ops(session, entity_uid=model.uid)
        withheld = {"document_id", "doc_key", "section_anchor", "doc_content_hash"}
        assert not withheld & set(op["fields"]), op["fields"]
        assert op["fields"]["title"] == "t"


def test_suspended_silences_the_hook(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        session.commit()
        count = len(ops(session))
        with cap.suspended(session):
            task = make_task(session, ids)
            task.title = "renamed"  # flush на выходе — тоже под флагом
            assert (
                cap.record(
                    session,
                    project_id=ids[0],
                    table="task",
                    entity_uid="0192f0c1-0000-7000-8000-00000000abcd",
                    kind="upsert",
                    fields={"title": "x"},
                    base={},
                    author=AUTHOR,
                )
                is None
            )
        session.commit()
        assert len(ops(session)) == count
        assert task.uid, "uid назначается и под suspended"

        task.title = "after"
        session.commit()
        [op] = ops(session, entity_uid=task.uid)
        assert op["fields"] == {"title": "after"}


def test_rollback_discards_and_savepoint_rollback_discards_inner_only(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        ids = seed_plan(session)
        session.commit()
        count = len(ops(session))

        make_task(session, ids, "PLN-001")
        session.rollback()
        assert len(ops(session)) == count
        assert cap.pending_ops(session) == []

        outer = make_task(session, ids, "PLN-002")
        savepoint = session.begin_nested()
        inner = make_task(session, ids, "PLN-003")
        inner_uid = inner.uid
        savepoint.rollback()
        session.commit()
        task_ops = ops(session, entity_kind="task")
        assert [o["entity_uid"] for o in task_ops] == [outer.uid]
        assert inner_uid not in {o["entity_uid"] for o in ops(session)}


def test_released_savepoint_keeps_ops(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        with session.begin_nested():
            task = make_task(session, ids)
        session.commit()
        assert len(ops(session, entity_uid=task.uid)) == 1


def test_delete_and_deterministic_edge_uid(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        a = make_task(session, ids, "PLN-001")
        b = make_task(session, ids, "PLN-002")
        task_service.add_dependency(
            session,
            project_id=ids[0],
            task_id="PLN-001",
            blocker_task_id="PLN-002",
            note="n",
            author=AUTHOR,
        )
        session.commit()
        dep = session.scalars(select(DependencyModel)).one()
        expected = reg.edge_uid(
            "dependency", {"from_task_id": a.uid, "to_task_id": b.uid, "kind": dep.kind}
        )
        assert dep.uid == expected
        [created] = ops(session, entity_kind="dependency")
        assert created["entity_uid"] == expected
        assert created["fields"]["from_task_id"] == a.uid

        task_service.remove_dependency(
            session, project_id=ids[0], task_id="PLN-001", blocker_task_id="PLN-002", author=AUTHOR
        )
        session.commit()
        deleted = [o for o in ops(session, entity_kind="dependency") if o["kind"] == "delete"]
        assert [o["entity_uid"] for o in deleted] == [expected]
        assert deleted[0]["fields"] == {}
        assert deleted[0]["base"]["to_task_id"] == b.uid


def test_delete_orphan_is_captured(factory: sessionmaker[Session]) -> None:
    """delete-orphan удаляет на flush — в ``session.deleted`` до flush его нет."""
    with factory() as session:
        ids = seed_plan(session)
        scenario_service.create(
            session,
            project_id=ids[0],
            title="t",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=AUTHOR,
            scenario_id="SCN-001",
            group_key="g",
            steps=["one", "two"],
        )
        session.commit()
        model = session.scalars(select(ScenarioModel)).one()
        step = model.steps[-1]
        step_uid = step.uid
        model.steps.remove(step)
        session.commit()
        [op] = [o for o in ops(session, entity_uid=step_uid) if o["kind"] == "delete"]
        assert op["entity_kind"] == "scenario_step"


def test_composite_tag_row_uses_edge_uid(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        task = make_task(session, ids)
        tag = TagModel(project_id=ids[0], name="infra")
        session.add(tag)
        session.flush()
        session.add(TaskTagModel(task_id=task.row_id, tag_id=tag.row_id))
        session.commit()
        expected = reg.edge_uid("task_tag", {"task_id": task.uid, "tag_id": tag.uid})
        [op] = ops(session, entity_kind="task_tag")
        assert op["entity_uid"] == expected
        assert op["fields"] == {"task_id": task.uid, "tag_id": tag.uid}


def test_missing_author_is_system_unknown(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    with factory() as session:
        session.add(ProjectModel(slug="x", title="X", root_path="/tmp/x", config_json={}))
        with caplog.at_level(logging.WARNING, logger="cod_doc.infra.sync_capture"):
            session.commit()
        [op] = ops(session, entity_kind="project")
        assert op["author"] == cap.UNKNOWN_AUTHOR
        assert "без автора" in caplog.text


def test_author_does_not_leak_into_next_transaction(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        seed_plan(session)
        session.commit()
        session.add(ProjectModel(slug="y", title="Y", root_path="/tmp/y", config_json={}))
        session.commit()
        authors = [o["author"] for o in ops(session, entity_kind="project")]
        assert authors == [AUTHOR, cap.UNKNOWN_AUTHOR]


def test_hlc_and_replica(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        make_task(session, ids, "PLN-001")
        session.commit()
        make_task(session, ids, "PLN-002")
        session.commit()
        journal = ops(session)
        replica = session.scalars(
            select(ReplicaModel).where(ReplicaModel.project_id == ids[0])
        ).one()
        assert replica.is_self
        assert {o["replica_uid"] for o in journal} == {replica.uid}
        stamps = [o["hlc"] for o in journal]
        assert len(set(stamps)) == len(stamps)
        assert all(s.endswith(f".{replica.uid}") for s in stamps)
        # порядок захвата сохраняется: проект раньше плана, план раньше секции
        kinds = [o["entity_kind"] for o in journal]
        assert kinds.index("project") < kinds.index("plan") < kinds.index("plan_section")
        assert all(o["changeset"] is None for o in journal)


def test_record_writes_explicit_op_and_drops_non_authored(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        ids = seed_plan(session)
        session.commit()
        op_id = cap.record(
            session,
            project_id=ids[0],
            table="finding",
            entity_uid="0192f0c1-0000-7000-8000-00000000f1d0",
            kind="upsert",
            fields={"title": "t", "payload": {"x": 1}, "miss_streak": 3},
            base={},
            author="agent:x",
        )
        session.commit()
        [op] = ops(session, op_id=op_id)
        assert op["fields"] == {"title": "t"}
        assert op["author"] == "agent:x"
        with pytest.raises(ValueError, match="не захватывается"):
            cap.record(
                session,
                project_id=ids[0],
                table="revision",
                entity_uid="x",
                kind="upsert",
                fields={},
                base={},
                author=AUTHOR,
            )


def test_unresolvable_fk_fails_the_transaction(factory: sessionmaker[Session]) -> None:
    """Конец без uid (вставлен мимо хука): операцию не построить — транзакция
    падает, а не коммитится без операции или с урезанной (fail-closed)."""
    with factory() as session:
        ids = seed_plan(session)
        session.commit()
        bind = factory.kw["bind"]
        with Session(bind) as bare:
            plan = bare.get_one(PlanModel, ids[1])
            plan.uid = None
            bare.commit()
        session.expire_all()
        with pytest.raises(cap.SyncCaptureError) as err:
            make_task(session, ids)
            session.commit()
        assert err.value.code == "SYNC-001"
        # Только таблица и колонка — ни row_id, ни значений.
        assert str(err.value) == "SYNC-001 task.plan_id: конец в plan без uid"
        session.rollback()
        assert session.scalars(select(TaskModel)).all() == [], "запись без операции не прошла"


def test_dangling_logical_ref_ships_as_null_not_row_id(factory: sessionmaker[Session]) -> None:
    """Логическая/полиморфная ссылка без строки-цели (FK в БД нет) — «ссылки
    нет»: уезжает ``null``, а не локальный row_id и не падение транзакции."""
    from cod_doc.infra.models import ExternalRefModel

    with factory() as session:
        ids = seed_plan(session)
        session.add(
            ExternalRefModel(
                project_id=ids[0],
                entity_kind="task",
                entity_row_id=999,
                system="github",
                external_id="1",
            )
        )
        session.commit()
        [op] = ops(session, entity_kind="external_ref")
        assert op["fields"]["entity_row_id"] is None


def test_flush_after_before_commit_is_still_journaled(factory: sessionmaker[Session]) -> None:
    """Правка из чужого before_commit (после нашего) уходит последним flush'ем
    commit'а — её операция тоже записана, а не выброшена с концом транзакции."""
    from sqlalchemy import event

    with factory() as session:
        ids = seed_plan(session)
        task = make_task(session, ids)
        session.commit()

        def late(sess: Session) -> None:
            if task.title != "late":
                task.title = "late"

        event.listen(factory, "before_commit", late)
        try:
            cap.set_author(session, AUTHOR)
            task.description = "x"
            session.commit()
        finally:
            event.remove(factory, "before_commit", late)
        titles = [o["fields"].get("title") for o in ops(session, entity_uid=task.uid)]
        assert "late" in titles


def test_ops_do_not_leak_into_next_transaction_after_close(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        ids = seed_plan(session)
        session.commit()
        make_task(session, ids, "PLN-001")
        session.flush()
        assert cap.pending_ops(session), "операции захвачены"
        session.close()
        assert cap.pending_ops(session) == []
        session.add(ProjectModel(slug="z", title="Z", root_path="/tmp/z", config_json={}))
        cap.set_author(session, AUTHOR)
        session.commit()
        assert not ops(session, entity_kind="task")


def test_document_restricted_later_in_same_transaction_ships_nothing(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    """Документ создан internal, затем в той же транзакции поднят до restricted:
    ни его тело, ни секции не уезжают, а лог не несёт значений."""
    with factory() as session:
        ids = seed_plan(session)
        session.commit()
        doc = make_doc(session, ids[0], "secret-later")
        section = session.scalars(
            select(SectionModel).where(SectionModel.document_id == doc.row_id)
        ).one()
        session.flush()
        doc.sensitivity = Sensitivity.RESTRICTED.value
        with caplog.at_level(logging.DEBUG, logger="cod_doc.infra.sync_capture"):
            session.commit()
        leaked = {o["entity_uid"] for o in ops(session)}
        assert doc.uid not in leaked
        assert section.uid not in leaked
        assert "hello" not in caplog.text and "secret-later" not in caplog.text


def test_scenario_withheld_after_ref_turns_restricted_in_same_transaction(
    factory: sessionmaker[Session],
) -> None:
    """Сущность со ссылкой на документ, ставший restricted позже в транзакции,
    уезжает без ссылки и связанных колонок (withheld пересчитан на commit)."""
    with factory() as session:
        ids = seed_plan(session)
        doc = make_doc(session, ids[0], "spec-x")
        session.commit()
        scenario_service.create(
            session,
            project_id=ids[0],
            title="t",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=AUTHOR,
            scenario_id="SCN-001",
            group_key="g",
            doc_key="spec-x",
            section_anchor="intro",
            steps=["one"],
        )
        session.flush()
        doc.sensitivity = Sensitivity.RESTRICTED.value
        session.commit()
        [op] = ops(session, entity_kind="scenario")
        withheld = {"document_id", "doc_key", "section_anchor", "doc_content_hash"}
        assert not withheld & set(op["fields"]), op["fields"]


def test_log_messages_name_columns_not_values(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    with factory() as session:
        ids = seed_plan(session)
        doc = make_doc(session, ids[0], "spec-a")
        session.commit()
        doc.sensitivity = Sensitivity.RESTRICTED.value
        with caplog.at_level(logging.DEBUG, logger="cod_doc.infra.sync_capture"):
            session.commit()
        assert "на remote осталась история" in caplog.text
        assert str(doc.uid) not in caplog.text
        assert "spec-a" not in caplog.text


def test_every_new_factory_gets_listeners_despite_id_reuse() -> None:
    """Фабрика на вызов ``db_for_entry``: класс сессии умирает, ``id`` достаётся
    следующему. ``event.contains`` ключуется ``id`` и отвечал «уже висит» про
    класс без слушателей — до правки так оставались 2981 из 3000 фабрик."""
    import gc

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker as make

    engine = create_engine("sqlite://")
    missing = 0
    for i in range(1000):
        factory = make(bind=engine)
        cap.install(factory)
        if cap._after_flush not in list(factory().dispatch.after_flush):
            missing += 1
        del factory
        if i % 50 == 0:
            gc.collect()
    engine.dispose()
    assert missing == 0


def test_new_ordered_rows_ship_rank_not_position(factory: sessionmaker[Session]) -> None:
    """CLO-003/004: ранг ставит before_flush ``infra/ordering``, захват собирает
    операции в after_flush — ранг уже в строке при любом порядке слушателей."""
    with factory() as session:
        ids = seed_plan(session)
        make_doc(session, ids[0], "spec-a")
        scenario_service.create(
            session,
            project_id=ids[0],
            title="t",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=AUTHOR,
            scenario_id="SCN-001",
            group_key="g",
            steps=["one"],
        )
        session.commit()
        for table in ("plan", "section", "plan_section", "scenario_step"):
            [op] = ops(session, entity_kind=table)
            assert op["fields"].get("rank"), (table, op["fields"])
            assert "position" not in op["fields"], table


def test_move_ships_only_rank(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        ids = seed_plan(session)
        doc = make_doc(session, ids[0], "spec-a")
        doc_service.add_section(
            session,
            project_id=ids[0],
            document_id=doc.row_id,
            anchor="two",
            heading="Two",
            level=2,
            position=1,
            body="b",
            author=AUTHOR,
            reindex=False,
        )
        session.commit()
        two = session.scalars(select(SectionModel).where(SectionModel.anchor == "two")).one()
        old_rank = two.rank
        doc_service.move_section(
            session,
            project_id=ids[0],
            document_id=doc.row_id,
            anchor="two",
            position=0,
            author=AUTHOR,
        )
        session.commit()
        moved = [o for o in ops(session, entity_uid=two.uid) if o["base"]]
        assert moved[-1]["fields"] == {"rank": two.rank}
        assert moved[-1]["base"] == {"rank": old_rank}
