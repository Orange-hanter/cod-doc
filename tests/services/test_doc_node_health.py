"""Здоровье разделов: детерминированные пробелы в корпусе.

Правила калибровались замером на живом корпусе cod-doc (170 документов) и там
**молчат**: все `min_docs` удовлетворены, `intent` заполнен у всех разделов,
доля самого частого типа 46% при пороге 60%, одноимённых индексов максимум два
при пороге три. Это и есть правильная калибровка — правило срабатывает на
вырожденном дереве, а не на рабочем.

Поэтому проверяется здесь синтетическое дерево: только на нём видно, что
правило вообще умеет срабатывать.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import DocNode, DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import doc_node_health as health
from cod_doc.services import doc_tree_service as tree
from cod_doc.services.doc_tree_service import NodeStat

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _node(key: str, *, intent: str = "что тут лежит", min_docs: int = 0) -> DocNode:
    return DocNode(
        project_id=1,
        node_key=key,
        title=key.title(),
        position=0,
        intent=intent,
        min_docs=min_docs,
    )


def _stat(key: str, count: int, *, intent: str = "что тут лежит", min_docs: int = 0) -> NodeStat:
    node = _node(key, intent=intent, min_docs=min_docs)
    return NodeStat(node=node, doc_count=count, under_filled=count < min_docs)


# ------------------------------------------------------------------ #
# Уровень раздела                                                     #
# ------------------------------------------------------------------ #


def test_required_but_empty_section_is_major() -> None:
    issues = health.assess_nodes([_stat("vision", 0, min_docs=1)])
    assert len(issues) == 1
    assert issues[0].severity == "major"
    assert issues[0].scope_id == "vision"
    assert "пуст" in issues[0].body


def test_thin_section_is_minor() -> None:
    issues = health.assess_nodes([_stat("vision", 1, min_docs=3)])
    assert len(issues) == 1
    assert issues[0].severity == "minor"
    assert "min_docs=3" in issues[0].body


def test_optional_empty_section_is_silent() -> None:
    """`min_docs=0` значит «раздел может быть пустым законно» — аудит в новом
    проекте отчётов ещё не накопил, и требовать их было бы шумом."""
    assert health.assess_nodes([_stat("audit", 0)]) == []


def test_missing_intent_is_reported() -> None:
    issues = health.assess_nodes([_stat("audit", 5, intent="   ")])
    assert len(issues) == 1
    assert "intent" in issues[0].body


def test_one_issue_per_section_not_per_condition() -> None:
    """Пустой раздел без intent — одна находка, а не две.

    Действие у них общее («наполнить раздел»), а по строке на условие очередь
    куратора уже однажды раздували — см. Инбокс.
    """
    issues = health.assess_nodes([_stat("vision", 0, intent="", min_docs=1)])
    assert len(issues) == 1
    assert "пуст" in issues[0].body
    assert "intent" in issues[0].body


def test_healthy_section_is_silent() -> None:
    assert health.assess_nodes([_stat("vision", 3, min_docs=1)]) == []


# ------------------------------------------------------------------ #
# Уровень корпуса                                                     #
# ------------------------------------------------------------------ #


def test_degenerate_typing_is_reported() -> None:
    """Свежий хаотичный импорт: импортёр проставил один тип всему подряд."""
    issues = health.assess_corpus(["module-spec"] * 20, {}, project_slug="p")
    codes = [i.code for i in issues]
    assert "TREE-TYPES" in codes
    assert "100%" in next(i for i in issues if i.code == "TREE-TYPES").body


def test_live_corpus_typing_stays_silent() -> None:
    """46% самого частого типа при 12 различных — посредственно, но живо.

    Замер на cod-doc; правило обязано молчать, иначе оно шумит на каждом
    рабочем проекте.
    """
    docs = ["module-spec"] * 78 + ["audit-report"] * 35 + ["other"] * 57
    assert [
        i for i in health.assess_corpus(docs, {}, project_slug="p") if i.code == "TREE-TYPES"
    ] == []


def test_small_corpus_says_nothing_about_typing() -> None:
    """Три документа одного типа — маленький проект, а не вырожденная таксономия."""
    assert health.assess_corpus(["guide"] * 3, {}, project_slug="p") == []


def test_index_siblings_reported_from_three() -> None:
    crowded = {"docs": ["a/README", "b/README", "c/README"]}
    issues = health.assess_corpus(["guide"] * 20, crowded, project_slug="p")
    readme = [i for i in issues if i.code == "TREE-README"]
    assert len(readme) == 1
    assert readme[0].scope_id == "docs"


def test_two_index_siblings_are_legitimate() -> None:
    """Порог начинается с трёх: `README` и `plugins/cod-doc/README` на живом
    корпусе — два разных законных индекса."""
    crowded = {"entry": ["README", "plugins/cod-doc/README"]}
    issues = health.assess_corpus(["guide"] * 20, crowded, project_slug="p")
    assert [i for i in issues if i.code == "TREE-README"] == []


# ------------------------------------------------------------------ #
# Сборка на БД                                                        #
# ------------------------------------------------------------------ #


@pytest.fixture
def session_factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _doc(session: Session, project_id: int, doc_key: str, doc_type: DocumentType) -> None:
    now = datetime.now(UTC)
    session.add(
        DocumentModel(
            project_id=project_id,
            doc_key=doc_key,
            path=f"{doc_key}.md",
            type=doc_type.value,
            status=DocumentStatus.DRAFT.value,
            title=doc_key,
            created=now,
            last_updated=now,
        )
    )
    session.flush()


def test_assess_is_silent_without_a_tree(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Разделов нет — вердикт бессмыслен, а не отрицателен."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _doc(session, pid, "a", DocumentType.GUIDE)

        assert health.assess(session, pid, project_slug="p") == []
        assert health.tree_is_seeded(session, pid) is False


def test_assess_finds_empty_required_sections(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")

        issues = health.assess(session, pid, project_slug="p")

        assert health.tree_is_seeded(session, pid) is True
        required = {"entry", "vision", "architecture", "data-model"}
        assert required <= {i.scope_id for i in issues}
        assert all(i.severity == "major" for i in issues if i.scope_id in required)


def test_unplaced_index_docs_are_not_blamed_on_a_section(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Неразложенный README уже виден Инбоксом; приписывать его разделу нечестно."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        for i in range(3):
            _doc(session, pid, f"loose{i}/README", DocumentType.MODULE_SPEC)

        issues = health.assess(session, pid, project_slug="p")

        assert [i for i in issues if i.code == "TREE-README"] == []


# ------------------------------------------------------------------ #
# Сшивка с находками                                                  #
# ------------------------------------------------------------------ #


def _open_findings(session: Session, project_id: int) -> list[dict]:
    from cod_doc.services import finding_service

    return finding_service.list_findings(session, project_id, status="open")


def test_sync_writes_findings(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")

        result = health.sync(session, project_id=pid, project_slug="p")
        session.flush()

        assert result.seeded is True
        assert result.issues > 0
        assert result.created == result.issues
        found = _open_findings(session, pid)
        assert len(found) == result.issues
        assert {f["source"] for f in found} == {"routine"}
        assert {f["source_ref"] for f in found} == {"doc_node_health"}


def test_sync_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Повторный прогон поднимает times_seen, а не плодит строки."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")

        first = health.sync(session, project_id=pid, project_slug="p")
        second = health.sync(session, project_id=pid, project_slug="p")
        session.flush()

        assert second.created == 0
        assert second.updated == first.issues
        assert len(_open_findings(session, pid)) == first.issues


def test_sync_closes_a_healed_section(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        health.sync(session, project_id=pid, project_slug="p")
        session.flush()
        before = len(_open_findings(session, pid))

        # Наполнить vision — его находка обязана закрыться.
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )
        result = health.sync(session, project_id=pid, project_slug="p")
        session.flush()

        assert result.resolved == 1
        assert len(_open_findings(session, pid)) == before - 1


def test_sync_does_not_reconcile_without_a_tree(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Дерева нет — пустой набор отпечатков закрыл бы всё как «вылеченное»."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        health.sync(session, project_id=pid, project_slug="p")
        session.flush()
        before = len(_open_findings(session, pid))
        assert before > 0

        from cod_doc.infra.models import DocNodeModel

        session.query(DocNodeModel).delete()
        session.flush()

        result = health.sync(session, project_id=pid, project_slug="p")
        session.flush()

        assert result == health.SyncResult(seeded=False)
        assert len(_open_findings(session, pid)) == before, "чужие находки не тронуты"


def test_sync_leaves_an_event(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        health.sync(session, project_id=pid, project_slug="p")
        session.flush()

        from cod_doc.infra.models import ActivityEventModel

        kinds = {e.kind for e in session.query(ActivityEventModel).all()}
        assert "doc.health_synced" in kinds
