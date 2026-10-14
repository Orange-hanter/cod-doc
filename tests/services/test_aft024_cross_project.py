"""AFT-024: скоуп проекта у doc-мутаций и чтений по row_id, task_doc, list_for_plan,
агрегатов плана и task_refs одобрений.

Общая hub-БД: row_id документа, секции, задачи и плана сквозные на все проекты,
поэтому операция по row_id без сверки проекта достаёт сущность соседа. Каждый
тест держит два проекта и проверяет, что сущность проекта A из проекта B
неотличима от несуществующей, а сама она не меняется.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType, Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import approval_service, checkout_service, doc_service, task_doc_service
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service as tasks
from cod_doc.services.doc_service import DocumentNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_TID = "ADO-001"
_AUTHOR = "human:test"


class _Proj(NamedTuple):
    project: int
    plan: int
    section: int
    task_row: int
    doc: int
    doc_section: int


def _project(session: Session, slug: str) -> _Proj:
    """Проект ``slug``: план с секцией и задачей ``ADO-001``, документ ``guide`` с секцией."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/repo/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
    session.add(sec)
    session.flush()
    task = tasks.create(
        session,
        project_id=proj.row_id,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=_TID,
        title=f"Task of {slug}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=_AUTHOR,
    )
    doc = doc_service.create(
        session,
        project_id=proj.row_id,
        doc_key="guide",
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        title=f"Guide of {slug}",
        author=_AUTHOR,
    )
    assert task.row_id is not None and doc.row_id is not None
    section = doc_service.add_section(
        session,
        project_id=proj.row_id,
        document_id=doc.row_id,
        anchor="intro",
        heading="Intro",
        level=2,
        position=0,
        body=f"body of {slug}",
        author=_AUTHOR,
    )
    assert section.row_id is not None
    return _Proj(proj.row_id, plan.row_id, sec.row_id, task.row_id, doc.row_id, section.row_id)


def _two_projects(engine_with_schema):  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _project(session, "a")
        b = _project(session, "b")
    return factory, a, b


# --------------------------------------------------------------------------- #
# doc_service: мутации и чтения по row_id                                      #
# --------------------------------------------------------------------------- #

_DOC_MUTATIONS = {
    "add_section": lambda s, a, pid: doc_service.add_section(
        s,
        project_id=pid,
        document_id=a.doc,
        anchor="intruder",
        heading="Intruder",
        level=2,
        position=1,
        body="x",
        author=_AUTHOR,
    ),
    "patch_section": lambda s, a, pid: doc_service.patch_section(
        s, project_id=pid, document_id=a.doc, anchor="intro", new_body="hijacked", author=_AUTHOR
    ),
    "delete_section": lambda s, a, pid: doc_service.delete_section(
        s, project_id=pid, document_id=a.doc, anchor="intro", author=_AUTHOR
    ),
    "update_status": lambda s, a, pid: doc_service.update_status(
        s, project_id=pid, document_id=a.doc, new_status=DocumentStatus.DEPRECATED, author=_AUTHOR
    ),
    "accept": lambda s, a, pid: doc_service.accept(
        s, project_id=pid, document_id=a.doc, author=_AUTHOR
    ),
    "rename": lambda s, a, pid: doc_service.rename(
        s, project_id=pid, document_id=a.doc, new_doc_key="stolen", author=_AUTHOR
    ),
}


@pytest.mark.parametrize("mutation", sorted(_DOC_MUTATIONS))
def test_doc_mutation_by_row_id_does_not_touch_another_projects_document(
    engine_with_schema, mutation
) -> None:  # type: ignore[no-untyped-def]
    """Мутация документа проекта A из проекта B — ``DocumentNotFoundError``,
    документ A (ключ, статус, секции и их тела) не меняется."""
    factory, a, b = _two_projects(engine_with_schema)

    with pytest.raises(DocumentNotFoundError), transactional(factory) as session:
        _DOC_MUTATIONS[mutation](session, a, b.project)

    with transactional(factory) as session:
        doc = doc_service.get_doc_by_id(session, a.doc, project_id=a.project)
        assert doc is not None
        assert (doc.doc_key, doc.status) == ("guide", DocumentStatus.DRAFT)
        sections = doc_service.get_sections(session, a.doc, project_id=a.project)
        assert [(s.anchor, s.body) for s in sections] == [("intro", "body of a")]


