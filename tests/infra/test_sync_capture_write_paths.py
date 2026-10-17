"""CLO-009: каждая authored-мутация пишет ``sync_op`` (RFC 29 §3.4; SCN-088).

Acceptance: «для каждой write-функции из спеков паритета (task, story, doc,
plan, adr_topic, sync) тест создаёт минимум одну операцию с верным
``entity_uid``; тест на SCN-088».

Перечень write-функций не ведётся руками: он собирается тем же детектором,
что и гейты паритета (``tests/services/_surface_parity.py::
discovered_mutations``), по всем спекам ``test_*_mutation_surface_parity.py``.
Новая мутация без случая здесь роняет ``test_every_parity_mutation_has_a_case``
— как роняет гейт паритета мутация без поверхности. Спека ``sync_service``
появится с CLO-016 и подхватится сама.

Спеки паритета покрывают не все authored-таблицы (сценарии, комментарии,
рутины, находки, решения по подсказкам, теги, модули…). Для них — отдельные
случаи по сервисам, а у таблиц без единого пути записи в коде — прямая
ORM-запись: так проверяется сам хук, а таблица числится в
``NO_WRITE_PATH``. ``test_every_captured_table_has_a_case`` требует случай
на каждую захватываемую таблицу.

Три пути, писавшие bulk-запросом мимо хука (RFC §3.4), переписаны в CLO-007:
шаги сценария и решение по подсказке — ORM, upsert находок — SQL с
``sync_capture.record`` рядом. Новый такой путь не пропустит AST-гейт
``test_sync_capture_no_bulk_authored.py`` (CLO-008).
"""

from __future__ import annotations

import importlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Priority,
    QuestionLinkKind,
    QuestionRelation,
    ScenarioKind,
    ScenarioLinkKind,
    ScenarioRelation,
    Sensitivity,
    StoryLinkKind,
    StoryRelation,
    TaskStatus,
    TaskType,
    UserStoryStatus,
)
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.models import (
    ADRDiagramModel,
    ADRModel,
    ADRRelationModel,
    ADRSupersedeModel,
    ADRTaskModel,
    ADRTopicModel,
    AffectedFileModel,
    ApprovalDocRevisionLinkModel,
    ApprovalModel,
    ApprovalTaskLinkModel,
    CommitLinkModel,
    DependencyModel,
    DocCommentModel,
    DocNodeModel,
    DocumentModel,
    DocumentTagModel,
    ExternalRefModel,
    FindingModel,
    ModuleCodeModel,
    ModuleDependencyModel,
    ModuleModel,
    OpenQuestionModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    QuestionLinkModel,
    QuestionOptionModel,
    RevisionModel,
    RoutineModel,
    ScenarioLinkModel,
    ScenarioModel,
    ScenarioStepModel,
    SectionModel,
    StoryAcceptanceModel,
    StoryLinkModel,
    StorySectionModel,
    StoryTagModel,
    StructureWaiverModel,
    SyncOpModel,
    TagModel,
    TaskDocumentModel,
    TaskModel,
    TaskTagModel,
    UserStoryModel,
)
from cod_doc.infra.models.link_suggestions import LinkSuggestionModel
from cod_doc.services import (
    adr_service,
    adr_topic_service,
    approval_service,
    comment_service,
    doc_node_health,
    doc_node_intent,
    doc_service,
    doc_tree_service,
    finding_service,
    plan_service,
    question_service,
    routine_service,
    scenario_service,
    story_service,
    structure_drift,
    task_doc_service,
    task_service,
)
from cod_doc.services.finding_service.dedup import FindingSeed
from cod_doc.services.link_service import semantic
from tests._alembic import run_alembic
from tests.services._surface_parity import ServiceSpec, discovered_mutations

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session

AUTHOR = "human:dakh"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Ожидание случая: (таблица, uid сущности). uid — функция: у созданной
#: строки он известен только после commit.
Expect = list[tuple[str, "Callable[[], str]"]]


# --- мир: одна засеянная БД, копия на случай -------------------------------------


@dataclass
class World:
    session: Session
    pid: int
    plan_id: int
    sec_a: int
    sec_b: int
    doc_id: int
    root: Path
    db_url: str

    def commit(self) -> None:
        """Commit подготовки случая — от того же автора, что и мутация."""
        cap.set_author(self.session, AUTHOR)
        self.session.commit()

    def uid(self, model: type[Any], **where: Any) -> str:
        stmt = select(model.uid)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return str(self.session.execute(stmt).scalar_one())

    def later(self, model: type[Any], **where: Any) -> Callable[[], str]:
        return lambda: self.uid(model, **where)

    def now(self, model: type[Any], **where: Any) -> Callable[[], str]:
        value = self.uid(model, **where)
        return lambda: value

    def task(self, task_id: str) -> TaskModel:
        return self.session.scalars(
            select(TaskModel).where(TaskModel.project_id == self.pid, TaskModel.task_id == task_id)
        ).one()


def _seed(session: Session, root: Path) -> dict[str, int]:
    cap.set_author(session, AUTHOR)
    now = datetime.now(UTC)
    proj = ProjectModel(slug="w", title="W", root_path=str(root), config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    pid = proj.row_id
    plan_service.create_plan(
        session, project_id=pid, scope="w-plan", principle=None, author=AUTHOR, id_prefix="PLN"
    )
    for letter in ("A", "B"):
        plan_service.create_section(
            session, project_id=pid, plan_scope="w-plan", letter=letter, title=letter, author=AUTHOR
        )
    plan = session.scalars(select(PlanModel).where(PlanModel.project_id == pid)).one()
    secs = {
        s.letter: s.row_id
        for s in session.scalars(
            select(PlanSectionModel).where(PlanSectionModel.plan_id == plan.row_id)
        )
    }
    for task_id in ("PLN-001", "PLN-002", "PLN-003"):
        task_service.create(
            session,
            project_id=pid,
            plan_id=plan.row_id,
            section_id=secs["A"],
            task_id=task_id,
            title=f"Task {task_id}",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author=AUTHOR,
            description="old",
        )
    doc = doc_service.create(
        session,
        project_id=pid,
        doc_key="spec-a",
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        title="Spec A",
        author=AUTHOR,
        path="docs/spec-a.md",
        sensitivity=Sensitivity.INTERNAL,
        reindex=False,
    )
    for pos, anchor in enumerate(("intro", "two")):
        doc_service.add_section(
            session,
            project_id=pid,
            document_id=doc.row_id,
            anchor=anchor,
            heading=anchor.title(),
            level=2,
            position=pos,
            body=f"body of {anchor}",
            author=AUTHOR,
            reindex=False,
        )
    doc_tree_service.init_tree(session, project_id=pid, author=AUTHOR)
    story_service.create_section(session, project_id=pid, key="core", title="Core", author=AUTHOR)
    story_service.create(
        session,
        project_id=pid,
        story_id="US-001",
        persona="dev",
        narrative="I want sync",
        priority=Priority.MEDIUM,
        author=AUTHOR,
        acceptance=["first"],
    )
    for adr_id in ("ADR-001", "ADR-002"):
        adr_service.create(
            session, project_id=pid, title=f"Decision {adr_id}", author=AUTHOR, adr_id=adr_id
        )
    adr_topic_service.create(session, project_id=pid, name="infra", author=AUTHOR)
    adr_topic_service.create(session, project_id=pid, name="data", author=AUTHOR)
    question_service.create(
        session,
        project_id=pid,
        title="Which?",
        question="Which one?",
        author=AUTHOR,
        options=(("A", None), ("B", None)),
        question_id="Q-001",
    )
    scenario_service.create(
        session,
        project_id=pid,
        title="Scn",
        kind=ScenarioKind.INVARIANT,
        preconditions="p",
        expected="e",
        author=AUTHOR,
        scenario_id="SCN-001",
        group_key="g",
        steps=["one", "two"],
    )
    return {
        "pid": pid,
        "plan_id": plan.row_id,
        "sec_a": secs["A"],
        "sec_b": secs["B"],
        "doc_id": doc.row_id,
    }


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, int]]:
    base = tmp_path_factory.mktemp("sync-world")
    db = base / "world.db"
    url = f"sqlite:///{db}"
    run_alembic("upgrade", "head", db_url=url)
    engine = make_engine(url)
    with make_session_factory(engine)() as session:
        ids = _seed(session, base)
        session.commit()
    engine.dispose()
    return db, ids


