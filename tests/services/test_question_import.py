"""OQM-006: importing legacy ``type: open-question`` documents into questions.

Fixtures mirror the two shapes found in the Restate corpus (2026-09): one
document per question, and a registry of many ``### OQ-NNN`` items.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.domain.entities import (
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
)
from cod_doc.services import import_service, question_service, task_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

AUTHOR = "human:test"
BASE = "docs/billing"
SINGLE_KEY = f"{BASE}/open-questions/oq-ru-provider"
REGISTRY_KEY = "docs/Open Questions"

SINGLE = """---
type: open-question
status: active
owner: architecture-team
decision_owner: bizdev + architecture
blocking: ["BIL-003 (adapter)", "BILL-G05 (legacy id, not a task)"]
---

# OQ — RU Payment Provider Selection

> **Status:** OPEN — to be decided before Phase 2.
> **Blocks:** Phase 2 RU integration (BIL-004).

## Navigation

- [RU Adaptation](../ru-adaptation.md)
- [Audit F-1](../audit.md#f-1)
- [ЮKassa](https://yookassa.ru/)
- [Missing](../nowhere.md)

## Question

**Какого RU payment provider выбираем primary?**

## Options

Intro before options.

### Option A: ЮKassa primary

+ СБП из коробки

### Option B: CloudPayments primary

- дешевле

## Recommendation

**Primary: ЮKassa.**

## Решение

Пересмотрено: провайдер выбран вне этого документа.
"""

NESTED = """---
type: open-question
status: active
owner: architecture-team
---

# OQ-110 — Модель commission_split

## Вопрос и ограничения

## Question

**Заводит ли M4 модель сплита?**

## Constraints

Колонки нет.

## Варианты

### Вариант A — вне платформы

Ничего не хранить.

### Вариант B — jsonb

Хранить jsonb.
"""

REGISTRY = """---
type: open-question
status: active
owner: Product Team
---

# Open Questions

## Summary Table

| ID | Вопрос | Модуль-владелец | Источники | Блокирует задач | Статус |
| :--- | :--- | :--- | :--- | :--- | :--- |
| OQ-001 | Какие поля при регистрации? | M1 AUTH | x.md | BIL-003 (unblocked), BIL-004 | Resolved |
| OQ-004 | Как устроена монетизация? | M1 Billing | y.md | BIL-003 | Open |

## Open Items

### OQ-004 Monetization model

- Primary owner: M1 Billing
- Needed decision: pricing entities.

### OQ-006 LLM tokens in subscription?

- Статус: Open

## Resolved Archive

### OQ-001 Registration fields

**Resolution:** company_name, email, phone.
"""


@pytest.fixture
def factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _seed(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="oq", title="P", root_path=str(root), config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    pid = proj.row_id
    plan = PlanModel(project_id=pid, scope="oq-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
    session.add(sec)
    session.flush()
    for tid in ("BIL-003", "BIL-004"):
        task_service.create(
            session,
            project_id=pid,
            plan_id=plan.row_id,
            section_id=sec.row_id,
            task_id=tid,
            title=f"task {tid}",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author=AUTHOR,
        )
    docs = {
        f"{BASE}/ru-adaptation": "---\ntype: module-spec\n---\n# RU\n\n## Intro\n\nx\n",
        f"{BASE}/audit": "---\ntype: module-spec\n---\n# Audit\n\n## F-1\n\nx\n",
        SINGLE_KEY: SINGLE,
        f"{BASE}/open-questions/oq-110": NESTED,
        REGISTRY_KEY: REGISTRY,
    }
    for key, raw in docs.items():
        path = f"{key}.md"
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(raw, encoding="utf-8")
        import_service.import_markdown(
            session, project_id=pid, doc_key=key, raw_markdown=raw, path=path, author=AUTHOR
        )
    return pid


def _edges(session: Session, qid: str, pid: int) -> set[tuple[str, str, str]]:
    q = question_service.get(session, pid, qid)
    assert q is not None and q.row_id is not None
    return {
        (e.to_kind.value, e.to_ref, e.relation.value)
        for e in question_service.list_links(session, q.row_id)
    }


def test_single_document_maps_question_options_links_and_context(  # type: ignore[no-untyped-def]
    factory, tmp_path: Path
) -> None:
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        result = question_service.import_document(
            session, project_id=pid, doc_key=SINGLE_KEY, author=AUTHOR
        )
        (qid,) = result.created
        q = question_service.get(session, pid, qid)
        assert q is not None and q.row_id is not None
        options = question_service.list_options(session, q.row_id)
        edges = _edges(session, qid, pid)
        doc_left = session.execute(
            select(func.count())
            .select_from(DocumentModel)
            .where(DocumentModel.doc_key == SINGLE_KEY)
        ).scalar_one()

    assert result.plan.mode == "single"
    assert q.title == "RU Payment Provider Selection"
    assert q.question == "**Какого RU payment provider выбираем primary?**"
    assert q.owner == "bizdev + architecture"
    assert q.source_doc_key == SINGLE_KEY
    assert q.status is QuestionStatus.OPEN
    assert [(o.title, o.body) for o in options] == [
        ("Option A: ЮKassa primary", "+ СБП из коробки"),
        ("Option B: CloudPayments primary", "- дешевле"),
    ]
    assert q.context is not None
    for fragment in ("OPEN — to be decided", "Intro before options.", "**Primary: ЮKassa.**"):
        assert fragment in q.context
    assert edges == {
        ("document", f"{BASE}/ru-adaptation", "see_also"),
        ("section", f"{BASE}/audit#f-1", "see_also"),
        ("url", "https://yookassa.ru/", "see_also"),
        ("task", "BIL-003", "blocks"),
        ("task", "BIL-004", "blocks"),
    }
    assert result.plan.skipped_links == ["../nowhere.md"]
    assert any("Решение" in w for w in result.plan.questions[0].warnings)
    assert doc_left == 0
    assert result.document_deleted and result.file_deleted
    assert not (tmp_path / f"{SINGLE_KEY}.md").exists()


def test_nested_headings_are_split_like_sections(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        result = question_service.import_document(
            session, project_id=pid, doc_key=f"{BASE}/open-questions/oq-110", author=AUTHOR
        )
        q = question_service.get(session, pid, result.created[0])
        assert q is not None and q.row_id is not None
        options = question_service.list_options(session, q.row_id)
    assert q.title == "OQ-110 — Модель commission_split"
    assert q.question == "**Заводит ли M4 модель сплита?**"
    assert [o.title for o in options] == ["Вариант A — вне платформы", "Вариант B — jsonb"]
    assert q.context is not None and "Колонки нет." in q.context


def test_registry_becomes_one_question_per_item(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        result = question_service.import_document(
            session, project_id=pid, doc_key=REGISTRY_KEY, author=AUTHOR
        )
        found = {
            q.title: q
            for q in question_service.list_for_project(session, pid)
            if q.source_doc_key == REGISTRY_KEY
        }
        edges = {title: _edges(session, q.question_id, pid) for title, q in found.items()}

    assert result.plan.mode == "registry"
    assert len(result.created) == 3
    monet = found["OQ-004 Monetization model"]
    assert monet.question == "Как устроена монетизация?"
    assert monet.owner == "M1 Billing"
    assert monet.status is QuestionStatus.OPEN
    assert monet.context is not None and "pricing entities" in monet.context
    assert edges["OQ-004 Monetization model"] == {("task", "BIL-003", "blocks")}

    llm = found["OQ-006 LLM tokens in subscription?"]
    assert llm.status is QuestionStatus.OPEN
    assert llm.question == "OQ-006 LLM tokens in subscription?"[len("OQ-006 ") :]

    reg = found["OQ-001 Registration fields"]
    assert reg.status is QuestionStatus.RESOLVED
    assert reg.resolution is not None and "company_name" in reg.resolution
    # "(unblocked)" in the table means the task no longer waits on the answer
    assert edges["OQ-001 Registration fields"] == {("task", "BIL-004", "blocks")}


def test_dry_run_writes_nothing_and_reimport_is_refused(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        plan = question_service.import_document(
            session, project_id=pid, doc_key=SINGLE_KEY, author=AUTHOR, dry_run=True
        )
        assert plan.created == []
        assert question_service.list_for_project(session, pid) == []
        assert (tmp_path / f"{SINGLE_KEY}.md").exists()

        kept = question_service.import_document(
            session, project_id=pid, doc_key=SINGLE_KEY, author=AUTHOR, delete_document=False
        )
        assert not kept.document_deleted
        assert (tmp_path / f"{SINGLE_KEY}.md").exists()
        with pytest.raises(question_service.AlreadyImportedError):
            question_service.import_document(
                session, project_id=pid, doc_key=SINGLE_KEY, author=AUTHOR
            )
        with pytest.raises(question_service.NotAQuestionDocumentError):
            question_service.import_document(
                session, project_id=pid, doc_key=f"{BASE}/audit", author=AUTHOR
            )
        events = session.execute(
            select(func.count())
            .select_from(ActivityEventModel)
            .where(ActivityEventModel.kind == "question.imported")
        ).scalar_one()
    assert events == 1


def test_imported_links_verify_against_the_project(factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with transactional(factory) as session:
        pid = _seed(session, tmp_path)
        question_service.import_document(session, project_id=pid, doc_key=SINGLE_KEY, author=AUTHOR)
        report = question_service.verify_links(session, project_id=pid)
    assert (report.broken, report.unchecked) == (0, 1)


def test_link_kinds_used_by_import_are_valid_shapes() -> None:
    for kind, ref in (
        (QuestionLinkKind.SECTION, f"{BASE}/audit#f-1"),
        (QuestionLinkKind.TASK, "BIL-003"),
        (QuestionLinkKind.URL, "https://yookassa.ru/"),
    ):
        question_service.validate_ref(kind, ref)
    assert QuestionRelation.SEE_ALSO.value == "see_also"
