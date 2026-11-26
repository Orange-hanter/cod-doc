"""REL-007: удаление строки сущности публикует `EntityDeleted` (RFC 39 §3.5).

Рёбер на концах нет FK, каскада тоже: каждый путь, удаляющий строку сущности
(`RefKind`), обязан сообщить об этом, иначе ребро повиснет на `uid`
несуществующей строки. Сканер находит в `cod_doc/services` функции с
`session.delete(...)` и `delete(XModel)` (AST, как сканер паритета
поверхностей) и требует либо публикации, либо записи в `NOT_ENTITY` с
обоснованием. Список — храповик: запись, у которой функции или удаления уже
нет, роняет тест, так что он только сокращается.
"""

from __future__ import annotations

import ast

import pytest

from cod_doc.domain.relations import RefKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.services import doc_events, entity_events
from cod_doc.services import doc_service as docs
from tests.services._surface_parity import MIN_REASON_LEN, SERVICES_DIR, _dotted

PUBLISH_CALLS = {"publish_deleted", "entity_events.publish_deleted"}

#: «путь::функция» → почему удаляемые строки — не вид `RefKind` (рёбер на них быть не может).
NOT_ENTITY: dict[str, str] = {
    "access_service.py::remove_member": "членство в проекте — хранилище доступа сервера, не сущность",
    "adr_service.py::unrelate": "ребро adr_relation — не вид RefKind (бэкенд, REL-017)",
    "adr_topic_service.py::delete": "полка реестра ADR — не вид RefKind, на неё рёбер нет",
    "comment_service.py::delete": "комментарий к секции — не вид RefKind, рёбер на него нет",
    "doc_tree_service.py::delete_node": "раздел дерева документации — не вид RefKind",
    "relation_service/cleanup.py::on_entity_deleted": "сам подписчик: снимает рёбра relation",
    "relation_service/edges.py::unrelate": "само ребро relation — не вид RefKind",
    "relation_service/verify.py::_fill": "дубль ребра relation при дописывании to_uid",
    "repo_index_service.py::scan_project": "derived-индекс файлов репозитория, не сущность",
    "routine_service.py::delete": "рутина куратора — не вид RefKind, рёбер на неё нет",
    "scenario_service/links.py::unlink": "привязка сценария (scenario_link) — не вид RefKind",
    "scenario_service/steps.py::set_steps": "шаг сценария — не вид RefKind, рёбер на него нет",
    "structure_service.py::_clear_indexes": "derived-индекс структуры кода, не сущность проекта",
    "structure_service.py::_gc_branch": "derived-снимки структуры кода, не сущность проекта",
    "sync_service/checkout_lease.py::unmark": "строки sync_ref — состояние реплики, не сущность",
    "sync_service/epoch_rebase.py::prepare": "пересборка из снапшота эпохи: рёбра едут в нём же",
    "sync_service/merge.py::_trim": "строки журнала sync_op, не сущности проекта",
    "sync_service/set_merge.py::_drop_pending": "строки журнала sync_op, не сущности проекта",
    "sync_service/tombstone.py::before_delete": "снимок поддерева в конфликт; удаляет apply._apply_delete",
    "task_service.py::remove_dependency": "ребро dependency — не вид RefKind (бэкенд, REL-017)",
    "task_service.py::update_affects_files": "affected_file — строка-связь задачи, не вид RefKind",
}


def _is_delete_call(node: ast.Call) -> bool:
    name = _dotted(node.func)
    if name.endswith("session.delete") and len(node.args) == 1:
        return True
    return (
        name == "delete"
        and bool(node.args)
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id.endswith("Model")
    )


def _scan() -> tuple[dict[str, bool], set[str]]:
    """«путь::функция» с удалением → публикует ли (сама или через module-local хелпер)."""
    found: dict[str, bool] = {}
    for path in sorted(SERVICES_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SERVICES_DIR).as_posix()
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        calls = {fn.name: [n for n in ast.walk(fn) if isinstance(n, ast.Call)] for fn in fns}
        direct = {
            name for name, cs in calls.items() if any(_dotted(c.func) in PUBLISH_CALLS for c in cs)
        }
        for name, cs in calls.items():
            if any(_is_delete_call(c) for c in cs):
                found[f"{rel}::{name}"] = name in direct or any(
                    _dotted(c.func) in direct for c in cs
                )
    return found, set(found)


def test_every_entity_delete_publishes() -> None:
    found, _ = _scan()
    unpublished = sorted(k for k, ok in found.items() if not ok and k not in NOT_ENTITY)
    assert unpublished == [], (
        "функции удаляют строки и не публикуют EntityDeleted; если строки — не вид "
        f"RefKind, внеси в NOT_ENTITY с обоснованием: {unpublished}"
    )


def test_scanner_sees_the_known_publishers() -> None:
    found, _ = _scan()
    for known in (
        "doc_service.py::delete",
        "doc_service.py::delete_section",
        "question_service/crud.py::delete",
        "plan_service/sections.py::delete_section",
        "scenario_service/export.py::_sync_sections",
        "sync_service/apply.py::_apply_delete",
    ):
        assert found.get(known) is True, known


def test_allowlist_is_a_ratchet() -> None:
    found, _ = _scan()
    stale = sorted(k for k in NOT_ENTITY if k not in found or found[k])
    assert stale == [], f"записи NOT_ENTITY без удаления или с публикацией: {stale}"
    assert all(len(r) >= MIN_REASON_LEN for r in NOT_ENTITY.values())


def test_entity_tables_match_ref_kinds() -> None:
    assert {RefKind(k) for k in entity_events.KIND_BY_TABLE.values()} == {
        k for k in RefKind if k not in {RefKind.CODE, RefKind.URL}
    }


def test_publish_without_subscriber_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc_events, "_HANDLERS", {})
    with pytest.raises(entity_events.MissingSubscriberError):
        entity_events.publish_deleted(
            None,  # type: ignore[arg-type]
            1,
            RefKind.SECTION,
            "u",
            key=None,
            author="t",
        )


def test_document_delete_removes_edges_of_document_and_sections(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import func, select

    from cod_doc.infra.models import ActivityEventModel, DocumentModel, RelationModel, SectionModel
    from tests.services.test_doc_rename_section import _seed
    from tests.services.test_relation_service import _uid

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        _seed(s)
        doc = s.scalars(select(DocumentModel)).first()
        sec = s.scalars(select(SectionModel)).first()
        assert doc is not None
        assert sec is not None
        doc.uid, sec.uid = doc.uid or _uid(), sec.uid or _uid()
        pid = doc.project_id
        # Рёбра пишем напрямую: источник-вопрос — произвольный uid, сущность не нужна.
        for kind, uid in (("document", doc.uid), ("section", sec.uid)):
            s.add(
                RelationModel(
                    project_id=pid,
                    from_kind="question",
                    from_uid="q-uid",
                    type="about",
                    to_kind=kind,
                    to_uid=uid,
                    author="t",
                )
            )
        s.add(
            RelationModel(
                project_id=pid,
                from_kind="document",
                from_uid=doc.uid,
                type="specified_in",
                to_kind="code",
                to_key="a.py",
                author="t",
            )
        )
        s.flush()
        docs.delete(s, project_id=pid, doc_key=doc.doc_key, author="t")
        assert s.scalar(select(func.count()).select_from(RelationModel)) == 0
        removed = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "relation.removed")
        ).all()
        assert len(removed) == 3
        assert {e.payload["reason"] for e in removed} == {"endpoint_deleted"}