@pytest.fixture
def world(template: tuple[Path, dict[str, int]], tmp_path: Path) -> Iterator[World]:
    src, ids = template
    db = tmp_path / "world.db"
    shutil.copyfile(src, db)
    engine = make_engine(f"sqlite:///{db}")
    with make_session_factory(engine)() as session:
        yield World(session=session, root=tmp_path, db_url=f"sqlite:///{db}", **ids)
    engine.dispose()


def _fresh_ops(session: Session, before: set[str]) -> list[SyncOpModel]:
    rows = session.scalars(select(SyncOpModel).order_by(SyncOpModel.hlc)).all()
    return [r for r in rows if r.op_id not in before]


# --- случаи ------------------------------------------------------------------------

CASES: dict[str, Callable[[World], Expect]] = {}
#: Случаи, у которых правильный ответ — «операций над этими таблицами нет».
NO_OPS: dict[str, tuple[frozenset[str], str]] = {}


def case(name: str) -> Callable[[Callable[[World], Expect]], Callable[[World], Expect]]:
    def register(fn: Callable[[World], Expect]) -> Callable[[World], Expect]:
        assert name not in CASES, name
        CASES[name] = fn
        return fn

    return register


# task_service ------------------------------------------------------------------------


@case("task_service.create")
def _(w: World) -> Expect:
    task_service.create(
        w.session,
        project_id=w.pid,
        plan_id=w.plan_id,
        section_id=w.sec_a,
        task_id="PLN-010",
        title="New",
        type=TaskType.FEATURE,
        priority=Priority.LOW,
        author=AUTHOR,
        affected_files=["cod_doc/x.py"],
    )
    return [
        ("task", w.later(TaskModel, task_id="PLN-010")),
        ("affected_file", w.later(AffectedFileModel, path="cod_doc/x.py")),
    ]


def _task_field(fn_name: str, **kw: Any) -> Callable[[World], Expect]:
    def run(w: World) -> Expect:
        getattr(task_service, fn_name)(
            w.session, project_id=w.pid, task_id="PLN-001", author=AUTHOR, **kw
        )
        return [("task", w.now(TaskModel, task_id="PLN-001"))]

    return run


case("task_service.update_description")(_task_field("update_description", new_description="new"))
case("task_service.update_acceptance")(_task_field("update_acceptance", new_acceptance="done"))
case("task_service.update_priority")(_task_field("update_priority", new_priority=Priority.HIGH))
case("task_service.update_status")(
    _task_field("update_status", new_status=TaskStatus.BLOCKED, reason="waiting")
)
case("task_service.complete")(_task_field("complete", commit_sha="abc1234"))
case("task_service.set_blocker")(_task_field("set_blocker", reason="waiting on infra"))
case("task_service.log_progress")(_task_field("log_progress", message="half way"))


@case("task_service.clear_blocker")
def _(w: World) -> Expect:
    task_service.set_blocker(
        w.session, project_id=w.pid, task_id="PLN-001", reason="r", author=AUTHOR
    )
    w.commit()
    task_service.clear_blocker(w.session, project_id=w.pid, task_id="PLN-001", author=AUTHOR)
    return [("task", w.now(TaskModel, task_id="PLN-001"))]


@case("task_service.move_to_section")
def _(w: World) -> Expect:
    task_service.move_to_section(
        w.session, project_id=w.pid, task_id="PLN-001", new_section_id=w.sec_b, author=AUTHOR
    )
    return [("task", w.now(TaskModel, task_id="PLN-001"))]


@case("task_service.move_tasks_to_plan")
def _(w: World) -> Expect:
    plan_service.create_plan(
        w.session, project_id=w.pid, scope="other", principle=None, author=AUTHOR, id_prefix="OTH"
    )
    plan_service.create_section(
        w.session, project_id=w.pid, plan_scope="other", letter="A", title="A", author=AUTHOR
    )
    task_service.move_tasks_to_plan(
        w.session,
        project_id=w.pid,
        plan_scope="other",
        section_letter="A",
        author=AUTHOR,
        task_ids=["PLN-002"],
    )
    uid = w.now(TaskModel, task_id="PLN-002")
    return [("task", uid)]


@case("task_service.add_dependency")
def _(w: World) -> Expect:
    task_service.add_dependency(
        w.session,
        project_id=w.pid,
        task_id="PLN-001",
        blocker_task_id="PLN-002",
        note="n",
        author=AUTHOR,
    )
    return [("dependency", w.later(DependencyModel, from_task_id=w.task("PLN-001").row_id))]


@case("task_service.remove_dependency")
def _(w: World) -> Expect:
    task_service.add_dependency(
        w.session,
        project_id=w.pid,
        task_id="PLN-001",
        blocker_task_id="PLN-002",
        note="n",
        author=AUTHOR,
    )
    w.commit()
    dep = w.now(DependencyModel, from_task_id=w.task("PLN-001").row_id)
    task_service.remove_dependency(
        w.session, project_id=w.pid, task_id="PLN-001", blocker_task_id="PLN-002", author=AUTHOR
    )
    return [("dependency", dep)]


@case("task_service.update_affects_files")
def _(w: World) -> Expect:
    task_service.update_affects_files(
        w.session, project_id=w.pid, task_id="PLN-001", paths=["a.py", "b.py"], author=AUTHOR
    )
    return [("affected_file", w.later(AffectedFileModel, path="b.py"))]


# story_service ------------------------------------------------------------------------


@case("story_service.create")
def _(w: World) -> Expect:
    story_service.create(
        w.session,
        project_id=w.pid,
        story_id="US-002",
        persona="ops",
        narrative="n",
        priority=Priority.LOW,
        author=AUTHOR,
        acceptance=["works"],
        section_key="core",
    )
    return [
        ("user_story", w.later(UserStoryModel, story_id="US-002")),
        ("story_acceptance", w.later(StoryAcceptanceModel, criterion="works")),
    ]


@case("story_service.update_status")
def _(w: World) -> Expect:
    story_service.update_status(
        w.session,
        project_id=w.pid,
        story_id="US-001",
        new_status=UserStoryStatus.ACCEPTED,
        author=AUTHOR,
    )
    return [("user_story", w.now(UserStoryModel, story_id="US-001"))]


