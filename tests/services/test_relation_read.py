"""relations(): единое чтение из всех источников (RFC 39 §3.6, REL-011)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from cod_doc.infra.models import (
    ADRModel,
    ADRRelationModel,
    ADRSupersedeModel,
    ADRTaskModel,
    CommitLinkModel,
    DependencyModel,
    DocumentModel,
    LinkModel,
    OpenQuestionModel,
    QuestionLinkModel,
    ScenarioLinkModel,
    ScenarioModel,
    SectionModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.services import relation_service as rs
from tests.services.test_relation_service import W, _uid

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


@pytest.fixture
def w(engine_with_schema: Engine, tmp_path: Path):  # type: ignore[no-untyped-def]
    with Session(engine_with_schema) as s:
        yield W(s, tmp_path)


def _row_id(w: W, model: type, **where: str) -> int:
    from sqlalchemy import select

    return int(w.s.scalars(select(model).filter_by(**where)).one().row_id)


def _seed_task_edges(w: W) -> None:
    """Задача ADO-001 со связью из каждого источника."""
    t1 = _row_id(w, TaskModel, task_id="ADO-001")
    t2 = _row_id(w, TaskModel, task_id="ADO-002")
    story = _row_id(w, UserStoryModel, story_id="US-007")
    question = _row_id(w, OpenQuestionModel, question_id="Q-001")
    scenario = _row_id(w, ScenarioModel, scenario_id="SCN-001")
    w.s.add_all(
        [
            DependencyModel(from_task_id=t1, to_task_id=t2, kind="blocks", note="n"),
            StoryLinkModel(story_id=story, to_kind="task", to_ref="ADO-001",
                           relation="implemented_by", uid=_uid()),
            ScenarioLinkModel(scenario_row_id=scenario, to_kind="task", to_ref="ADO-001",
                              relation="verifies", uid=_uid()),
            QuestionLinkModel(question_row_id=question, to_kind="task", to_ref="ADO-001",
                              relation="about", uid=_uid()),
            CommitLinkModel(project_id=w.pid, task_id="ADO-001", sha="abc0" * 8,
                            short_sha="abc0", message="feat: x\nbody", author="a", uid=_uid()),
        ]
    )  # fmt: skip
    w.s.add(DependencyModel(from_task_id=t2, to_task_id=t1, kind="blocks"))
    adr = ADRModel(project_id=w.pid, adr_id="ADR-001", title="Adr", uid=_uid())
    doc = DocumentModel(
        project_id=w.pid, doc_key="docs/a.md", path="docs/a.md", type="spec", status="draft",
        title="Doc A", uid=_uid(),
    )  # fmt: skip
    w.s.add_all([adr, doc])
    w.s.flush()
    sec = SectionModel(
        document_id=doc.row_id, anchor="intro", heading="Intro", level=2, position=1, body="",
        content_hash="h", uid=_uid(),
    )  # fmt: skip
    w.s.add(sec)
    w.s.flush()
    w.s.add_all(
        [
            ADRTaskModel(adr_row_id=adr.row_id, task_id="ADO-001", relation="implements", uid=_uid()),
            LinkModel(project_id=w.pid, from_section_id=sec.row_id, raw="[[ADO-001]]",
                      kind="task", to_task_id="ADO-001", resolved=True),
        ]
    )  # fmt: skip
    w.s.flush()
    w.relate("story:US-064", "implemented_by", "task:ADO-001")  # relation


def test_every_source_is_read_with_labels_and_directions(w: W) -> None:
    _seed_task_edges(w)
    views = rs.relations(w.s, project_id=w.pid, ref="task:ADO-001")
    got = {(v.source, v.label, v.direction, v.other_ref) for v in views}
    assert got == {
        ("relation", "implements", "in", "story:US-064"),
        ("dependency", "blocked_by", "out", "task:ADO-002"),
        ("dependency", "blocks", "in", "task:ADO-002"),
        ("adr_task", "implements", "in", "adr:ADR-001"),
        ("story_link", "implements", "in", "story:US-007"),
        ("scenario_link", "verified_by", "in", "scenario:SCN-001"),
        ("question_link", "discussed_in", "in", "question:Q-001"),
        ("prose", "mentioned_in", "in", "section:docs/a.md#intro"),
        ("commit", "committed_in", "out", "commit:abc0"),
    }
    commit = next(v for v in views if v.source == "commit")
    assert commit.other is None
    assert commit.other_title == "feat: x"
    assert next(v for v in views if v.source == "dependency" and v.direction == "out").note == "n"
    assert all(v.resolved for v in views)


def test_include_derived_false_keeps_authored_and_backends(w: W) -> None:
    _seed_task_edges(w)
    views = rs.relations(w.s, project_id=w.pid, ref="task:ADO-001", include_derived=False)
    sources = {v.source for v in views}
    assert "prose" not in sources
    assert "commit" not in sources
    assert {"relation", "dependency", "adr_task", "story_link"} <= sources


def test_direction_and_type_filters(w: W) -> None:
    _seed_task_edges(w)
    out = rs.relations(w.s, project_id=w.pid, ref="task:ADO-001", direction="out")
    assert {v.source for v in out} == {"dependency", "commit"}
    only = rs.relations(w.s, project_id=w.pid, ref="task:ADO-001", types=["committed_in"])
    assert [v.source for v in only] == ["commit"]


def test_outgoing_legacy_and_adr_backends(w: W) -> None:
    w.story("US-300")
    story = _row_id(w, UserStoryModel, story_id="US-300")
    w.s.add(StoryLinkModel(story_id=story, to_kind="task", to_ref="ADO-001",
                           relation="implemented_by", uid=_uid()))  # fmt: skip
    a1 = ADRModel(project_id=w.pid, adr_id="ADR-001", title="a1", uid=_uid())
    a2 = ADRModel(project_id=w.pid, adr_id="ADR-002", title="a2", uid=_uid())
    w.s.add_all([a1, a2])
    w.s.flush()
    w.s.add_all(
        [
            ADRRelationModel(from_id=a1.row_id, to_id=a2.row_id, kind="amends", uid=_uid()),
            ADRSupersedeModel(superseding_id=a2.row_id, superseded_id=a1.row_id, uid=_uid()),
            ADRTaskModel(adr_row_id=a1.row_id, task_id="ADO-001", relation="relates", uid=_uid()),
        ]
    )
    w.s.flush()
    story_views = rs.relations(w.s, project_id=w.pid, ref="story:US-300")
    assert [(v.source, v.label, v.direction, v.other_ref) for v in story_views] == [
        ("story_link", "implemented_by", "out", "task:ADO-001")
    ]
    adr1 = {
        (v.source, v.label, v.direction, v.other_ref)
        for v in rs.relations(w.s, project_id=w.pid, ref="adr:ADR-001")
    }
    assert adr1 == {
        ("adr_relation", "amends", "out", "adr:ADR-002"),
        ("adr_supersedes", "superseded_by", "in", "adr:ADR-002"),
        ("adr_task", "relates_to", "out", "task:ADO-001"),
    }
    adr2 = {
        (v.source, v.label, v.direction)
        for v in rs.relations(w.s, project_id=w.pid, ref="adr:ADR-002")
    }
    assert adr2 == {
        ("adr_relation", "amended_by", "in"),
        ("adr_supersedes", "supersedes", "out"),
    }


def test_symmetric_edge_reads_as_out_from_both_ends(w: W) -> None:
    w.relate("story:US-064", "see_also", "story:US-007")
    for ref in ("story:US-064", "story:US-007"):
        (view,) = rs.relations(w.s, project_id=w.pid, ref=ref)
        assert (view.label, view.direction) == ("relates_to", "out")
    assert not rs.relations(w.s, project_id=w.pid, ref="story:US-007", direction="in")


def test_query_count_does_not_grow_with_edges(w: W, engine_with_schema: Engine) -> None:
    _seed_task_edges(w)

    def measure() -> int:
        seen: list[int] = []

        def count(*args: object) -> None:
            seen.append(1)

        event.listen(engine_with_schema, "before_cursor_execute", count)
        try:
            rs.relations(w.s, project_id=w.pid, ref="task:ADO-001")
        finally:
            event.remove(engine_with_schema, "before_cursor_execute", count)
        return len(seen)

    small_views = rs.relations(w.s, project_id=w.pid, ref="task:ADO-001")
    small = measure()
    _seed_extra(w)
    big_views = rs.relations(w.s, project_id=w.pid, ref="task:ADO-001")
    big = measure()
    assert len(big_views) > len(small_views) + 20
    assert big == small, (small, big)


def _seed_extra(w: W) -> None:
    t1 = _row_id(w, TaskModel, task_id="ADO-001")
    for i in range(10, 40):
        w.task(f"ADO-9{i}")
        w.story(f"US-9{i}")
        w.s.add(
            DependencyModel(
                from_task_id=t1,
                to_task_id=_row_id(w, TaskModel, task_id=f"ADO-9{i}"),
                kind="blocks",
            )
        )
        w.s.add(StoryLinkModel(story_id=_row_id(w, UserStoryModel, story_id=f"US-9{i}"), to_kind="task",
                               to_ref="ADO-001", relation="implemented_by", uid=_uid()))  # fmt: skip
    w.s.flush()
