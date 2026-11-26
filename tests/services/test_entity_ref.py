"""`kind:key` ↔ `uid` (RFC 39 §3.1, REL-003)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from cod_doc.domain.relations import (
    ENTITY_KINDS,
    KEY_EXAMPLES,
    EntityRef,
    RefKind,
    RefSyntaxError,
    parse_ref,
)
from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    FindingModel,
    ModuleModel,
    OpenQuestionModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    ScenarioModel,
    SectionModel,
    StoryAcceptanceModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.services.entity_ref import MissingUidError, display_refs, resolve, resolve_many

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def _uid() -> str:
    return str(uuid.uuid4())


def _project(session: Session, slug: str) -> int:
    project = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", uid=_uid())
    session.add(project)
    session.flush()
    return project.row_id


def _seed(session: Session, project_id: int) -> dict[RefKind, str]:
    """По строке каждого вида; возвращает kind → публичный ключ."""
    plan = PlanModel(project_id=project_id, scope="plan-a", uid=_uid())
    story = UserStoryModel(
        project_id=project_id,
        story_id="US-001",
        persona="p",
        narrative="narrative",
        status="draft",
        priority="medium",
        uid=_uid(),
    )
    doc = DocumentModel(
        project_id=project_id,
        doc_key="docs/a.md",
        path="docs/a.md",
        type="spec",
        status="draft",
        title="Doc A",
        uid=_uid(),
    )
    session.add_all([plan, story, doc])
    session.flush()
    psec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Sec A", slug="a", position=1, uid=_uid()
    )
    session.add(psec)
    session.flush()
    session.add_all(
        [
            TaskModel(
                project_id=project_id,
                task_id="ADO-001",
                plan_id=plan.row_id,
                section_id=psec.row_id,
                title="Task",
                status="todo",
                type="feature",
                priority="medium",
                uid=_uid(),
            ),
            StoryAcceptanceModel(story_id=story.row_id, position=2, criterion="crit", uid=_uid()),
            SectionModel(
                document_id=doc.row_id,
                anchor="intro",
                heading="Intro",
                level=2,
                position=1,
                body="",
                content_hash="h",
                uid=_uid(),
            ),
            ADRModel(project_id=project_id, adr_id="ADR-001", title="Adr", uid=_uid()),
            OpenQuestionModel(
                project_id=project_id,
                question_id="Q-001",
                title="Question",
                question="?",
                author="t",
                uid=_uid(),
            ),
            ScenarioModel(
                project_id=project_id,
                scenario_id="SCN-001",
                title="Scenario",
                kind="happy",
                group_key="g",
                preconditions="",
                expected="",
                author="t",
                uid=_uid(),
            ),
            FindingModel(
                project_id=project_id,
                finding_uid="f-1",
                source="x",
                fingerprint="fp",
                severity="low",
                title="Finding",
                uid=_uid(),
            ),
            ModuleModel(
                project_id=project_id, module_id="core", name="Core", status="active", uid=_uid()
            ),
        ]
    )
    session.flush()
    return {
        RefKind.TASK: "ADO-001",
        RefKind.STORY: "US-001",
        RefKind.CRITERION: "US-001#2",
        RefKind.DOCUMENT: "docs/a.md",
        RefKind.SECTION: "docs/a.md#intro",
        RefKind.ADR: "ADR-001",
        RefKind.QUESTION: "Q-001",
        RefKind.SCENARIO: "SCN-001",
        RefKind.PLAN: "plan-a",
        RefKind.PLAN_SECTION: "plan-a#A",
        RefKind.FINDING: "f-1",
        RefKind.MODULE: "core",
    }


@pytest.fixture
def session(engine_with_schema: Engine):  # type: ignore[no-untyped-def]
    with Session(engine_with_schema) as s:
        yield s


# --- parse_ref ----------------------------------------------------------------


@pytest.mark.parametrize("kind", list(RefKind))
def test_parse_roundtrip_for_every_kind(kind: RefKind) -> None:
    raw = f"{kind.value}:{KEY_EXAMPLES[kind]}"
    ref = parse_ref(raw)
    assert ref == EntityRef(kind, KEY_EXAMPLES[kind])
    assert str(ref) == raw


def test_parse_unknown_kind_lists_allowed() -> None:
    with pytest.raises(RefSyntaxError) as exc:
        parse_ref("idea:X-1")
    assert all(k.value in str(exc.value) for k in RefKind)


@pytest.mark.parametrize("raw", ["story", "story:", "story: US-1", "", ":US-1"])
def test_parse_bad_key_shows_example(raw: str) -> None:
    with pytest.raises(RefSyntaxError):
        parse_ref(raw)


def test_parse_empty_key_message_has_example() -> None:
    with pytest.raises(RefSyntaxError, match=r"story:US-007"):
        parse_ref("story:")


def test_url_key_keeps_colons() -> None:
    assert parse_ref("url:https://x.y:80/a").key == "https://x.y:80/a"


# --- resolve ------------------------------------------------------------------


def test_every_entity_kind_resolves_and_displays(session: Session) -> None:
    pid = _project(session, "p1")
    keys = _seed(session, pid)
    assert set(keys) == ENTITY_KINDS
    refs = [EntityRef(k, key) for k, key in keys.items()]
    found = resolve_many(session, pid, refs)
    assert set(found) == set(refs)
    for ref, hit in found.items():
        assert hit.kind is ref.kind
        assert hit.key == ref.key
        assert hit.uid
        assert hit.title
        assert resolve(session, pid, ref) == hit
    shown = display_refs(session, pid, [(h.kind, h.uid) for h in found.values()])
    assert shown == {h.uid: EntityRef(h.kind, h.key) for h in found.values()}


def test_missing_and_foreign_project_are_none(session: Session) -> None:
    pid = _project(session, "p1")
    other = _project(session, "p2")
    keys = _seed(session, pid)
    assert resolve(session, pid, EntityRef(RefKind.TASK, "ADO-999")) is None
    for kind, key in keys.items():
        assert resolve(session, other, EntityRef(kind, key)) is None, kind


@pytest.mark.parametrize("kind", [RefKind.CODE, RefKind.URL])
def test_code_and_url_have_no_uid(session: Session, kind: RefKind) -> None:
    with pytest.raises(ValueError, match="uid"):
        resolve(session, 1, EntityRef(kind, "x"))
    with pytest.raises(ValueError, match="uid"):
        display_refs(session, 1, [(kind, "x")])


def test_row_without_uid_is_an_error(session: Session) -> None:
    pid = _project(session, "p1")
    _seed(session, pid)
    task = session.query(TaskModel).one()
    task.uid = None
    session.flush()
    with pytest.raises(MissingUidError, match="ADO-001"):
        resolve(session, pid, EntityRef(RefKind.TASK, "ADO-001"))


def test_one_query_per_kind(session: Session, engine_with_schema: Engine) -> None:
    pid = _project(session, "p1")
    keys = _seed(session, pid)
    refs = [EntityRef(k, key) for k, key in keys.items()]
    session.commit()
    seen: list[str] = []

    def count(*args: object) -> None:
        seen.append(str(args[2]))

    event.listen(engine_with_schema, "before_cursor_execute", count)
    try:
        found = resolve_many(session, pid, refs)
        assert len(seen) == len(ENTITY_KINDS)
        seen.clear()
        display_refs(session, pid, [(h.kind, h.uid) for h in found.values()])
        assert len(seen) == len(ENTITY_KINDS)
    finally:
        event.remove(engine_with_schema, "before_cursor_execute", count)


def test_rename_keeps_uid_and_display_shows_new_key(session: Session) -> None:
    pid = _project(session, "p1")
    _seed(session, pid)
    old = resolve(session, pid, EntityRef(RefKind.DOCUMENT, "docs/a.md"))
    sec = resolve(session, pid, EntityRef(RefKind.SECTION, "docs/a.md#intro"))
    assert old is not None
    assert sec is not None
    doc = session.query(DocumentModel).one()
    doc.doc_key = "docs/b.md"
    session.flush()
    shown = display_refs(session, pid, [(RefKind.DOCUMENT, old.uid), (RefKind.SECTION, sec.uid)])
    assert shown[old.uid] == EntityRef(RefKind.DOCUMENT, "docs/b.md")
    assert shown[sec.uid] == EntityRef(RefKind.SECTION, "docs/b.md#intro")
    assert resolve(session, pid, EntityRef(RefKind.DOCUMENT, "docs/a.md")) is None