@case("story_service.create_section")
def _(w: World) -> Expect:
    story_service.create_section(
        w.session, project_id=w.pid, key="extra", title="Extra", author=AUTHOR
    )
    return [("story_section", w.later(StorySectionModel, key="extra"))]


@case("story_service.assign_section")
def _(w: World) -> Expect:
    story_service.assign_section(
        w.session, project_id=w.pid, story_id="US-001", key="core", author=AUTHOR
    )
    return [("user_story", w.now(UserStoryModel, story_id="US-001"))]


@case("story_service.add_criterion")
def _(w: World) -> Expect:
    story_service.add_criterion(
        w.session, project_id=w.pid, story_id="US-001", criterion="second", author=AUTHOR
    )
    return [("story_acceptance", w.later(StoryAcceptanceModel, criterion="second"))]


@case("story_service.set_criterion_met")
def _(w: World) -> Expect:
    story_service.set_criterion_met(
        w.session, project_id=w.pid, story_id="US-001", position=0, met=True, author=AUTHOR
    )
    return [("story_acceptance", w.now(StoryAcceptanceModel, criterion="first"))]


@case("story_service.link")
def _(w: World) -> Expect:
    story_service.link(
        w.session,
        project_id=w.pid,
        story_id="US-001",
        to_kind=StoryLinkKind.TASK,
        to_ref="PLN-001",
        relation=StoryRelation.IMPLEMENTED_BY,
        author=AUTHOR,
    )
    return [("story_link", w.later(StoryLinkModel, to_ref="PLN-001"))]


# doc_service --------------------------------------------------------------------------


@case("doc_service.create")
def _(w: World) -> Expect:
    doc_service.create(
        w.session,
        project_id=w.pid,
        doc_key="spec-b",
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        title="B",
        author=AUTHOR,
        path="docs/spec-b.md",
        reindex=False,
    )
    return [("document", w.later(DocumentModel, doc_key="spec-b"))]


@case("doc_service.add_section")
def _(w: World) -> Expect:
    doc_service.add_section(
        w.session,
        project_id=w.pid,
        document_id=w.doc_id,
        anchor="three",
        heading="Three",
        level=2,
        position=2,
        body="b",
        author=AUTHOR,
        reindex=False,
    )
    return [("section", w.later(SectionModel, anchor="three"))]


@case("doc_service.patch_section")
def _(w: World) -> Expect:
    doc_service.patch_section(
        w.session,
        project_id=w.pid,
        document_id=w.doc_id,
        anchor="intro",
        new_body="patched",
        author=AUTHOR,
        reindex=False,
    )
    return [("section", w.now(SectionModel, anchor="intro"))]


@case("doc_service.delete_section")
def _(w: World) -> Expect:
    uid = w.now(SectionModel, anchor="two")
    doc_service.delete_section(
        w.session,
        project_id=w.pid,
        document_id=w.doc_id,
        anchor="two",
        author=AUTHOR,
        reindex=False,
    )
    return [("section", uid)]


@case("doc_service.move_section")
def _(w: World) -> Expect:
    doc_service.move_section(
        w.session, project_id=w.pid, document_id=w.doc_id, anchor="two", position=0, author=AUTHOR
    )
    return [("section", w.now(SectionModel, anchor="two"))]


@case("doc_service.rename_section")
def _(w: World) -> Expect:
    uid = w.now(SectionModel, anchor="two")
    doc_service.rename_section(
        w.session,
        project_id=w.pid,
        document_id=w.doc_id,
        anchor="two",
        heading="Second",
        new_anchor="second",
        author=AUTHOR,
    )
    return [("section", uid)]


@case("doc_service.update_status")
def _(w: World) -> Expect:
    doc_service.update_status(
        w.session,
        project_id=w.pid,
        document_id=w.doc_id,
        new_status=DocumentStatus.ACTIVE,
        author=AUTHOR,
    )
    return [("document", w.now(DocumentModel, doc_key="spec-a"))]


@case("doc_service.update_metadata")
def _(w: World) -> Expect:
    doc_service.update_metadata(
        w.session,
        project_id=w.pid,
        document_id=w.doc_id,
        author=AUTHOR,
        title="Spec A, renamed",
        reindex=False,
    )
    return [("document", w.now(DocumentModel, doc_key="spec-a"))]


@case("doc_service.accept")
def _(w: World) -> Expect:
    doc_service.accept(w.session, project_id=w.pid, document_id=w.doc_id, author=AUTHOR)
    return [("document", w.now(DocumentModel, doc_key="spec-a"))]


@case("doc_service.rename")
def _(w: World) -> Expect:
    uid = w.now(DocumentModel, doc_key="spec-a")
    doc_service.rename(
        w.session,
        project_id=w.pid,
        document_id=w.doc_id,
        new_doc_key="spec-renamed",
        new_path="docs/spec-renamed.md",
        author=AUTHOR,
    )
    return [("document", uid)]


@case("doc_service.delete")
def _(w: World) -> Expect:
    uid = w.now(DocumentModel, doc_key="spec-a")
    doc_service.delete(w.session, project_id=w.pid, doc_key="spec-a", author=AUTHOR)
    return [("document", uid)]


# doc_tree_service ---------------------------------------------------------------------


@case("doc_tree_service.create_node")
def _(w: World) -> Expect:
    doc_tree_service.create_node(
        w.session, project_id=w.pid, node_key="extra", title="Extra", author=AUTHOR
    )
    return [("doc_node", w.later(DocNodeModel, node_key="extra"))]


@case("doc_tree_service.update_node")
def _(w: World) -> Expect:
    doc_tree_service.update_node(
        w.session, project_id=w.pid, node_key="vision", title="Vision!", author=AUTHOR
    )
    return [("doc_node", w.now(DocNodeModel, node_key="vision"))]


@case("doc_tree_service.delete_node")
def _(w: World) -> Expect:
    uid = w.now(DocNodeModel, node_key="skills")
    doc_tree_service.delete_node(w.session, project_id=w.pid, node_key="skills", author=AUTHOR)
    return [("doc_node", uid)]


@case("doc_tree_service.init_tree")
def _(w: World) -> Expect:
    for node in w.session.scalars(select(DocNodeModel).where(DocNodeModel.project_id == w.pid)):
        w.session.delete(node)
    w.commit()
    doc_tree_service.init_tree(w.session, project_id=w.pid, author=AUTHOR)
    return [("doc_node", w.later(DocNodeModel, node_key="vision"))]


@case("doc_tree_service.assign")
def _(w: World) -> Expect:
    doc_tree_service.assign(
        w.session, project_id=w.pid, doc_key="spec-a", node_key="data-model", author=AUTHOR
    )
    return [("document", w.now(DocumentModel, doc_key="spec-a"))]


@case("doc_tree_service.classify_project")
def _(w: World) -> Expect:
    doc_service.create(
        w.session,
        project_id=w.pid,
        doc_key="docs/architecture",
        type=DocumentType.ARCHITECTURE,
        status=DocumentStatus.DRAFT,
        title="Arch",
        author=AUTHOR,
        path="docs/architecture.md",
        reindex=False,
    )
    w.commit()
    doc_tree_service.classify_project(w.session, project_id=w.pid, author=AUTHOR, dry_run=False)
    return [("document", w.now(DocumentModel, doc_key="docs/architecture"))]


