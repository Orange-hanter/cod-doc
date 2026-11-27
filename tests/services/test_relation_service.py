"""relation_service: запись, чтение, словарь, проверка (RFC 39 §3.4, REL-006)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cod_doc.infra.models import (
    ActivityEventModel,
    ADRModel,
    OpenQuestionModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RelationModel,
    RevisionModel,
    ScenarioModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.services import activity_service
from cod_doc.services import relation_service as rs
from cod_doc.services.relation_service import RelationError, RelationTypeSpec

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine

AUTHOR = "tester"


def _uid() -> str:
    return str(uuid.uuid4())


class W:
    """Проект с набором сущностей; `add_*` досоздают недостающее."""

    def __init__(self, session: Session, root: Path) -> None:
        self.s = session
        project = ProjectModel(slug="p", title="p", root_path=str(root), uid=_uid())
        session.add(project)
        session.flush()
        self.pid = project.row_id
        plan = PlanModel(project_id=self.pid, scope="plan", uid=_uid())
        session.add(plan)
        session.flush()
        sec = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="S", slug="a", position=1, uid=_uid()
        )
        session.add(sec)
        session.flush()
        self.plan, self.sec = plan.row_id, sec.row_id
        for sid in ("US-007", "US-064", "US-100"):
            self.story(sid)
        self.task("ADO-001")
        self.task("ADO-002")
        self.question("Q-001")
        self.scenario("SCN-001")

    def story(self, sid: str) -> None:
        self.s.add(
            UserStoryModel(
                project_id=self.pid,
                story_id=sid,
                persona="p",
                narrative="n",
                status="draft",
                priority="medium",
                uid=_uid(),
            )
        )
        self.s.flush()

    def task(self, tid: str) -> None:
        self.s.add(
            TaskModel(
                project_id=self.pid,
                task_id=tid,
                plan_id=self.plan,
                section_id=self.sec,
                title="t",
                status="todo",
                type="feature",
                priority="medium",
                uid=_uid(),
            )
        )
        self.s.flush()

    def question(self, qid: str) -> None:
        self.s.add(
            OpenQuestionModel(
                project_id=self.pid,
                question_id=qid,
                title="q",
                question="?",
                author="t",
                uid=_uid(),
            )
        )
        self.s.flush()

    def scenario(self, sid: str) -> None:
        self.s.add(
            ScenarioModel(
                project_id=self.pid,
                scenario_id=sid,
                title="s",
                kind="happy",
                group_key="g",
                preconditions="",
                expected="",
                author="t",
                uid=_uid(),
            )
        )
        self.s.flush()

    def relate(self, a: str, t: str, b: str, **kw: object):  # type: ignore[no-untyped-def]
        return rs.relate(
            self.s, project_id=self.pid, from_ref=a, type=t, to_ref=b, author=AUTHOR, **kw
        )

    def events(self, kind: str) -> int:
        return (
            self.s.scalar(
                select(func.count())
                .select_from(ActivityEventModel)
                .where(ActivityEventModel.kind == kind)
            )
            or 0
        )

    def edges(self) -> int:
        return self.s.scalar(select(func.count()).select_from(RelationModel)) or 0


@pytest.fixture
def w(engine_with_schema: Engine, tmp_path: Path):  # type: ignore[no-untyped-def]
    with Session(engine_with_schema) as s:
        yield W(s, tmp_path)


def test_trigger_see_also_is_relates_to_with_event_and_revision(w: W) -> None:
    edge = w.relate("story:US-064", "see_also", "story:US-007")
    assert edge.created
    assert edge.type == "relates_to"
    assert w.edges() == 1
    assert w.events("relation.added") == 1
    story = w.s.scalars(
        select(UserStoryModel).where(UserStoryModel.story_id == edge.from_ref.key)
    ).one()
    rev = w.s.scalars(select(RevisionModel).where(RevisionModel.entity_id == story.row_id)).all()
    assert any("relation.added" in r.diff for r in rev)
    payload = (
        w.s.scalars(select(ActivityEventModel).where(ActivityEventModel.kind == "relation.added"))
        .one()
        .payload
    )
    assert payload["from"]["ref"].startswith("story:") and payload["to"]["uid"]


def test_symmetric_dedupe_and_idempotency_no_second_event(w: W) -> None:
    w.relate("story:US-064", "see_also", "story:US-007")
    again = w.relate("story:US-007", "relates_to", "story:US-064")
    assert not again.created
    assert w.edges() == 1
    assert w.events("relation.added") == 1


def test_inverse_call_is_the_same_edge(w: W) -> None:
    first = w.relate("task:ADO-001", "implements", "story:US-007")
    assert first.reversed
    assert (first.type, str(first.from_ref), str(first.to_ref)) == (
        "implemented_by",
        "story:US-007",
        "task:ADO-001",
    )
    second = w.relate("story:US-007", "implemented_by", "task:ADO-001")
    assert not second.created
    assert w.edges() == 1


def test_acyclic_rejects_cycle(w: W) -> None:
    w.relate("story:US-007", "depends_on", "story:US-064")
    w.relate("story:US-064", "depends_on", "story:US-100")
    with pytest.raises(RelationError, match="цикл"):
        w.relate("story:US-100", "depends_on", "story:US-007")
    with pytest.raises(RelationError, match="цикл"):  # то же через обратное имя
        w.relate("story:US-007", "required_by", "story:US-100")
    with pytest.raises(RelationError, match="самой собой"):
        w.relate("story:US-007", "depends_on", "story:US-007")


def test_forward_ref_then_verify_fills_uid(w: W) -> None:
    edge = w.relate("question:Q-001", "about", "task:ADO-999")
    assert edge.to_uid is None
    assert not edge.resolved
    report = rs.verify(w.s, project_id=w.pid)
    assert report.broken
    assert w.events("relation.added") == 1
    w.task("ADO-999")
    before = w.s.scalar(select(func.count()).select_from(ActivityEventModel))
    report = rs.verify(w.s, project_id=w.pid)
    assert report.filled == 1
    row = w.s.scalars(select(RelationModel)).one()
    assert row.to_uid and row.to_key is None and row.resolved
    assert w.s.scalar(select(func.count()).select_from(ActivityEventModel)) == before


def test_forward_ref_only_for_allow_forward_types(w: W) -> None:
    with pytest.raises(RelationError, match="allow_forward"):
        w.relate("story:US-007", "implemented_by", "task:ADO-999")
    # цель отсутствует, но источник есть: пишется после появления сущности, дубля нет
    w.relate("question:Q-001", "about", "task:ADO-999")
    w.task("ADO-999")
    again = w.relate("question:Q-001", "about", "task:ADO-999")
    assert not again.created
    assert w.edges() == 1
    assert w.s.scalars(select(RelationModel)).one().to_uid


def test_code_ref_verify(w: W, tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    w.relate("scenario:SCN-001", "exercised_by", "code:mod.py::f")
    w.relate("scenario:SCN-001", "exercised_by", "code:gone.py::g")
    w.relate("scenario:SCN-001", "exercised_by", "task:ADO-001")
    report = rs.verify(w.s, project_id=w.pid)
    assert report.checked == 2
    assert report.resolved == 1
    assert len(report.broken) == 1
    rows = {r.to_key: r for r in w.s.scalars(select(RelationModel)).all() if r.to_key}
    assert rows["mod.py::f"].resolved and rows["mod.py::f"].last_checked
    assert not rows["gone.py::g"].resolved and rows["gone.py::g"].broken_reason


def test_url_shape(w: W) -> None:
    w.relate("question:Q-001", "about", "url:https://example.com/x")
    with pytest.raises(RelationError, match="url"):
        w.relate("question:Q-001", "about", "url:ftp://x")


def test_backend_types_name_the_existing_tool(w: W) -> None:
    with pytest.raises(RelationError) as exc:
        w.relate("task:ADO-001", "blocked_by", "task:ADO-002")
    assert "task_add_dependency" in exc.value.hint
    assert w.edges() == 0


def test_error_lists_every_invalid_param(w: W) -> None:
    with pytest.raises(RelationError) as exc:
        rs.relate(w.s, project_id=w.pid, from_ref="nope", type="x", to_ref="story:", author=" ")
    assert len(exc.value.problems) >= 3
    with pytest.raises(RelationError) as exc2:
        w.relate("story:US-404", "relates_to", "story:US-405")
    assert len(exc2.value.problems) == 2


def test_unknown_type_hint_suggests_and_gives_example(w: W) -> None:
    with pytest.raises(RelationError) as exc:
        w.relate("story:US-064", "depend_on", "story:US-007")
    msg = str(exc.value)
    assert "Похоже: depends_on" in msg
    assert "required_by" in msg
    assert 'relate(from_ref="story:US-064", type="depends_on", to_ref="story:US-007")' in msg


def test_pair_with_only_relates_to_names_define_type(w: W) -> None:
    with pytest.raises(RelationError, match="relation_type_define"):
        w.relate("document:a.md", "depends_on", "document:b.md")


def test_unrelate(w: W) -> None:
    w.relate("story:US-064", "see_also", "story:US-007")
    assert rs.unrelate(
        w.s,
        project_id=w.pid,
        from_ref="story:US-007",
        type="relates_to",
        to_ref="story:US-064",
        author=AUTHOR,
    )
    assert w.events("relation.removed") == 1
    assert w.edges() == 0
    assert not rs.unrelate(
        w.s,
        project_id=w.pid,
        from_ref="story:US-007",
        type="relates_to",
        to_ref="story:US-064",
        author=AUTHOR,
    )


def test_relations_labels_and_counts(w: W) -> None:
    w.relate("story:US-007", "implemented_by", "task:ADO-001")
    out = rs.relations(w.s, project_id=w.pid, ref="story:US-007")
    inc = rs.relations(w.s, project_id=w.pid, ref="task:ADO-001")
    assert [(v.label, v.direction, str(v.other)) for v in out] == [
        ("implemented_by", "out", "task:ADO-001")
    ]
    assert [(v.label, v.direction, str(v.other)) for v in inc] == [
        ("implements", "in", "story:US-007")
    ]
    assert rs.relations(w.s, project_id=w.pid, ref="task:ADO-001", types=["implemented_by"])
    assert not rs.relations(w.s, project_id=w.pid, ref="task:ADO-001", types=["verifies"])
    infos = {
        (i.type.name, tuple(sorted(k.value for k in i.type.from_kinds))): i.edges
        for i in rs.relation_types(w.s, project_id=w.pid, from_kind="story")
    }
    assert infos[("implemented_by", ("story",))] == 1


def test_define_type_flow(w: W) -> None:
    spec = RelationTypeSpec("mitigates", "mitigated_by", ["story"], ["adr"])
    rs.define_type(w.s, project_id=w.pid, spec=spec, author=AUTHOR)
    assert rs.define_type(w.s, project_id=w.pid, spec=spec, author=AUTHOR)  # повтор — no-op
    w.s.add(ADRModel(project_id=w.pid, adr_id="ADR-001", title="a", uid=_uid()))
    w.s.flush()
    edge = w.relate("adr:ADR-001", "mitigated_by", "story:US-007")
    assert edge.reversed
    assert any(
        i.type.name == "mitigates" and i.edges == 1 and not i.builtin
        for i in rs.relation_types(w.s, project_id=w.pid)
    )
    assert rs.deprecate_type(w.s, project_id=w.pid, name="mitigates", author=AUTHOR)
    with pytest.raises(RelationError, match="устарел"):
        w.relate("story:US-064", "mitigates", "adr:ADR-001")
    assert rs.relations(w.s, project_id=w.pid, ref="story:US-007")  # старое ребро читается


def test_define_type_collisions(w: W) -> None:
    for name, inv in (("relates_to", "x_by"), ("see_also", "y_by"), ("fresh", "required_by")):
        with pytest.raises(RelationError, match="занят встроенным"):
            rs.define_type(
                w.s,
                project_id=w.pid,
                author=AUTHOR,
                spec=RelationTypeSpec(name, inv, ["story"], ["story"]),
            )
    rs.define_type(
        w.s,
        project_id=w.pid,
        author=AUTHOR,
        spec=RelationTypeSpec("risk_of", "risked_by", ["story"], ["adr"]),
    )
    with pytest.raises(RelationError):
        rs.define_type(
            w.s,
            project_id=w.pid,
            author=AUTHOR,
            spec=RelationTypeSpec("risk_of", "other_by", ["story"], ["adr"]),
        )
    with pytest.raises(RelationError):  # обратное имя уже занято проектным типом
        rs.define_type(
            w.s,
            project_id=w.pid,
            author=AUTHOR,
            spec=RelationTypeSpec("threat_of", "risked_by", ["story"], ["adr"]),
        )
    with pytest.raises(RelationError) as exc:
        rs.define_type(
            w.s,
            project_id=w.pid,
            author=AUTHOR,
            spec=RelationTypeSpec("Bad Name", "x", ["code"], []),
        )
    assert len(exc.value.problems) >= 3
    with pytest.raises(RelationError, match="встроенный"):
        rs.deprecate_type(w.s, project_id=w.pid, name="relates_to", author=AUTHOR)


def test_event_errors_are_not_swallowed(w: W) -> None:
    with (
        patch.object(activity_service, "emit", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        w.relate("story:US-064", "see_also", "story:US-007")


def test_deleting_an_endpoint_removes_edges_in_both_directions(w: W) -> None:
    """REL-007: question_service.delete публикует EntityDeleted: рёбра сняты, след остался."""
    from cod_doc.services import question_service

    w.question("Q-002")
    w.relate("question:Q-001", "about", "story:US-007")  # удаляемый — источник
    w.relate("question:Q-002", "duplicates", "question:Q-001")  # удаляемый — цель
    w.relate("question:Q-002", "about", "story:US-007")  # чужое ребро остаётся
    question_service.delete(w.s, project_id=w.pid, question_id="Q-001", author=AUTHOR)
    left = w.s.scalars(select(RelationModel)).all()
    assert [(r.from_kind, r.to_kind, r.type) for r in left] == [("question", "story", "about")]
    removed = w.s.scalars(
        select(ActivityEventModel).where(ActivityEventModel.kind == "relation.removed")
    ).all()
    assert len(removed) == 2
    assert {e.payload["reason"] for e in removed} == {"endpoint_deleted"}
    # источник пережил удаление цели: ревизия на нём (Q-002)
    q2 = w.s.scalars(
        select(OpenQuestionModel).where(OpenQuestionModel.question_id == "Q-002")
    ).one()
    assert w.s.scalars(
        select(RevisionModel).where(
            RevisionModel.entity_id == q2.row_id, RevisionModel.reason == "endpoint_deleted"
        )
    ).all()


def test_backend_redirect_also_reports_missing_targets(w: W) -> None:
    with pytest.raises(RelationError) as exc:
        w.relate("task:X-1", "blocked_by", "task:X-2")
    assert "task_add_dependency" in exc.value.hint
    text = "\n".join(exc.value.problems)
    assert "backend" in text
    assert "task:X-1 не найден" in text
    assert "task:X-2 не найден" in text
