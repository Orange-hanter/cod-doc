"""Запись и чтение рёбер `relation` (RFC 39 §3.4).

`relate` проходит по шагам RFC: разбор концов → поиск типа с алиасами и
inverse → проверка цели по её виду → проверка цикла → запись → ревизия
сущности-источника и событие. Все параметры проверяются за один проход, и
ошибка называет каждый неверный. Типы с бэкендом не `relation` пока отвергаются
с указанием существующего тула (диспетчер — REL-017).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.domain.relations import (
    ENTITY_KINDS,
    EntityRef,
    RefKind,
    RefSyntaxError,
    parse_ref,
)
from cod_doc.infra import sync_registry
from cod_doc.infra.models import ProjectModel, RelationModel
from cod_doc.services import activity_service
from cod_doc.services.entity_ref import ResolvedRef, resolve_many

from ._types import RelationEdge, RelationError
from ._vocab import Loaded, check_backend, load, unknown_type_error

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.relation_types import RelationType

_MAX_AUTHOR = 128
_URL = re.compile(r"^https?://\S+$")

#: Виды-источники, у которых есть история ревизий. У criterion и finding её нет:
#: событие пишется без ревизии (`emit_for_write`).
_REVISION_KINDS: dict[RefKind, EntityKind] = {
    RefKind.TASK: EntityKind.TASK,
    RefKind.STORY: EntityKind.STORY,
    RefKind.DOCUMENT: EntityKind.DOCUMENT,
    RefKind.SECTION: EntityKind.SECTION,
    RefKind.ADR: EntityKind.ADR,
    RefKind.QUESTION: EntityKind.QUESTION,
    RefKind.SCENARIO: EntityKind.SCENARIO,
    RefKind.PLAN: EntityKind.PLAN,
    RefKind.PLAN_SECTION: EntityKind.PLAN_SECTION,
    RefKind.MODULE: EntityKind.MODULE,
}


@dataclass(frozen=True)
class _Plan:
    """Ребро, готовое к записи: канонический тип и направление хранения."""

    type: RelationType
    reversed: bool
    from_ref: EntityRef
    from_res: ResolvedRef
    to_ref: EntityRef
    to_res: ResolvedRef | None  # None — литерал (code/url) или ссылка вперёд
    to_key: str | None


def _literal_problem(ref: EntityRef) -> str | None:
    if ref.kind is RefKind.URL and not _URL.fullmatch(ref.key):
        return f"to_ref: {ref} — url должен быть http(s)://… без пробелов"
    if ref.kind is RefKind.CODE:
        path = PurePosixPath(ref.key.partition("::")[0])
        if not path.parts or path.is_absolute() or ".." in path.parts:
            return f"to_ref: {ref} — путь кода относительный, без '..' (path::symbol)"
    return None


def _parse_ends(from_raw: str, to_raw: str) -> tuple[dict[str, EntityRef], list[str]]:
    refs: dict[str, EntityRef] = {}
    problems: list[str] = []
    for label, raw in (("from_ref", from_raw), ("to_ref", to_raw)):
        try:
            refs[label] = parse_ref(raw)
        except RefSyntaxError as exc:
            problems.append(f"{label}: {exc}")
    if "from_ref" in refs and refs["from_ref"].kind not in ENTITY_KINDS:
        problems.append(f"from_ref: {refs['from_ref']} — источником может быть только сущность")
        del refs["from_ref"]
    return refs, problems


def _find_type(
    loaded: Loaded, token: str, refs: dict[str, EntityRef], raws: tuple[str, str], *, writing: bool
) -> tuple[RelationType | None, bool, list[str], str]:
    f, t = refs["from_ref"].kind, refs["to_ref"].kind
    res = loaded.vocab.lookup(token, f, t)
    if res is None:
        err = unknown_type_error(loaded, token, raws[0], raws[1], f, t)
        return None, False, err.problems, err.hint
    if writing and res.type in loaded.deprecated:
        return None, False, [f"тип {res.type.name!r} устарел: новые рёбра не создаются"], ""
    try:
        check_backend(res.type, token)
    except RelationError as exc:
        # Тип известен: дальше проверяются и концы, чтобы ответ назвал всё сразу.
        return res.type, res.reversed, exc.problems, exc.hint
    return res.type, res.reversed, [], ""


def _prepare(
    session: Session,
    project_id: int,
    from_raw: str,
    token: str,
    to_raw: str,
    *,
    writing: bool,
) -> _Plan | None:
    """Собрать `_Plan`; `None` — только для `unrelate`: источника нет, снимать нечего."""
    refs, problems = _parse_ends(from_raw, to_raw)
    hint = ""
    rt: RelationType | None = None
    rev = False
    if len(refs) == 2:  # noqa: PLR2004 — оба конца разобраны
        loaded = load(session, project_id)
        rt, rev, found, hint = _find_type(loaded, token, refs, (from_raw, to_raw), writing=writing)
        problems += found
    if rt is None:
        if problems:
            raise RelationError(problems, hint)
        return None
    src, dst = (refs["to_ref"], refs["from_ref"]) if rev else (refs["from_ref"], refs["to_ref"])
    hits = resolve_many(session, project_id, [r for r in (src, dst) if r.kind in ENTITY_KINDS])
    src_res = hits.get(src)
    dst_res = hits.get(dst) if dst.kind in ENTITY_KINDS else None
    if src_res is None:
        if not writing:
            if problems:
                raise RelationError(problems, hint)
            return None
        problems.append(f"{'to_ref' if rev else 'from_ref'}: {src} не найден")
    forward = dst.kind in ENTITY_KINDS and dst_res is None
    # Бэкенд пишет только по существующей цели: ссылка вперёд ему недоступна.
    if forward and writing and (not rt.allow_forward or rt.backend != "relation"):
        problems.append(
            f"{'from_ref' if rev else 'to_ref'}: {dst} не найден; ссылка вперёд допустима "
            "только для типов с allow_forward (about, blocks)"
            if rt.backend == "relation"
            else f"{'from_ref' if rev else 'to_ref'}: {dst} не найден"
        )
    if writing and (lit := _literal_problem(dst)):
        problems.append(lit)
    if src_res is not None and dst_res is not None and src_res.uid == dst_res.uid:
        problems.append("связь сущности с самой собой запрещена")
    if problems:
        raise RelationError(problems, hint)
    assert src_res is not None
    if rt.symmetric and dst_res is not None and src_res.uid > dst_res.uid:
        src, dst, src_res, dst_res = dst, src, dst_res, src_res
    return _Plan(rt, rev, src, src_res, dst, dst_res, None if dst_res else dst.key)


def _find_edge(session: Session, project_id: int, plan: _Plan) -> RelationModel | None:
    stmt = select(RelationModel).where(
        RelationModel.project_id == project_id,
        RelationModel.from_uid == plan.from_res.uid,
        RelationModel.type == plan.type.name,
        RelationModel.to_kind == plan.to_ref.kind.value,
    )
    if plan.to_res is not None:
        # Ребро могло быть записано ссылкой вперёд, пока цели не было.
        forward = RelationModel.to_uid.is_(None) & (RelationModel.to_key == plan.to_ref.key)
        stmt = stmt.where((RelationModel.to_uid == plan.to_res.uid) | forward)
    else:
        stmt = stmt.where(RelationModel.to_uid.is_(None), RelationModel.to_key == plan.to_ref.key)
    return session.scalars(stmt.order_by(RelationModel.row_id)).first()


def _reaches(session: Session, project_id: int, type_name: str, start: str, target: str) -> bool:
    """Есть ли путь `start → … → target` по рёбрам типа (обход в ширину)."""
    seen = {start}
    frontier = {start}
    while frontier:
        nxt = set(
            session.scalars(
                select(RelationModel.to_uid).where(
                    RelationModel.project_id == project_id,
                    RelationModel.type == type_name,
                    RelationModel.from_uid.in_(frontier),
                    RelationModel.to_uid.is_not(None),
                )
            )
        )
        if target in nxt:
            return True
        frontier = {u for u in nxt if u is not None} - seen
        seen |= frontier
    return False


def _edge_uid(session: Session, project_id: int, plan: _Plan) -> str | None:
    """Детерминированный uid ребра (реестр синхронизации); `None` — у проекта ещё нет uid."""
    project_uid = session.get_one(ProjectModel, project_id).uid
    if project_uid is None:
        return None  # uid поставит захват sync при flush
    return sync_registry.edge_uid(
        "relation",
        {
            "project_id": project_uid,
            "from_uid": plan.from_res.uid,
            "type": plan.type.name,
            "to_kind": plan.to_ref.kind.value,
            "to_uid": plan.to_res.uid if plan.to_res else None,
            "to_key": plan.to_key,
        },
    )


def record(
    session: Session,
    *,
    project_id: int,
    op: str,
    author: str,
    type_name: str,
    source: ResolvedRef | None,
    from_ref: EntityRef,
    from_uid: str,
    to_ref: EntityRef,
    to_uid: str | None,
    note: str | None,
    reason: str | None = None,
) -> None:
    """Ревизия сущности-источника и событие `relation.<op>` одним вызовом.

    Источник без ревизий (criterion, finding) или уже удалённый (`source=None`)
    даёт только событие.
    """
    payload: dict[str, Any] = {
        "type": type_name,
        "from": {"uid": from_uid, "ref": str(from_ref)},
        "to": {"uid": to_uid, "ref": str(to_ref)},
        "note": note,
    }
    if reason:
        payload["reason"] = reason
    summary = f"{from_ref} {type_name} {to_ref} ({op})"
    kind = f"relation.{op}"
    rev_kind = _REVISION_KINDS.get(from_ref.kind)
    if source is not None and rev_kind is not None:
        activity_service.write_revision_and_emit_event(
            session,
            project_id=project_id,
            entity_kind=rev_kind,
            entity_id=source.row_id,
            author=author,
            diff=json.dumps({"op": kind, **payload}, ensure_ascii=False),
            reason=reason or kind,
            activity_kind=kind,
            activity_scope_kind=from_ref.kind.value,
            activity_scope_id=from_ref.key,
            activity_payload=payload,
            activity_summary=summary,
        )
        return
    activity_service.emit_for_write(
        session,
        project_id,
        kind,
        author,
        scope_kind=from_ref.kind.value,
        scope_id=from_ref.key,
        payload=payload,
        summary=summary,
    )


def _edge_view(plan: _Plan, row: RelationModel, *, created: bool) -> RelationEdge:
    return RelationEdge(
        type=plan.type.name,
        uid=row.uid,
        from_ref=plan.from_ref,
        to_ref=plan.to_ref,
        from_uid=row.from_uid,
        to_uid=row.to_uid,
        note=row.note,
        resolved=row.resolved,
        reversed=plan.reversed,
        created=created,
    )


def _check_author(author: str) -> list[str]:
    if not author.strip() or len(author) > _MAX_AUTHOR:
        return [f"author: непустая строка до {_MAX_AUTHOR} символов"]
    return []


def relate(
    session: Session,
    *,
    project_id: int,
    from_ref: str,
    type: str,
    to_ref: str,
    author: str,
    note: str | None = None,
) -> RelationEdge:
    """Связать две сущности; повтор того же ребра возвращает его без нового события."""
    try:
        plan = _prepare(session, project_id, from_ref, type, to_ref, writing=True)
    except RelationError as exc:
        extra = _check_author(author)
        raise RelationError([*exc.problems, *extra], exc.hint) from None
    if problems := _check_author(author):
        raise RelationError(problems)
    assert plan is not None
    existing = _find_edge(session, project_id, plan)
    if existing is not None:
        if plan.to_res is not None and existing.to_uid is None:
            existing.to_uid, existing.to_key, existing.resolved = plan.to_res.uid, None, True
            session.flush()
        return _edge_view(plan, existing, created=False)
    if (
        plan.type.acyclic
        and plan.to_res is not None
        and _reaches(session, project_id, plan.type.name, plan.to_res.uid, plan.from_res.uid)
    ):
        raise RelationError(
            [f"{plan.from_ref} {plan.type.name} {plan.to_ref} замкнуло бы цикл"],
            f"Тип {plan.type.name!r} ацикличен: между {plan.to_ref} и {plan.from_ref} "
            "уже есть путь в обратную сторону.",
        )
    row = RelationModel(
        project_id=project_id,
        from_kind=plan.from_ref.kind.value,
        from_uid=plan.from_res.uid,
        type=plan.type.name,
        to_kind=plan.to_ref.kind.value,
        to_uid=plan.to_res.uid if plan.to_res else None,
        to_key=plan.to_key,
        note=note,
        resolved=plan.to_res is not None or plan.to_ref.kind in {RefKind.CODE, RefKind.URL},
        author=author,
        uid=_edge_uid(session, project_id, plan),
    )
    session.add(row)
    session.flush()
    record(
        session,
        project_id=project_id,
        op="added",
        author=author,
        type_name=plan.type.name,
        source=plan.from_res,
        from_ref=plan.from_ref,
        from_uid=plan.from_res.uid,
        to_ref=plan.to_ref,
        to_uid=row.to_uid,
        note=note,
    )
    return _edge_view(plan, row, created=True)


def unrelate(
    session: Session,
    *,
    project_id: int,
    from_ref: str,
    type: str,
    to_ref: str,
    author: str,
) -> bool:
    """Снять ребро; `False` — такого ребра не было."""
    if problems := _check_author(author):
        raise RelationError(problems)
    plan = _prepare(session, project_id, from_ref, type, to_ref, writing=False)
    if plan is None:
        return False
    row = _find_edge(session, project_id, plan)
    if row is None:
        return False
    note = row.note
    session.delete(row)
    session.flush()
    record(
        session,
        project_id=project_id,
        op="removed",
        author=author,
        type_name=plan.type.name,
        source=plan.from_res,
        from_ref=plan.from_ref,
        from_uid=plan.from_res.uid,
        to_ref=plan.to_ref,
        to_uid=plan.to_res.uid if plan.to_res else None,
        note=note,
    )
    return True