# doc_node_health / doc_node_intent: пишут только routine-находки (derived) -----------


@case("doc_node_health.sync")
def _(w: World) -> Expect:
    doc_node_health.sync(w.session, project_id=w.pid, project_slug="w")
    assert w.session.scalars(select(FindingModel)).first() is not None, "находки записаны"
    return []


NO_OPS["doc_node_health.sync"] = (
    frozenset({"finding"}),
    "пишет только находки source=routine — derived (RFC §3.2), реплика пересчитывает их сама",
)


@case("doc_node_intent.analyze")
def _(w: World) -> Expect:
    verdicts = [
        {"node_key": "vision", "covered": False, "gap": "нет видения"},
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(doc_node_intent, "_call_lite_raw", lambda *a, **k: json.dumps(verdicts))
        mp.setattr(doc_node_intent, "parse_verdicts", lambda raw: _intent_verdicts(raw))
        doc_node_intent.analyze(w.session, project_id=w.pid, cfg=object())  # type: ignore[arg-type]
    return []


def _intent_verdicts(raw: str) -> Any:
    return (
        doc_node_intent.parse_verdicts.__wrapped__(raw)
        if hasattr(doc_node_intent.parse_verdicts, "__wrapped__")
        else _ORIG_PARSE(raw)
    )


_ORIG_PARSE = doc_node_intent.parse_verdicts

NO_OPS["doc_node_intent.analyze"] = (
    frozenset({"finding"}),
    "пишет только находки source=routine — derived (RFC §3.2), реплика пересчитывает их сама",
)


# plan_service -------------------------------------------------------------------------


@case("plan_service.create_plan")
def _(w: World) -> Expect:
    plan_service.create_plan(
        w.session, project_id=w.pid, scope="p2", principle="x", author=AUTHOR, id_prefix="PTW"
    )
    return [("plan", w.later(PlanModel, scope="p2"))]


@case("plan_service.create_section")
def _(w: World) -> Expect:
    plan_service.create_section(
        w.session, project_id=w.pid, plan_scope="w-plan", letter="C", title="C", author=AUTHOR
    )
    return [("plan_section", w.later(PlanSectionModel, letter="C"))]


@case("plan_service.update_section")
def _(w: World) -> Expect:
    plan_service.update_section(
        w.session,
        project_id=w.pid,
        plan_scope="w-plan",
        letter="B",
        title="Bee",
        author=AUTHOR,
        reason="rename",
    )
    return [("plan_section", w.now(PlanSectionModel, letter="B"))]


@case("plan_service.move_section")
def _(w: World) -> Expect:
    plan_service.move_section(
        w.session,
        project_id=w.pid,
        plan_scope="w-plan",
        letter="B",
        before="A",
        author=AUTHOR,
        reason="reorder",
    )
    return [("plan_section", w.now(PlanSectionModel, letter="B"))]


@case("plan_service.delete_section")
def _(w: World) -> Expect:
    uid = w.now(PlanSectionModel, letter="B")
    plan_service.delete_section(
        w.session, project_id=w.pid, plan_scope="w-plan", letter="B", author=AUTHOR, reason="gone"
    )
    return [("plan_section", uid)]


# adr_service --------------------------------------------------------------------------


@case("adr_service.create")
def _(w: World) -> Expect:
    adr_service.create(w.session, project_id=w.pid, title="Third", author=AUTHOR, adr_id="ADR-003")
    return [("adr", w.later(ADRModel, adr_id="ADR-003"))]


def _adr(fn_name: str, **kw: Any) -> Callable[[World], Expect]:
    def run(w: World) -> Expect:
        getattr(adr_service, fn_name)(
            w.session, project_id=w.pid, adr_id="ADR-001", author=AUTHOR, **kw
        )
        return [("adr", w.now(ADRModel, adr_id="ADR-001"))]

    return run


case("adr_service.update")(_adr("update", context="ctx"))
case("adr_service.sync_body")(_adr("sync_body", decision="we do it"))
case("adr_service.deprecate")(_adr("deprecate", reason="old"))
case("adr_service.set_topic")(_adr("set_topic", topic="infra"))


@case("adr_service.add_diagram")
def _(w: World) -> Expect:
    adr_service.add_diagram(
        w.session, project_id=w.pid, adr_id="ADR-001", mermaid="graph TD; A-->B", author=AUTHOR
    )
    return [("adr_diagram", w.later(ADRDiagramModel, mermaid="graph TD; A-->B"))]


@case("adr_service.supersede")
def _(w: World) -> Expect:
    adr_service.supersede(
        w.session,
        project_id=w.pid,
        superseding_adr_id="ADR-002",
        superseded_adr_id="ADR-001",
        author=AUTHOR,
    )
    return [
        ("adr_supersedes", lambda: w.uid(ADRSupersedeModel)),
        ("adr", w.now(ADRModel, adr_id="ADR-001")),
    ]


@case("adr_service.link_task")
def _(w: World) -> Expect:
    adr_service.link_task(
        w.session, project_id=w.pid, adr_id="ADR-001", task_id="PLN-001", author=AUTHOR
    )
    return [("adr_task", w.later(ADRTaskModel, task_id="PLN-001"))]


@case("adr_service.relate")
def _(w: World) -> Expect:
    adr_service.relate(
        w.session,
        project_id=w.pid,
        from_adr_id="ADR-002",
        to_adr_id="ADR-001",
        kind="amends",
        author=AUTHOR,
    )
    return [("adr_relation", lambda: w.uid(ADRRelationModel))]


@case("adr_service.unrelate")
def _(w: World) -> Expect:
    adr_service.relate(
        w.session,
        project_id=w.pid,
        from_adr_id="ADR-002",
        to_adr_id="ADR-001",
        kind="amends",
        author=AUTHOR,
    )
    w.commit()
    uid = w.now(ADRRelationModel)
    adr_service.unrelate(
        w.session,
        project_id=w.pid,
        from_adr_id="ADR-002",
        to_adr_id="ADR-001",
        kind="amends",
        author=AUTHOR,
    )
    return [("adr_relation", uid)]


# adr_topic_service --------------------------------------------------------------------


@case("adr_topic_service.create")
def _(w: World) -> Expect:
    adr_topic_service.create(w.session, project_id=w.pid, name="ops", author=AUTHOR)
    return [("adr_topic", w.later(ADRTopicModel, name="ops"))]


@case("adr_topic_service.update")
def _(w: World) -> Expect:
    adr_topic_service.update(
        w.session, project_id=w.pid, name="infra", includes="servers", author=AUTHOR
    )
    return [("adr_topic", w.now(ADRTopicModel, name="infra"))]


@case("adr_topic_service.move")
def _(w: World) -> Expect:
    adr_topic_service.move(w.session, project_id=w.pid, name="data", position=0, author=AUTHOR)
    return [("adr_topic", w.now(ADRTopicModel, name="data"))]


@case("adr_topic_service.delete")
def _(w: World) -> Expect:
    uid = w.now(ADRTopicModel, name="data")
    adr_topic_service.delete(w.session, project_id=w.pid, name="data", author=AUTHOR)
    return [("adr_topic", uid)]


# approval_service ---------------------------------------------------------------------


def _request(w: World) -> str:
    rev = w.session.scalars(
        select(RevisionModel.revision_id)
        .where(RevisionModel.entity_kind == "document", RevisionModel.entity_id == w.doc_id)
        .limit(1)
    ).one()
    approval = approval_service.request(
        w.session,
        w.pid,
        approval_type="manual",
        requested_by=AUTHOR,
        linked_task_refs=["PLN-001"],
        linked_doc_revision_ids=[rev],
    )
    return approval.approval_id


@case("approval_service.request")
def _(w: World) -> Expect:
    approval_id = _request(w)
    return [
        ("approval", w.later(ApprovalModel, approval_id=approval_id)),
        ("approval_task_link", w.later(ApprovalTaskLinkModel, task_ref="PLN-001")),
        ("approval_doc_revision_link", lambda: w.uid(ApprovalDocRevisionLinkModel)),
    ]


@case("approval_service.request_doc_patch")
def _(w: World) -> Expect:
    approval_service.request_doc_patch(
        w.session,
        w.pid,
        op="link_retarget",
        args={"doc_key": "spec-a", "anchor": "intro", "old": "a.md", "new": "b.md"},
        diff="-a\n+b\n",
        rationale="moved",
    )
    return [("approval", lambda: w.uid(ApprovalModel))]


@case("approval_service.resolve")
def _(w: World) -> Expect:
    approval_id = _request(w)
    w.commit()
    approval_service.resolve(w.session, w.pid, approval_id, decision="deny", resolved_by=AUTHOR)
    return [("approval", w.now(ApprovalModel, approval_id=approval_id))]


@case("approval_service.cancel")
def _(w: World) -> Expect:
    approval_id = _request(w)
    w.commit()
    approval_service.cancel(w.session, w.pid, approval_id, reason="nope", cancelled_by=AUTHOR)
    return [("approval", w.now(ApprovalModel, approval_id=approval_id))]


# question_service ---------------------------------------------------------------------


@case("question_service.create")
def _(w: World) -> Expect:
    question_service.create(
        w.session,
        project_id=w.pid,
        title="New?",
        question="New one?",
        author=AUTHOR,
        options=(("X", "x"),),
        question_id="Q-002",
    )
    return [
        ("open_question", w.later(OpenQuestionModel, question_id="Q-002")),
        ("open_question_option", w.later(QuestionOptionModel, title="X")),
    ]


def _q(fn_name: str, **kw: Any) -> Callable[[World], Expect]:
    def run(w: World) -> Expect:
        getattr(question_service, fn_name)(
            w.session, project_id=w.pid, question_id="Q-001", author=AUTHOR, **kw
        )
        return [("open_question", w.now(OpenQuestionModel, question_id="Q-001"))]

    return run


case("question_service.update")(_q("update", context="ctx"))
case("question_service.resolve")(_q("resolve", resolution="A it is", chosen_option=1))
case("question_service.drop")(_q("drop", resolution="moot"))


@case("question_service.reopen")
def _(w: World) -> Expect:
    question_service.drop(
        w.session, project_id=w.pid, question_id="Q-001", author=AUTHOR, resolution="moot"
    )
    w.commit()
    return _q("reopen")(w)


@case("question_service.add_option")
def _(w: World) -> Expect:
    question_service.add_option(
        w.session, project_id=w.pid, question_id="Q-001", title="C", author=AUTHOR
    )
    return [("open_question_option", w.later(QuestionOptionModel, title="C"))]


@case("question_service.update_option")
def _(w: World) -> Expect:
    question_service.update_option(
        w.session, project_id=w.pid, question_id="Q-001", position=1, body="b", author=AUTHOR
    )
    return [("open_question_option", w.now(QuestionOptionModel, title="A"))]


@case("question_service.remove_option")
def _(w: World) -> Expect:
    uid = w.now(QuestionOptionModel, title="B")
    question_service.remove_option(
        w.session, project_id=w.pid, question_id="Q-001", position=2, author=AUTHOR
    )
    return [("open_question_option", uid)]


@case("question_service.link")
def _(w: World) -> Expect:
    question_service.link(
        w.session,
        project_id=w.pid,
        question_id="Q-001",
        to_kind=QuestionLinkKind.TASK,
        to_ref="PLN-001",
        relation=QuestionRelation.BLOCKS,
        author=AUTHOR,
    )
    return [("open_question_link", w.later(QuestionLinkModel, to_ref="PLN-001"))]


@case("question_service.unlink")
def _(w: World) -> Expect:
    question_service.link(
        w.session,
        project_id=w.pid,
        question_id="Q-001",
        to_kind=QuestionLinkKind.TASK,
        to_ref="PLN-001",
        relation=QuestionRelation.BLOCKS,
        author=AUTHOR,
    )
    w.commit()
    uid = w.now(QuestionLinkModel, to_ref="PLN-001")
    question_service.unlink(
        w.session,
        project_id=w.pid,
        question_id="Q-001",
        to_kind=QuestionLinkKind.TASK,
        to_ref="PLN-001",
        relation=QuestionRelation.BLOCKS,
        author=AUTHOR,
    )
    return [("open_question_link", uid)]


_LEGACY_QUESTION = """---
type: open-question
status: active
---

# OQ — Which database

## Question

**Postgres or SQLite?**

## Options

### Option A: Postgres

+ server

### Option B: SQLite

+ embedded
"""


@case("question_service.import_document")
def _(w: World) -> Expect:
    from cod_doc.services import import_service

    import_service.import_markdown(
        w.session,
        project_id=w.pid,
        doc_key="docs/oq-db",
        raw_markdown=_LEGACY_QUESTION,
        path="docs/oq-db.md",
        author=AUTHOR,
    )
    w.commit()
    question_service.import_document(
        w.session, project_id=w.pid, doc_key="docs/oq-db", author=AUTHOR
    )
    return [
        ("open_question", lambda: w.uid(OpenQuestionModel, title="Which database")),
    ]


# authored-таблицы вне спеков паритета --------------------------------------------------


@case("scenario_service.create")
def _(w: World) -> Expect:
    scenario_service.create(
        w.session,
        project_id=w.pid,
        title="Second",
        kind=ScenarioKind.INVARIANT,
        preconditions="p",
        expected="e",
        author=AUTHOR,
        scenario_id="SCN-002",
        group_key="g",
        steps=["only"],
    )
    return [
        ("scenario", w.later(ScenarioModel, scenario_id="SCN-002")),
        ("scenario_step", w.later(ScenarioStepModel, text="only")),
    ]


@case("scenario_service.update")
def _(w: World) -> Expect:
    scenario_service.update(
        w.session, project_id=w.pid, scenario_id="SCN-001", title="Renamed", author=AUTHOR
    )
    return [("scenario", w.now(ScenarioModel, scenario_id="SCN-001"))]


@case("scenario_service.retire")
def _(w: World) -> Expect:
    scenario_service.retire(w.session, project_id=w.pid, scenario_id="SCN-001", author=AUTHOR)
    return [("scenario", w.now(ScenarioModel, scenario_id="SCN-001"))]


@case("scenario_service.add_step")
def _(w: World) -> Expect:
    scenario_service.add_step(
        w.session, project_id=w.pid, scenario_id="SCN-001", text="three", author=AUTHOR
    )
    return [("scenario_step", w.later(ScenarioStepModel, text="three"))]


@case("scenario_service.set_steps")
def _(w: World) -> Expect:
    old = w.now(ScenarioStepModel, text="one")
    scenario_service.set_steps(
        w.session, project_id=w.pid, scenario_id="SCN-001", steps=["uno"], author=AUTHOR
    )
    # ORM-дифф (CLO-007): «one» переписан в «uno» — та же строка, тот же uid;
    # «two» удалён.
    return [("scenario_step", w.later(ScenarioStepModel, text="uno")), ("scenario_step", old)]


@case("scenario_service.link")
def _(w: World) -> Expect:
    scenario_service.link(
        w.session,
        project_id=w.pid,
        scenario_id="SCN-001",
        to_kind=ScenarioLinkKind.TASK,
        to_ref="PLN-001",
        relation=ScenarioRelation.VERIFIES,
        author=AUTHOR,
    )
    return [("scenario_link", w.later(ScenarioLinkModel, to_ref="PLN-001"))]


@case("scenario_service.unlink")
def _(w: World) -> Expect:
    scenario_service.link(
        w.session,
        project_id=w.pid,
        scenario_id="SCN-001",
        to_kind=ScenarioLinkKind.TASK,
        to_ref="PLN-001",
        relation=ScenarioRelation.VERIFIES,
        author=AUTHOR,
    )
    w.commit()
    uid = w.now(ScenarioLinkModel, to_ref="PLN-001")
    scenario_service.unlink(
        w.session,
        project_id=w.pid,
        scenario_id="SCN-001",
        to_kind=ScenarioLinkKind.TASK,
        to_ref="PLN-001",
        relation=ScenarioRelation.VERIFIES,
        author=AUTHOR,
    )
    return [("scenario_link", uid)]


@case("comment_service.create")
def _(w: World) -> Expect:
    comment_service.create(
        w.session, project_id=w.pid, document_id=w.doc_id, body="nit", author=AUTHOR
    )
    return [("doc_comment", w.later(DocCommentModel, body="nit"))]


@case("comment_service.update_status")
def _(w: World) -> Expect:
    c = comment_service.create(
        w.session, project_id=w.pid, document_id=w.doc_id, body="nit", author=AUTHOR
    )
    w.commit()
    comment_service.update_status(
        w.session, project_id=w.pid, comment_id=c.row_id, new_status="resolved"
    )
    return [("doc_comment", w.now(DocCommentModel, body="nit"))]


@case("comment_service.delete")
def _(w: World) -> Expect:
    c = comment_service.create(
        w.session, project_id=w.pid, document_id=w.doc_id, body="nit", author=AUTHOR
    )
    w.commit()
    uid = w.now(DocCommentModel, body="nit")
    comment_service.delete(w.session, c.row_id, project_id=w.pid)
    return [("doc_comment", uid)]


@case("task_doc_service.put")
def _(w: World) -> Expect:
    task_doc_service.put(
        w.session,
        project_id=w.pid,
        task_row_id=w.task("PLN-001").row_id,
        key="plan",
        title="Plan",
        body="steps",
        author=AUTHOR,
    )
    return [("task_document", w.later(TaskDocumentModel, key="plan"))]


@case("routine_service.create")
def _(w: World) -> Expect:
    routine_service.create(
        w.session, w.pid, name="nightly", check_name="doc_drift", cron="0 3 * * *"
    )
    return [("routine", w.later(RoutineModel, name="nightly"))]


@case("routine_service.update_status")
def _(w: World) -> Expect:
    routine_service.create(
        w.session, w.pid, name="nightly", check_name="doc_drift", cron="0 3 * * *"
    )
    w.commit()
    routine_service.update_status(w.session, w.pid, "nightly", enabled=False)
    return [("routine", w.now(RoutineModel, name="nightly"))]


@case("routine_service.delete")
def _(w: World) -> Expect:
    routine_service.create(
        w.session, w.pid, name="nightly", check_name="doc_drift", cron="0 3 * * *"
    )
    w.commit()
    uid = w.now(RoutineModel, name="nightly")
    routine_service.delete(w.session, w.pid, "nightly")
    return [("routine", uid)]


def _external_finding(w: World) -> None:
    finding_service.ingest_findings(
        w.session,
        project_id=w.pid,
        source_run_id="ai_review:1",
        seeds=[
            FindingSeed(
                fingerprint="fp-1", source="ai_review", title="Bug", severity="major", path="a.py"
            )
        ],
    )


@case("finding_service.ingest_findings")
def _(w: World) -> Expect:
    _external_finding(w)
    # Upsert находок — SQL мимо хука, операцию пишет sync_capture.record (CLO-007).
    return [("finding", w.later(FindingModel, fingerprint="fp-1"))]


@case("finding_service.dismiss_finding")
def _(w: World) -> Expect:
    _external_finding(w)
    w.commit()
    finding_uid = w.session.scalars(select(FindingModel.finding_uid)).one()
    finding_service.dismiss_finding(
        w.session, project_id=w.pid, finding_uid=finding_uid, author=AUTHOR
    )
    return [("finding", w.now(FindingModel, fingerprint="fp-1"))]


@case("finding_service.promote_finding")
def _(w: World) -> Expect:
    _external_finding(w)
    w.commit()
    finding = w.session.scalars(select(FindingModel)).one()
    finding_service.promote_finding(
        w.session,
        project_id=w.pid,
        finding_id=finding.row_id,
        on_finding="create_task",
        plan_id=w.plan_id,
        section_id=w.sec_a,
        author=AUTHOR,
        id_prefix="PLN",
    )
    return [("finding", w.now(FindingModel, fingerprint="fp-1"))]


@case("structure_drift.upsert_waiver")
def _(w: World) -> Expect:
    structure_drift.upsert_waiver(
        w.session,
        w.pid,
        finding_fingerprint="fp-x",
        owner=AUTHOR,
        reason="known",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    return [("structure_waiver", w.later(StructureWaiverModel, finding_fingerprint="fp-x"))]


def _suggestion(w: World) -> LinkSuggestionModel:
    section = w.session.scalars(select(SectionModel).where(SectionModel.anchor == "intro")).one()
    semantic._upsert_suggestion(
        w.session,
        from_section_id=section.row_id,
        to_doc_key="spec-a",
        to_section_id=None,
        score=0.9,
        evidence={},
    )
    w.session.flush()
    return w.session.scalars(select(LinkSuggestionModel)).one()


@case("link_service.update_suggestion_state")
def _(w: World) -> Expect:
    row = _suggestion(w)
    w.commit()
    section = w.session.scalars(select(SectionModel).where(SectionModel.anchor == "intro")).one()
    semantic.update_suggestion_state(
        w.session, row.row_id, "rejected", project_id=w.pid, author=AUTHOR
    )
    key = {"from_section_id": section.uid, "to_doc_key": "spec-a", "to_section_id": None}
    return [("link_suggestion", lambda: reg.edge_uid("link_suggestion", key))]


@case("link_service._auto_accept_above")
def _(w: World) -> Expect:
    row = _suggestion(w)
    w.commit()
    section = w.session.scalars(select(SectionModel).where(SectionModel.anchor == "intro")).one()
    semantic._auto_accept_above(w.session, row.from_section_id, 0.5)
    key = {"from_section_id": section.uid, "to_doc_key": "spec-a", "to_section_id": None}
    return [("link_suggestion", lambda: reg.edge_uid("link_suggestion", key))]


#: Сервисы без параметра ``author``: их операции честно ``system:unknown``.
NO_AUTHOR_PARAM: tuple[str, ...] = ("routine_service.",)


# sync_service (CLO-016) -------------------------------------------------------------
#
# push и pull — мутации по детектору паритета (пишут sync.pushed / sync.pulled),
# но authored-данные не трогают: push только помечает хвост changeset'ом, pull
# применяет чужие операции под ``suspended``. Правильный ответ — «операций нет».


def _sync_store(w: World) -> Any:
    from cod_doc.infra.remote.file import FileRemote

    root = w.root / "remote"
    root.mkdir(exist_ok=True)
    return FileRemote(root)


def _push_world(w: World, store: Any) -> None:
    from cod_doc.services.sync_service import push

    w.commit()  # подготовка случая не держит транзакцию: push пишет своими
    engine = make_engine(w.db_url)
    try:
        push.push(make_session_factory(engine), "w", author=AUTHOR, store=store)
    finally:
        engine.dispose()


@case("sync_service.push")
def _(w: World) -> Expect:
    _push_world(w, _sync_store(w))
    return []


NO_OPS["sync_service.push"] = (
    cap.CAPTURED_TABLES,
    "упаковка помечает уже записанный хвост changeset'ом, новых операций не пишет",
)


@case("sync_service.pull")
def _(w: World) -> Expect:
    import uuid

    from sqlalchemy import text

    from cod_doc.services.sync_service import pull, push

    store = _sync_store(w)
    _push_world(w, store)
    # Вторая реплика — копия БД после push со своей реплик-идентичностью.
    # backup, а не copyfile: свежий commit push'а может лежать ещё в -wal.
    other = w.root / "other.db"
    with (
        sqlite3.connect(w.db_url.removeprefix("sqlite:///")) as src,
        sqlite3.connect(other) as dst,
    ):
        src.backup(dst)
    engine = make_engine(f"sqlite:///{other}")
    try:
        sf = make_session_factory(engine)
        with sf() as s:
            s.execute(
                text("UPDATE replica SET uid = :u WHERE is_self = 1"), {"u": str(uuid.uuid7())}
            )
            s.commit()
        with sf() as s:
            task_service.update_description(
                s,
                project_id=w.pid,
                task_id="PLN-001",
                new_description="с другой реплики",
                author=AUTHOR,
            )
            s.commit()
        push.push(sf, "w", author=AUTHOR, store=store)
    finally:
        engine.dispose()
    engine = make_engine(w.db_url)
    try:
        result = pull.pull(make_session_factory(engine), "w", author=AUTHOR, store=store)
    finally:
        engine.dispose()
    assert result.ops > 0
    return []


#: Случаи, после которых в ``sync_op`` законно появляются чужие операции с
#: ``changeset`` — история применённых changeset'ов, а не захват (RFC 29 B14).
FOREIGN_HISTORY: frozenset[str] = frozenset({"sync_service.pull"})

NO_OPS["sync_service.pull"] = (
    cap.CAPTURED_TABLES,
    "чужие операции применяются под suspended: эха в журнале нет (§10.6)",
)


# конфликты (CLO-017): таблица local, разрешение — обычная запись поля сущности


def _open_title_conflict(w: World) -> str:
    from cod_doc.services.sync_service import conflicts

    spec = conflicts.ConflictSpec(
        entity_kind="task",
        entity_uid=w.uid(TaskModel, task_id="PLN-001"),
        field="title",
        kind="field",
        ours="ours",
        theirs="Заголовок с другой реплики",
        base="base",
        theirs_op="01JTHEIRSOP0000000000000000",
    )
    return conflicts.open_conflict(w.session, w.pid, spec, author=AUTHOR)


@case("sync_service.open_conflict")
def _(w: World) -> Expect:
    _open_title_conflict(w)
    return []


NO_OPS["sync_service.open_conflict"] = (
    cap.CAPTURED_TABLES,
    "sync_conflict — local: конфликт состояние реплики, а не данные проекта",
)


@case("sync_service.resolve_conflict")
def _(w: World) -> Expect:
    from cod_doc.services.sync_service import conflicts

    uid = _open_title_conflict(w)
    w.commit()
    conflicts.resolve_conflict(w.session, w.pid, uid, take="theirs", author=AUTHOR)
    return [("task", w.now(TaskModel, task_id="PLN-001"))]


@case("project_service.init_project")
def _(w: World) -> Expect:
    from cod_doc.config import ProjectEntry
    from cod_doc.services import project_service

    root = w.root / "second"
    root.mkdir()
    entry = ProjectEntry(name="second", path=str(root), db_url=w.db_url)
    project_service.init_project(entry)
    return [("project", w.later(ProjectModel, slug="second"))]


# таблицы без пути записи в коде: проверяется сам хук на прямой ORM-записи -------------

#: Authored-таблица, которую не пишет ни один сервис (на 2026-10-15). Случай
#: пишет её ORM напрямую — так хук проверен, а пробел виден в этом перечне.
NO_WRITE_PATH: dict[str, str] = {
    "tag": "тегов в сервисах нет: модель 0007, запись не реализована",
    "document_tag": "тегов в сервисах нет",
    "task_tag": "тегов в сервисах нет",
    "story_tag": "тегов в сервисах нет",
    "module": "модули только читаются (context_service, story_service.links)",
    "module_dependency": "модули только читаются",
    "module_code": "модули только читаются",
    "external_ref": "внешних ссылок в сервисах нет",
    "commit_link": "пишет только commit_link_service.import_from_git_log (нужен git-репозиторий); "
    "случай — прямая запись",
}


@case("orm.tag")
def _(w: World) -> Expect:
    cap.set_author(w.session, AUTHOR)
    tag = TagModel(project_id=w.pid, name="infra")
    w.session.add(tag)
    w.session.flush()
    task = w.task("PLN-001")
    story = w.session.scalars(select(UserStoryModel)).one()
    w.session.add_all(
        [
            TaskTagModel(task_id=task.row_id, tag_id=tag.row_id),
            StoryTagModel(story_id=story.row_id, tag_id=tag.row_id),
            DocumentTagModel(document_id=w.doc_id, tag_id=tag.row_id),
        ]
    )
    doc = w.session.get_one(DocumentModel, w.doc_id)
    return [
        ("tag", w.later(TagModel, name="infra")),
        ("task_tag", lambda: reg.edge_uid("task_tag", {"task_id": task.uid, "tag_id": tag.uid})),
        (
            "story_tag",
            lambda: reg.edge_uid("story_tag", {"story_id": story.uid, "tag_id": tag.uid}),
        ),
        (
            "document_tag",
            lambda: reg.edge_uid("document_tag", {"document_id": doc.uid, "tag_id": tag.uid}),
        ),
    ]


@case("orm.module")
def _(w: World) -> Expect:
    cap.set_author(w.session, AUTHOR)
    a = ModuleModel(project_id=w.pid, module_id="core", name="Core", status="active")
    b = ModuleModel(project_id=w.pid, module_id="infra", name="Infra", status="active")
    w.session.add_all([a, b])
    w.session.flush()
    w.session.add_all(
        [
            ModuleDependencyModel(from_module=a.row_id, to_module=b.row_id),
            ModuleCodeModel(module_id=a.row_id, kind="package", path="cod_doc/core"),
        ]
    )
    return [
        ("module", w.later(ModuleModel, module_id="core")),
        ("module_dependency", lambda: w.uid(ModuleDependencyModel)),
        ("module_code", lambda: w.uid(ModuleCodeModel)),
    ]


@case("orm.external_ref")
def _(w: World) -> Expect:
    cap.set_author(w.session, AUTHOR)
    task = w.task("PLN-001")
    w.session.add(
        ExternalRefModel(
            project_id=w.pid,
            entity_kind="task",
            entity_row_id=task.row_id,
            system="github",
            external_id="42",
        )
    )
    return [("external_ref", w.later(ExternalRefModel, external_id="42"))]


@case("orm.commit_link")
def _(w: World) -> Expect:
    cap.set_author(w.session, AUTHOR)
    w.session.add(
        CommitLinkModel(
            project_id=w.pid,
            task_id="PLN-001",
            sha="abc1234",
            short_sha="abc1234",
            message="feat: x",
            author="dev",
        )
    )
    return [("commit_link", w.later(CommitLinkModel, sha="abc1234"))]


# --- тесты -------------------------------------------------------------------------------


def _params() -> list[Any]:
    out = []
    for name in sorted(CASES):
        out.append(pytest.param(name, id=name))
    return out


def _allowed_fields(table: str) -> frozenset[str]:
    spec = reg.spec_for(table)
    if spec.cls is reg.SyncClass.AUTHORED:
        return reg.SHIPPED_COLUMNS[table] - {"uid"}
    return spec.authored_columns


@pytest.mark.parametrize("name", _params())
def test_write_path_writes_sync_op(world: World, name: str) -> None:
    """SCN-088: мутация → операция с uid изменённой сущности; только authored-поля."""
    session = world.session
    before = set(session.scalars(select(SyncOpModel.op_id)))
    expected = CASES[name](world)
    session.commit()
    fresh = _fresh_ops(session, before)
    if name in FOREIGN_HISTORY:
        # Чужие операции ложатся в журнал историей — с changeset, не хвостом (B14).
        # Эхо (операция без changeset) ловит NO_OPS ниже.
        assert any(op.changeset is not None for op in fresh), "pull не записал историю"
        fresh = [op for op in fresh if op.changeset is None]

    for op in fresh:
        fields, base = json.loads(op.fields), json.loads(op.base)
        allowed = _allowed_fields(op.entity_kind)
        assert set(fields) <= allowed, (op.entity_kind, set(fields) - allowed)
        assert set(base) <= allowed, (op.entity_kind, set(base) - allowed)
        if not name.startswith(NO_AUTHOR_PARAM):
            assert op.author != cap.UNKNOWN_AUTHOR, (name, op.entity_kind)
        assert op.changeset is None

    if name in NO_OPS:
        tables, _reason = NO_OPS[name]
        assert not [op for op in fresh if op.entity_kind in tables], "derived-запись дала операцию"
        return

    assert expected, f"{name}: случай без ожиданий"
    got = {(op.entity_kind, op.entity_uid) for op in fresh}
    for table, uid in expected:
        assert (table, uid()) in got, (
            f"{name}: нет операции {table} uid={uid()}; есть {sorted(got)}"
        )


def _parity_specs() -> list[ServiceSpec]:
    specs: list[ServiceSpec] = []
    for path in sorted(
        (REPO_ROOT / "tests" / "services").glob("test_*_mutation_surface_parity.py")
    ):
        module = importlib.import_module(f"tests.services.{path.stem}")
        for value in vars(module).values():
            items = value if isinstance(value, list | tuple) else [value]
            specs.extend(item for item in items if isinstance(item, ServiceSpec))
    return specs


def test_every_parity_mutation_has_a_case() -> None:
    """Каждая write-функция спеков паритета проверена случаем выше."""
    specs = _parity_specs()
    names = {spec.name for spec in specs}
    assert {
        "task_service",
        "story_service",
        "doc_service",
        "plan_service",
        "adr_topic_service",
    } <= names
    required = {f"{spec.name}.{fn}" for spec in specs for fn in discovered_mutations(spec)}
    missing = sorted(required - set(CASES))
    assert missing == [], f"мутации без случая захвата: {missing}"


def test_every_captured_table_has_a_case() -> None:
    """Каждая захватываемая таблица хотя бы раз ожидается в каком-то случае."""
    covered = set()
    for fn in CASES.values():
        # имена таблиц — первые элементы кортежей ожиданий; берём из исходника
        covered |= {c for c in fn.__code__.co_consts if isinstance(c, str)}
    missing = sorted(cap.CAPTURED_TABLES - covered)
    assert missing == [], f"захватываемые таблицы без случая: {missing}"


def test_no_write_path_list_is_current() -> None:
    """Перечень таблиц без пути записи не врёт: имени модели нет в сервисах."""
    import re

    services = "\n".join(
        p.read_text(encoding="utf-8") for p in (REPO_ROOT / "cod_doc" / "services").rglob("*.py")
    )
    models = {
        "tag": "TagModel(",
        "document_tag": "DocumentTagModel(",
        "task_tag": "TaskTagModel(",
        "story_tag": "StoryTagModel(",
        "module": "ModuleModel(",
        "module_dependency": "ModuleDependencyModel(",
        "module_code": "ModuleCodeModel(",
        "external_ref": "ExternalRefModel(",
    }
    for table, ctor in models.items():
        assert table in NO_WRITE_PATH
        assert re.search(rf"\b{re.escape(ctor)}", services) is None, (
            f"{table}: появился путь записи — убери из NO_WRITE_PATH и добавь случай сервиса"
        )


def test_repeat_ingest_records_only_counters(world: World) -> None:
    """CLO-007: повтор находки — операция с ``last_seen_at``/``times_seen`` и их ``base``.

    Upsert идёт SQL мимо хука; ``record`` пишет операцию на тот же uid, что у
    первой вставки, с прежними значениями счётчиков в ``base``.
    """
    session = world.session
    _external_finding(world)
    world.commit()
    first = session.scalars(select(FindingModel)).one()
    uid, seen_at = first.uid, first.last_seen_at
    before = set(session.scalars(select(SyncOpModel.op_id)))

    _external_finding(world)
    session.commit()
    ops = [op for op in _fresh_ops(session, before) if op.entity_kind == "finding"]

    assert [(op.entity_uid, op.kind) for op in ops] == [(uid, "upsert")]
    fields, base = json.loads(ops[0].fields), json.loads(ops[0].base)
    assert set(fields) == set(base) == {"last_seen_at", "times_seen"}
    assert (fields["times_seen"], base["times_seen"]) == (2, 1)
    stamp = seen_at if seen_at.tzinfo is not None else seen_at.replace(tzinfo=UTC)
    assert base["last_seen_at"] == stamp.astimezone(UTC).isoformat()
    assert ops[0].author == "system:ingest:ai_review"


def test_routine_finding_records_nothing(world: World) -> None:
    """Routine-находка — derived (``row_is_local``): upsert операции не даёт."""
    session = world.session
    before = set(session.scalars(select(SyncOpModel.op_id)))
    finding_service.ingest_findings(
        session,
        project_id=world.pid,
        source_run_id="routine-run",
        seeds=[FindingSeed(fingerprint="fp-r", source="routine", title="R", severity="minor")],
    )
    session.commit()
    assert [op for op in _fresh_ops(session, before) if op.entity_kind == "finding"] == []
