"""Дерево документации: разделы и раскладка документов по ним.

ADO-116. До этого сервиса иерархии документов в БД не было: экран строил
дерево из ``doc_key.split("/")``, то есть показывал файловую кучу. Здесь она
становится хранимой сущностью — с ключом, названием, порядком и намерением.

Разделение ответственности:

* :mod:`cod_doc.services.doc_taxonomy` — чистое описание дефолтного дерева и
  правил раскладки, без БД;
* этот модуль — всё, что пишет.

Мутации пишут revision и activity event по ADO-040. Адрес ревизии раздела —
``EntityKind.DOC_NODE``: у ``doc_node`` своя нумерация ``row_id``, и писать её
под ``DOCUMENT`` нельзя — раздел ``row_id=1`` сел бы в историю документа
``row_id=1``. Привязка документа к разделу — наоборот, ревизия документа: это
изменение документа, а не раздела.

Caller owns the transaction.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, NamedTuple

from cod_doc.domain.entities import DocNode, DocumentType, EntityKind
from cod_doc.infra.models import DocNodeModel, DocumentModel
from cod_doc.infra.repositories import DocNodeRepository, DocumentRepository
from cod_doc.services import activity_service, doc_taxonomy, validation
from cod_doc.services import revision_service as rev
from cod_doc.services.doc_taxonomy import DEFAULT_RULES, DEFAULT_TREE, ClassifyReport, Placement

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session


class NodeNotFoundError(LookupError):
    """Раздел с таким ключом в проекте не заведён."""

    def __init__(self, node_key: str) -> None:
        super().__init__(f"doc node not found: {node_key!r}")
        self.node_key = node_key


class NodeAlreadyExistsError(ValueError):
    """Раздел с таким ключом уже есть — ключ уникален в пределах проекта."""

    def __init__(self, node_key: str) -> None:
        super().__init__(f"doc node already exists: {node_key!r}")
        self.node_key = node_key


class NodeHasDocumentsError(ValueError):
    """Удаление раздела, в котором лежат документы, требует явного согласия."""

    def __init__(self, node_key: str, count: int) -> None:
        super().__init__(
            f"doc node {node_key!r} still holds {count} document(s); "
            "pass reassign_to=<node_key> or force=True"
        )
        self.node_key = node_key
        self.count = count


class NodeStat(NamedTuple):
    """Раздел вместе с тем, что о нём нужно знать экрану и куратору."""

    node: DocNode
    doc_count: int
    #: Ниже порога ``min_docs``: раздел объявлен обязательным и не наполнен.
    under_filled: bool


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields}, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# Чтение                                                                       #
# --------------------------------------------------------------------------- #


def list_nodes(session: Session, project_id: int) -> list[DocNode]:
    """Разделы проекта в порядке показа."""
    return DocNodeRepository(session).list_for_project(project_id)


def get_node(session: Session, project_id: int, node_key: str) -> DocNode | None:
    return DocNodeRepository(session).get_by_key(project_id, node_key)


#: Потолок обхода ``parent_id`` в ``node_ancestors``. Это страховка от циклов
#: в данных, а не ограничение дерева: ``create_node`` глубину не ограничивает.
#: При глубине сверх лимита цепочка молча обрезается со стороны корня.
_ANCESTOR_DEPTH_LIMIT = 8


def node_ancestors(nodes: Sequence[DocNode], node_key: str) -> list[DocNode]:
    """Цепочка разделов от корня до ``node_key`` включительно.

    Нужна крошкам экрана документации: рельс показывает разделы плоско, а
    дерево вложенное, и без цепочки пользователь не видит, где он. Неизвестный
    ключ (например, псевдопункт рельса) даёт пустой список. Обход по
    ``parent_id`` страхуется от циклов и ограничен ``_ANCESTOR_DEPTH_LIMIT``.

    Принимает уже загруженный список разделов: у вызывающих экранов он и так
    есть, а повторный запрос был бы тем самым N+1, против которого написан
    ``node_stats``.
    """
    by_id = {n.row_id: n for n in nodes if n.row_id is not None}
    by_key = {n.node_key: n for n in by_id.values()}

    chain: list[DocNode] = []
    seen: set[int] = set()
    current = by_key.get(node_key)
    while (
        current is not None
        and current.row_id is not None
        and current.row_id not in seen
        and len(chain) < _ANCESTOR_DEPTH_LIMIT
    ):
        chain.append(current)
        seen.add(current.row_id)
        current = by_id.get(current.parent_id) if current.parent_id is not None else None
    chain.reverse()
    return chain


def inbox_node(session: Session, project_id: int) -> DocNode | None:
    """Раздел-инбокс проекта, если дерево засеяно."""
    return DocNodeRepository(session).inbox(project_id)


def node_stats(session: Session, project_id: int) -> list[NodeStat]:
    """Разделы со счётчиками — один агрегат на весь экран, а не N+1.

    Инбокс считается по ``node_id IS NULL``, а не по ссылкам на его строку.
    «Не разложен» — одно состояние с одним представлением: иначе документ мог
    бы оказаться в Инбоксе двумя способами (NULL или явная привязка), и любой
    счётчик пришлось бы складывать из двух источников.
    """
    repo = DocNodeRepository(session)
    counts = repo.counts_by_node(project_id)
    out: list[NodeStat] = []
    for node in repo.list_for_project(project_id):
        count = counts.get(None, 0) if node.is_inbox else counts.get(node.row_id, 0)
        out.append(NodeStat(node=node, doc_count=count, under_filled=count < node.min_docs))
    return out


def unplaced_count(session: Session, project_id: int) -> int:
    """Сколько документов ещё не разложено.

    Считается по ``node_id IS NULL``, а не по содержимому Инбокса: документ
    без раздела не разложен, даже если раздела-инбокса в проекте нет вовсе.
    """
    return DocNodeRepository(session).counts_by_node(project_id).get(None, 0)


def unplaced(session: Session, project_id: int) -> list[str]:
    """Ключи неразложенных документов — то, что показывает Инбокс."""
    docs = DocumentRepository(session).list_for_project(project_id)
    return [d.doc_key for d in docs if d.node_id is None]


# --------------------------------------------------------------------------- #
# Мутации: разделы                                                             #
# --------------------------------------------------------------------------- #


def create_node(
    session: Session,
    *,
    project_id: int,
    node_key: str,
    title: str,
    author: str,
    intent: str = "",
    parent_key: str | None = None,
    position: int | None = None,
    expected_types: list[str] | None = None,
    min_docs: int = 0,
    is_inbox: bool = False,
    reason: str | None = None,
) -> DocNode:
    """Завести раздел. ``position`` по умолчанию — в конец списка."""
    validation.validate_doc_node_key(node_key)
    validation.validate_doc_node_title(title)
    if position is not None:
        validation.validate_doc_node_position(position)

    repo = DocNodeRepository(session)
    if repo.get_by_key(project_id, node_key) is not None:
        raise NodeAlreadyExistsError(node_key)

    parent_id: int | None = None
    if parent_key is not None:
        parent = repo.get_by_key(project_id, parent_key)
        if parent is None:
            raise NodeNotFoundError(parent_key)
        parent_id = parent.row_id

    node = repo.add(
        DocNode(
            project_id=project_id,
            node_key=node_key,
            parent_id=parent_id,
            title=title,
            intent=intent,
            position=position if position is not None else repo.next_position(project_id),
            expected_types=list(expected_types or []),
            min_docs=min_docs,
            is_inbox=is_inbox,
        )
    )
    session.flush()
    assert node.row_id is not None

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.DOC_NODE,
        entity_id=node.row_id,
        author=author,
        diff=_diff("create_node", node_key=node_key, title=title, position=node.position),
        reason=reason or "create_node",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "doc.node_created",
        author,
        scope_kind=EntityKind.DOC_NODE.value,
        scope_id=node_key,
        payload={"node_key": node_key, "title": title, "position": node.position},
        summary=f"Doc node {node_key} created",
    )
    return node


def update_node(
    session: Session,
    *,
    project_id: int,
    node_key: str,
    author: str,
    title: str | None = None,
    intent: str | None = None,
    position: int | None = None,
    min_docs: int | None = None,
    reason: str | None = None,
) -> DocNode:
    """Поправить раздел. ``None`` означает «не трогать это поле».

    Идемпотентно: вызов, который ничего не меняет, не пишет ни ревизию, ни
    событие — как ``doc_service.update_status``.
    """
    if title is not None:
        validation.validate_doc_node_title(title)
    if position is not None:
        validation.validate_doc_node_position(position)

    model = _require_node_model(session, project_id, node_key)
    changes: dict[str, object] = {}
    if title is not None and title != model.title:
        changes["title"] = {"from": model.title, "to": title}
        model.title = title
    if intent is not None and intent != model.intent:
        changes["intent"] = {"from": model.intent, "to": intent}
        model.intent = intent
    if position is not None and position != model.position:
        changes["position"] = {"from": model.position, "to": position}
        model.position = position
    if min_docs is not None and min_docs != model.min_docs:
        changes["min_docs"] = {"from": model.min_docs, "to": min_docs}
        model.min_docs = min_docs

    repo = DocNodeRepository(session)
    if not changes:
        current = repo.get_by_key(project_id, node_key)
        assert current is not None
        return current

    session.flush()
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.DOC_NODE,
        entity_id=model.row_id,
        author=author,
        diff=_diff("update_node", node_key=node_key, changes=changes),
        reason=reason or "update_node",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "doc.node_updated",
        author,
        scope_kind=EntityKind.DOC_NODE.value,
        scope_id=node_key,
        payload={"node_key": node_key, "changed": sorted(changes)},
        summary=f"Doc node {node_key} updated: {', '.join(sorted(changes))}",
    )
    updated = repo.get_by_key(project_id, node_key)
    assert updated is not None
    return updated


def delete_node(
    session: Session,
    *,
    project_id: int,
    node_key: str,
    author: str,
    reassign_to: str | None = None,
    force: bool = False,
    reason: str | None = None,
) -> int:
    """Удалить раздел; возвращает число осиротевших/перенесённых документов.

    Непустой раздел удаляется только осознанно: либо ``reassign_to`` переносит
    его документы, либо ``force`` отправляет их в Инбокс. Молча растворить
    полку с документами нельзя — это ровно тот способ, которым документация
    теряется.
    """
    model = _require_node_model(session, project_id, node_key)
    docs = (
        session.query(DocumentModel)
        .filter(DocumentModel.project_id == project_id, DocumentModel.node_id == model.row_id)
        .all()
    )
    if docs and not force and reassign_to is None:
        raise NodeHasDocumentsError(node_key, len(docs))

    target_id: int | None = None
    if reassign_to is not None:
        target = DocNodeRepository(session).get_by_key(project_id, reassign_to)
        if target is None:
            raise NodeNotFoundError(reassign_to)
        # Инбокс — это NULL, а не ссылка на его строку; та же нормализация,
        # что в ``assign``. Без неё документы уезжали на строку Инбокса и
        # пропадали из всех счётчиков разом: рельс считает Инбокс по
        # ``node_id IS NULL`` и таких строк не видит.
        target_id = None if target.is_inbox else target.row_id

    moved = len(docs)
    for doc in docs:
        # ``last_updated`` не трогаем по той же причине, что в ``assign``:
        # перенос между разделами не правит содержимое документа.
        doc.node_id = target_id

    session.delete(model)
    session.flush()

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.DOC_NODE,
        entity_id=model.row_id,
        author=author,
        diff=_diff("delete_node", node_key=node_key, moved=moved, reassign_to=reassign_to),
        reason=reason or "delete_node",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "doc.node_deleted",
        author,
        scope_kind=EntityKind.DOC_NODE.value,
        scope_id=node_key,
        payload={"node_key": node_key, "moved": moved, "reassign_to": reassign_to},
        summary=f"Doc node {node_key} deleted ({moved} docs moved)",
    )
    return moved


def init_tree(
    session: Session,
    *,
    project_id: int,
    author: str,
    tree: tuple[doc_taxonomy.NodeSpec, ...] = DEFAULT_TREE,
    reason: str | None = None,
) -> list[DocNode]:
    """Засеять дефолтное дерево. Идемпотентно: существующие ключи не трогает.

    Возвращает только созданные разделы — по пустому списку видно, что дерево
    уже было.
    """
    repo = DocNodeRepository(session)
    created: list[DocNode] = []
    for position, spec in enumerate(tree):
        if repo.get_by_key(project_id, spec.node_key) is not None:
            continue
        created.append(
            create_node(
                session,
                project_id=project_id,
                node_key=spec.node_key,
                title=spec.title,
                intent=spec.intent,
                position=position,
                expected_types=list(spec.expected_types),
                min_docs=spec.min_docs,
                is_inbox=spec.is_inbox,
                author=author,
                reason=reason or "init_tree",
            )
        )
    return created


# --------------------------------------------------------------------------- #
# Мутации: раскладка документов                                                #
# --------------------------------------------------------------------------- #


def assign(
    session: Session,
    *,
    project_id: int,
    doc_key: str,
    node_key: str | None,
    author: str,
    position: int | None = None,
    reason: str | None = None,
) -> DocNode | None:
    """Положить документ в раздел; ``node_key=None`` — вынуть в Инбокс.

    Возвращает раздел, в котором документ оказался после вызова. Идемпотентно:
    повторная привязка к тому же разделу не пишет ни ревизию, ни событие.
    """
    doc = (
        session.query(DocumentModel)
        .filter(DocumentModel.project_id == project_id, DocumentModel.doc_key == doc_key)
        .one_or_none()
    )
    if doc is None:
        raise LookupError(f"document not found: {doc_key!r}")

    repo = DocNodeRepository(session)
    new_node: DocNode | None = None
    if node_key is not None:
        new_node = repo.get_by_key(project_id, node_key)
        if new_node is None:
            raise NodeNotFoundError(node_key)

    # Явная привязка к Инбоксу — это тот же «не разложен», то есть NULL.
    # Второе представление того же состояния заставило бы каждый счётчик
    # складываться из двух источников, а первый же забытый источник дал бы
    # расхождение между рельсом и списком.
    new_id = (
        None
        if (new_node is not None and new_node.is_inbox)
        else (new_node.row_id if new_node is not None else None)
    )
    old_id = doc.node_id
    if old_id == new_id and (position is None or position == doc.node_position):
        return new_node

    old_key: str | None = None
    if old_id is not None:
        previous = repo.get(old_id)
        old_key = previous.node_key if previous is not None else None

    doc.node_id = new_id
    if position is not None:
        doc.node_position = position
    # ``last_updated`` намеренно не трогаем. Это отметка о свежести содержимого:
    # по ней человек читает колонку «обновлён», а правила FM-004/FM-005 —
    # протухание документа. Раскладка по разделам содержимого не меняет, а
    # ``classify --apply`` проходит разом по всему корпусу: один такой вызов
    # обнулил бы признак протухания у всех документов сразу. Когда именно
    # документ переложили, хранят ревизия и activity event.
    session.flush()

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_id=doc.row_id,
        author=author,
        diff=_diff("node", old=old_key, new=node_key),
        reason=reason or "assign_node",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "doc.node_changed",
        author,
        scope_kind="doc",
        scope_id=doc_key,
        payload={"old_node": old_key, "new_node": node_key},
        summary=f"Document {doc_key}: node {old_key} → {node_key}",
    )
    return new_node


def classify_project(
    session: Session,
    *,
    project_id: int,
    author: str,
    dry_run: bool = True,
    only_unplaced: bool = True,
    rules: tuple[doc_taxonomy.PlacementRule, ...] = DEFAULT_RULES,
    reason: str | None = None,
) -> ClassifyReport:
    """Разложить корпус по правилам. По умолчанию — ``dry_run`` и только Инбокс.

    ``only_unplaced=True`` намеренно дефолт: повторный прогон не должен
    перекладывать документ, который человек уже положил руками. Переразложить
    всё целиком — осознанное действие с явным флагом.

    ``dry_run`` считает раскладку и ничего не пишет. Отчёт в обоих случаях
    одинаковый, поэтому «посмотреть» и «применить» отличаются одним флагом, а
    не разными путями кода.
    """
    repo = DocNodeRepository(session)
    known = {node.node_key for node in repo.list_for_project(project_id)}
    docs = DocumentRepository(session).list_for_project(project_id)

    report = ClassifyReport()
    for doc in docs:
        if only_unplaced and doc.node_id is not None:
            continue
        placement = doc_taxonomy.classify(doc.doc_key, DocumentType(doc.type), rules=rules)
        # Правило может указывать на раздел, которого в этом проекте нет:
        # дерево правится руками. Такой документ честнее оставить в Инбоксе,
        # чем заводить раздел молча.
        if placement.node_key is not None and placement.node_key not in known:
            placement = Placement(
                doc_key=doc.doc_key,
                node_key=None,
                reason=f"раздел {placement.node_key!r} в проекте не заведён",
            )
        report.placements.append(placement)
        if not dry_run and placement.node_key is not None:
            assign(
                session,
                project_id=project_id,
                doc_key=doc.doc_key,
                node_key=placement.node_key,
                author=author,
                reason=reason or f"classify: {placement.reason}",
            )
    return report


def _require_node_model(session: Session, project_id: int, node_key: str) -> DocNodeModel:
    model = (
        session.query(DocNodeModel)
        .filter(DocNodeModel.project_id == project_id, DocNodeModel.node_key == node_key)
        .one_or_none()
    )
    if model is None:
        raise NodeNotFoundError(node_key)
    return model