def test_doc_mutation_in_own_project_still_works(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Скоуп не ломает свою правку: патч секции своего документа проходит."""
    factory, a, _b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        doc_service.patch_section(
            session,
            project_id=a.project,
            document_id=a.doc,
            anchor="intro",
            new_body="edited",
            author=_AUTHOR,
        )
        (section,) = doc_service.get_sections(session, a.doc, project_id=a.project)
        assert section.body == "edited"


def test_doc_reads_by_row_id_hide_another_projects_document(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Чтения по row_id из чужого проекта: документ и секция — None, секции — пусто."""
    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        assert doc_service.get_doc_by_id(session, a.doc, project_id=b.project) is None
        assert doc_service.get_section_by_id(session, a.doc_section, project_id=b.project) is None
        assert doc_service.get_sections(session, a.doc, project_id=b.project) == []

        own = doc_service.get_section_by_id(session, a.doc_section, project_id=a.project)
        assert own is not None and own.body == "body of a"


# --------------------------------------------------------------------------- #
# task_doc_service                                                             #
# --------------------------------------------------------------------------- #


def test_task_docs_of_another_projects_task_are_invisible_and_immutable(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Документы задачи проекта A из проекта B: чтения пусты, put/revert —
    ``LookupError``; документ A не меняется."""
    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        task_doc_service.put(
            session,
            project_id=a.project,
            task_row_id=a.task_row,
            key="design",
            title="Design",
            body="v1",
            author=_AUTHOR,
        )
        (rev_row,) = task_doc_service.revisions(session, a.task_row, "design", project_id=a.project)

    with transactional(factory) as session:
        assert task_doc_service.get(session, a.task_row, "design", project_id=b.project) is None
        assert task_doc_service.list_for_task(session, a.task_row, project_id=b.project) == []
        assert task_doc_service.revisions(session, a.task_row, "design", project_id=b.project) == []

    with pytest.raises(LookupError), transactional(factory) as session:
        task_doc_service.put(
            session,
            project_id=b.project,
            task_row_id=a.task_row,
            key="design",
            title="Design",
            body="hijacked",
            author=_AUTHOR,
        )
    with pytest.raises(LookupError), transactional(factory) as session:
        task_doc_service.revert(
            session,
            project_id=b.project,
            task_row_id=a.task_row,
            key="design",
            revision_id=rev_row["revision_id"],
            author=_AUTHOR,
        )

    with transactional(factory) as session:
        own = task_doc_service.get(session, a.task_row, "design", project_id=a.project)
        assert own is not None and own.body == "v1"


# --------------------------------------------------------------------------- #
# Задача чужого проекта с plan_id нашего плана (легаси-данные)                 #
# --------------------------------------------------------------------------- #


def _leak_b_task_into_a_plan(session: Session, a: _Proj, b: _Proj) -> None:
    leaked = session.get(TaskModel, b.task_row)
    assert leaked is not None
    leaked.plan_id, leaked.section_id = a.plan, a.section
    leaked.status = TaskStatus.DONE.value
    session.flush()


def test_list_for_plan_skips_a_foreign_task_left_in_the_plan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        _leak_b_task_into_a_plan(session, a, b)
        listed = tasks.list_for_plan(session, a.plan, project_id=a.project)
        assert [t.row_id for t in listed] == [a.task_row]


def test_plan_totals_do_not_count_a_foreign_task_left_in_the_plan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Прогресс плана (views section_totals / plan_totals, миграция 0048) не
    считает чужую задачу: «1 из 1, 0 сделано», а не «2, 1 сделано»."""
    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        _leak_b_task_into_a_plan(session, a, b)
        progress = plans.recalc(session, a.plan, project_id=a.project)
        assert (progress.total, progress.done) == (1, 0)
        (section,) = progress.sections
        assert (section.total, section.done) == (1, 0)

        b_progress = plans.recalc_for_project(session, b.project)[b.plan]
        assert b_progress.total == 0, "задача, ушедшая из плана B, там тоже не считается"


# --------------------------------------------------------------------------- #
# approval_service.request: linked_task_refs                                   #
# --------------------------------------------------------------------------- #


def test_approval_task_refs_touch_only_tasks_of_the_requesting_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Решение AFT-024: ``linked_task_refs`` — свободные ссылки (контракт
    разрешает ещё не существующие ID), не валидируются. Но побочный эффект
    — перевод задачи в ``in_review`` — касается только задачи проекта
    одобрения: одноимённая задача соседа остаётся ``in_progress``, а ссылка
    на ID, которого в проекте нет, записывается как есть и ничего не меняет."""
    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        for p in (a, b):
            checkout_service.checkout(session, _TID, agent="agent:x", project_id=p.project)
        only_in_a = tasks.create(
            session,
            project_id=a.project,
            plan_id=a.plan,
            section_id=a.section,
            task_id="ONLY-001",
            title="Only in A",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author=_AUTHOR,
        )
        checkout_service.checkout(session, "ONLY-001", agent="agent:x", project_id=a.project)
        assert only_in_a.row_id is not None

        approval = approval_service.request(
            session,
            b.project,
            approval_type="manual",
            requested_by=_AUTHOR,
            linked_task_refs=[_TID, "ONLY-001"],
        )

    assert approval.linked_task_refs == [_TID, "ONLY-001"]
    with transactional(factory) as session:
        task_a = tasks.get(session, _TID, project_id=a.project)
        task_b = tasks.get(session, _TID, project_id=b.project)
        only = tasks.get(session, "ONLY-001", project_id=a.project)
        assert task_a is not None and task_b is not None and only is not None
        assert task_b.status is TaskStatus.IN_REVIEW, "задача проекта одобрения — в ревью"
        assert task_a.status is TaskStatus.IN_PROGRESS_NEW, "одноимённая задача соседа не тронута"
        assert only.status is TaskStatus.IN_PROGRESS_NEW, "ссылка мимо проекта ничего не меняет"


# --------------------------------------------------------------------------- #
# link_service.semantic: предложения ссылок (ai-review PR #200)               #
# --------------------------------------------------------------------------- #


def test_link_suggestion_of_another_project_cannot_be_read_or_resolved(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Предложение ссылки из секции проекта A: из проекта B его не видно ни
    чтением, ни списком, а reject/accept по ``row_id`` — «не найдено» и
    состояние не меняют."""
    from cod_doc.infra.models.link_suggestions import LinkSuggestionModel
    from cod_doc.services.link_service import semantic

    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        sugg = LinkSuggestionModel(
            from_section_id=a.doc_section, to_doc_key="guide", score=0.9, state="pending"
        )
        session.add(sugg)
        session.flush()
        sugg_id = sugg.row_id

    with transactional(factory) as session:
        assert semantic.get_suggestion(session, sugg_id, project_id=b.project) is None
        assert semantic.list_suggestions(session, project_id=b.project) == []
        assert (
            semantic.list_suggestions_for_document(session, project_id=b.project, document_id=a.doc)
            == []
        )
        for state in ("rejected", "accepted"):
            assert not semantic.update_suggestion_state(
                session, sugg_id, state, project_id=b.project
            )
        # Генерация по секции соседа отказывает до обращения к индексу
        # эмбеддингов — конфиг не нужен, и ничего не пишется.
        no_config: Any = None
        assert (
            semantic.suggest_for_section(
                session, a.doc_section, no_config, project_id=b.project, dry_run=False
            )
            == []
        )

    with transactional(factory) as session:
        own = semantic.get_suggestion(session, sugg_id, project_id=a.project)
        assert own is not None and own["state"] == "pending"
        assert [
            s["row_id"]
            for s in semantic.list_suggestions_for_document(
                session, project_id=a.project, document_id=a.doc
            )
        ] == [sugg_id]
        assert [s["row_id"] for s in semantic.list_suggestions(session, project_id=a.project)] == [
            sugg_id
        ]
        assert semantic.update_suggestion_state(session, sugg_id, "rejected", project_id=a.project)


# --------------------------------------------------------------------------- #
# render_body и comment_service (ai-review PR #200)                            #
# --------------------------------------------------------------------------- #


def test_render_body_hides_another_projects_document(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        assert doc_service.render_body(session, a.doc, project_id=b.project) is None
        own = doc_service.render_body(session, a.doc, project_id=a.project)
        assert own is not None and "body of a" in own


def test_comments_of_another_project_cannot_be_read_changed_or_deleted(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Комментарий к документу проекта A: из проекта B его не видно, статус не
    меняется, удалить нельзя; на документ A из B комментарий не создать."""
    from cod_doc.services import comment_service as comments

    factory, a, b = _two_projects(engine_with_schema)
    with transactional(factory) as session:
        comment = comments.create(
            session, project_id=a.project, document_id=a.doc, body="fix this", author=_AUTHOR
        )
        cid = comment.row_id
        assert cid is not None

    with transactional(factory) as session:
        assert comments.get(session, cid, project_id=b.project) is None
        assert comments.list_for_document(session, a.doc, project_id=b.project) == []
        with pytest.raises(comments.CommentNotFoundError):
            comments.update_status(
                session, project_id=b.project, comment_id=cid, new_status="resolved"
            )
        assert comments.delete(session, cid, project_id=b.project) is False
        with pytest.raises(ValueError, match="not found"):
            comments.create(
                session, project_id=b.project, document_id=a.doc, body="intruder", author=_AUTHOR
            )

    with transactional(factory) as session:
        own = comments.get(session, cid, project_id=a.project)
        assert own is not None and own.status == "open"
        assert [
            c.row_id for c in comments.list_for_document(session, a.doc, project_id=a.project)
        ] == [cid]
