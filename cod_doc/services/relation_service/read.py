"""Единое чтение связей сущности (RFC 39 §3.6): одна лента `RelationView`.

Источники, в порядке приоритета при совпадении ребра:

- `relation` — записанные связи (в обе стороны);
- бэкенды (только чтение): `dependency`, `adr_relation`, `adr_supersedes`;
- прежние таблицы той же формы (до фазы C RFC): `adr_task`, `story_link`,
  `scenario_link`, `open_question_link` — имена типов приведены к встроенным;
- производные (`include_derived`): ссылки из прозы (`mentions` /
  `mentioned_in`) и коммиты задачи (`committed_in`).

Запросов на источник — константа (по одному-двум), заголовки концов
подтягиваются пачкой через `entity_ref`: по `uid` (`describe`) или по ключу
(`resolve_many`), один запрос на вид.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import literal, or_, select

from cod_doc.domain.relations import (
    ENTITY_KINDS,
    EntityRef,
    RefKind,
    RefSyntaxError,
    parse_ref,
)
from cod_doc.infra.models import (
    ADRModel,
    ADRRelationModel,
    ADRSupersedeModel,
    ADRTaskModel,
    CommitLinkModel,
    DependencyModel,
    LinkModel,
    OpenQuestionModel,
    QuestionLinkModel,
    RelationModel,
    ScenarioLinkModel,
    ScenarioModel,
    SectionModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.services.entity_ref import ResolvedRef, describe, resolve_many

from ._types import RelationError, RelationView
from ._vocab import Loaded, load

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

_K = RefKind
_OUT: Literal["out"] = "out"
_IN: Literal["in"] = "in"

#: `adr_task.relation` → встроенный тип (`relates` — симметричный `relates_to`).
_ADR_TASK_TYPES = {
    "implements": "implemented_by",
    "invalidates": "invalidated_by",
    "discovers": "discovered_by",
    "relates": "relates_to",
}
#: Вид ссылки из прозы → (вид сущности, колонка ключа).
_LINK_TARGETS = {
    "task": (_K.TASK, "to_task_id"),
    "story": (_K.STORY, "to_story_id"),
    "adr": (_K.ADR, "to_adr_id"),
}
_DOC_LINK_KINDS = {"canonical", "wiki", "markdown", "section"}
_COMMIT_TITLE_CHARS = 80


@dataclass(frozen=True)
class _Raw:
    """Один конец-кандидат в ленту до подтягивания заголовков."""

    source: str
    type: str
    label: str
    direction: Literal["out", "in"]
    kind: RefKind
    uid: str | None = None  # другой конец по uid
    key: str | None = None  # другой конец по ключу (литерал, ссылка вперёд, прежние таблицы)
    resolved: bool = True
    note: str | None = None
    text: str | None = None  # конец не из RefKind (коммит): готовая строка
    title: str | None = None


def _types_of(loaded: Loaded, token: str, f: RefKind, t: RefKind) -> tuple[str, str, bool]:
    """(каноническое имя, обратное имя, симметричность) по токену и паре видов."""
    res = loaded.vocab.lookup(token, f, t)
    if res is None:
        return token, token, False
    rt = res.type
    return rt.name, rt.inverse, rt.symmetric


def _authored(session: Session, pid: int, me: ResolvedRef, loaded: Loaded) -> list[_Raw]:
    rows = session.scalars(
        select(RelationModel)
        .where(
            RelationModel.project_id == pid,
            or_(RelationModel.from_uid == me.uid, RelationModel.to_uid == me.uid),
        )
        .order_by(RelationModel.row_id)
    )
    out: list[_Raw] = []
    for r in rows:
        f, t = RefKind(r.from_kind), RefKind(r.to_kind)
        symmetric = any(x.name == r.type and x.symmetric for x in loaded.types)
        inverse = next(
            (
                x.inverse
                for x in loaded.types
                if x.name == r.type and f in x.from_kinds and t in x.to_kinds
            ),
            r.type,
        )
        if r.from_uid == me.uid:
            out.append(
                _Raw("relation", r.type, r.type, _OUT, t, r.to_uid, r.to_key, r.resolved, r.note)
            )
        else:
            direction = _OUT if symmetric else _IN
            label = r.type if symmetric else inverse
            out.append(
                _Raw("relation", r.type, label, direction, f, r.from_uid, None, True, r.note)
            )
    return out


def _dependency(session: Session, me: ResolvedRef) -> list[_Raw]:
    if me.kind is not _K.TASK:
        return []
    out = [
        _Raw("dependency", "blocked_by", "blocked_by", _OUT, _K.TASK, uid, None, True, note)
        for uid, note in session.execute(
            select(TaskModel.uid, DependencyModel.note)
            .join(DependencyModel, DependencyModel.to_task_id == TaskModel.row_id)
            .where(DependencyModel.from_task_id == me.row_id)
        )
    ]
    out += [
        _Raw("dependency", "blocked_by", "blocks", _IN, _K.TASK, uid, None, True, note)
        for uid, note in session.execute(
            select(TaskModel.uid, DependencyModel.note)
            .join(DependencyModel, DependencyModel.from_task_id == TaskModel.row_id)
            .where(DependencyModel.to_task_id == me.row_id)
        )
    ]
    return out


def _adr_edges(session: Session, me: ResolvedRef, loaded: Loaded) -> list[_Raw]:
    if me.kind is not _K.ADR:
        return []
    out: list[_Raw] = []
    for kind, uid, note, outgoing in (
        *(
            (k, u, n, True)
            for k, u, n in session.execute(
                select(ADRRelationModel.kind, ADRModel.uid, ADRRelationModel.reason)
                .join(ADRModel, ADRModel.row_id == ADRRelationModel.to_id)
                .where(ADRRelationModel.from_id == me.row_id)
            )
        ),
        *(
            (k, u, n, False)
            for k, u, n in session.execute(
                select(ADRRelationModel.kind, ADRModel.uid, ADRRelationModel.reason)
                .join(ADRModel, ADRModel.row_id == ADRRelationModel.from_id)
                .where(ADRRelationModel.to_id == me.row_id)
            )
        ),
    ):
        name, inverse, _ = _types_of(loaded, kind, _K.ADR, _K.ADR)
        out.append(
            _Raw(
                "adr_relation", name, name if outgoing else inverse,
                _OUT if outgoing else _IN, _K.ADR, uid, None, True, note,
            )
        )  # fmt: skip
    for uid, note in session.execute(
        select(ADRModel.uid, ADRSupersedeModel.reason)
        .join(ADRSupersedeModel, ADRSupersedeModel.superseded_id == ADRModel.row_id)
        .where(ADRSupersedeModel.superseding_id == me.row_id)
    ):
        out.append(
            _Raw("adr_supersedes", "supersedes", "supersedes", _OUT, _K.ADR, uid, None, True, note)
        )
    for uid, note in session.execute(
        select(ADRModel.uid, ADRSupersedeModel.reason)
        .join(ADRSupersedeModel, ADRSupersedeModel.superseding_id == ADRModel.row_id)
        .where(ADRSupersedeModel.superseded_id == me.row_id)
    ):
        out.append(
            _Raw(
                "adr_supersedes", "supersedes", "superseded_by", _IN, _K.ADR, uid, None, True, note
            )
        )
    return out


def _adr_task(session: Session, pid: int, me: ResolvedRef, loaded: Loaded) -> list[_Raw]:
    out: list[_Raw] = []
    if me.kind is _K.ADR:
        for task_key, relation in session.execute(
            select(ADRTaskModel.task_id, ADRTaskModel.relation).where(
                ADRTaskModel.adr_row_id == me.row_id
            )
        ):
            name = _ADR_TASK_TYPES.get(relation, relation)
            out.append(_Raw("adr_task", name, name, _OUT, _K.TASK, None, task_key))
    elif me.kind is _K.TASK:
        for uid, relation in session.execute(
            select(ADRModel.uid, ADRTaskModel.relation)
            .join(ADRTaskModel, ADRTaskModel.adr_row_id == ADRModel.row_id)
            .where(ADRModel.project_id == pid, ADRTaskModel.task_id == me.key)
        ):
            name = _ADR_TASK_TYPES.get(relation, relation)
            _, inverse, symmetric = _types_of(loaded, name, _K.ADR, _K.TASK)
            label, direction = (name, _OUT) if symmetric else (inverse, _IN)
            out.append(_Raw("adr_task", name, label, direction, _K.ADR, uid))
    return out


#: (имя источника, модель ребра, колонка владельца, модель владельца, вид владельца).
_LEGACY: tuple[tuple[str, Any, Any, Any, RefKind], ...] = (
    ("story_link", StoryLinkModel, StoryLinkModel.story_id, UserStoryModel, _K.STORY),
    (
        "scenario_link", ScenarioLinkModel, ScenarioLinkModel.scenario_row_id, ScenarioModel,
        _K.SCENARIO,
    ),
    (
        "question_link", QuestionLinkModel, QuestionLinkModel.question_row_id, OpenQuestionModel,
        _K.QUESTION,
    ),
)  # fmt: skip


def _legacy(session: Session, pid: int, me: ResolvedRef, loaded: Loaded) -> list[_Raw]:
    out: list[_Raw] = []
    for source, link, fk, owner, owner_kind in _LEGACY:
        note = link.note if hasattr(link, "note") else literal(None)
        if me.kind is owner_kind:
            rows = session.execute(
                select(link.to_kind, link.to_ref, link.relation, note).where(fk == me.row_id)
            )
            for to_kind, to_ref, relation, n in rows:
                try:
                    target = RefKind(to_kind)
                except ValueError:
                    continue
                name, _, _ = _types_of(loaded, relation, owner_kind, target)
                out.append(_Raw(source, name, name, _OUT, target, None, to_ref, True, n))
        rows = session.execute(
            select(link.relation, owner.uid, note)
            .join(owner, owner.row_id == fk)
            .where(owner.project_id == pid, link.to_kind == me.kind.value, link.to_ref == me.key)
        )
        for relation, uid, n in rows:
            name, inverse, symmetric = _types_of(loaded, relation, owner_kind, me.kind)
            label, direction = (name, _OUT) if symmetric else (inverse, _IN)
            out.append(_Raw(source, name, label, direction, owner_kind, uid, None, True, n))
    return out


def _link_target(link: LinkModel) -> tuple[RefKind, str] | None:
    if link.kind in _LINK_TARGETS:
        kind, col = _LINK_TARGETS[link.kind]
        key = getattr(link, col)
        return (kind, key) if key else None
    if link.kind in _DOC_LINK_KINDS and link.to_doc_key:
        return _K.DOCUMENT, link.to_doc_key
    if link.kind == "code" and link.to_file_path:
        sym = f"::{link.to_symbol}" if link.to_symbol else ""
        return _K.CODE, f"{link.to_file_path}{sym}"
    if link.kind == "url":
        return _K.URL, link.raw
    return None


def _prose(session: Session, pid: int, me: ResolvedRef) -> list[_Raw]:
    out: list[_Raw] = []
    base = select(LinkModel).where(LinkModel.project_id == pid)
    if me.kind in {_K.DOCUMENT, _K.SECTION}:
        stmt = base
        if me.kind is _K.SECTION:
            stmt = stmt.where(LinkModel.from_section_id == me.row_id)
        else:
            stmt = stmt.join(SectionModel, SectionModel.row_id == LinkModel.from_section_id).where(
                SectionModel.document_id == me.row_id
            )
        for link in session.scalars(stmt):
            target = _link_target(link)
            if target is not None and target != (me.kind, me.key):
                out.append(
                    _Raw(
                        "prose", "mentions", "mentions", _OUT, target[0], None, target[1],
                        link.resolved,
                    )
                )  # fmt: skip
    column = {
        _K.DOCUMENT: LinkModel.to_doc_key,
        _K.TASK: LinkModel.to_task_id,
        _K.STORY: LinkModel.to_story_id,
        _K.ADR: LinkModel.to_adr_id,
    }.get(me.kind)
    if column is not None:
        mentions = (
            select(SectionModel.uid)
            .join(LinkModel, LinkModel.from_section_id == SectionModel.row_id)
            .where(LinkModel.project_id == pid, column == me.key)
        )
        if me.kind is _K.DOCUMENT:  # самоупоминания документа не в счёт
            mentions = mentions.where(SectionModel.document_id != me.row_id)
        out += [
            _Raw("prose", "mentions", "mentioned_in", _IN, _K.SECTION, uid)
            for uid in session.scalars(mentions.distinct())
        ]
    return out


def _commits(session: Session, pid: int, me: ResolvedRef) -> list[_Raw]:
    if me.kind is not _K.TASK:
        return []
    return [
        _Raw(
            "commit",
            "committed_in",
            "committed_in",
            _OUT,
            _K.CODE,
            text=f"commit:{c.short_sha}",
            title=(c.message or "").splitlines()[0][:_COMMIT_TITLE_CHARS] if c.message else None,
        )
        for c in session.scalars(
            select(CommitLinkModel)
            .where(CommitLinkModel.project_id == pid, CommitLinkModel.task_id == me.key)
            .order_by(CommitLinkModel.ts, CommitLinkModel.row_id)
        )
    ]


def _views(session: Session, pid: int, raws: list[_Raw]) -> list[RelationView]:
    by_uid = describe(session, pid, {(r.kind, r.uid) for r in raws if r.uid})
    keyed = [
        EntityRef(r.kind, r.key)
        for r in raws
        if r.key and not r.uid and r.kind in ENTITY_KINDS and r.text is None
    ]
    by_key = resolve_many(session, pid, keyed)
    views: list[RelationView] = []
    seen: set[tuple[str, str, str]] = set()
    for r in raws:
        other: EntityRef | None
        title = r.title
        resolved = r.resolved
        if r.text is not None:
            other, shown = None, r.text
        else:
            hit = by_uid.get(r.uid) if r.uid else by_key.get(EntityRef(r.kind, r.key or ""))
            if hit is not None:
                other, title = EntityRef(r.kind, hit.key), hit.title
            else:  # литерал, ссылка вперёд или конец, которого уже нет
                other = EntityRef(r.kind, r.key or r.uid or "?")
                if r.kind in ENTITY_KINDS:
                    resolved = False
            shown = str(other)
        ident = (r.type, r.direction, shown)
        if ident in seen:  # то же ребро из двух источников: первый приоритетнее
            continue
        seen.add(ident)
        views.append(
            RelationView(
                label=r.label, type=r.type, other=other, other_ref=shown, other_title=title,
                direction=r.direction, source=r.source, resolved=resolved, note=r.note,
            )
        )  # fmt: skip
    return views


def relations(
    session: Session,
    *,
    project_id: int,
    ref: str,
    direction: str = "both",
    types: Sequence[str] | None = None,
    include_derived: bool = True,
) -> list[RelationView]:
    """Все связи сущности одной лентой (§3.6); `source` каждой — откуда она."""
    try:
        parsed = parse_ref(ref)
    except RefSyntaxError as exc:
        raise RelationError([f"ref: {exc}"]) from None
    if direction not in {"out", "in", "both"}:
        raise RelationError([f"direction: {direction!r}, допустимо out | in | both"])
    if parsed.kind not in ENTITY_KINDS:
        raise RelationError([f"ref: {parsed} — у code/url нет собственных связей"])
    me = resolve_many(session, project_id, [parsed]).get(parsed)
    if me is None:
        raise RelationError([f"ref: {parsed} не найден"])
    loaded = load(session, project_id)
    raws = [
        *_authored(session, project_id, me, loaded),
        *_dependency(session, me),
        *_adr_edges(session, me, loaded),
        *_adr_task(session, project_id, me, loaded),
        *_legacy(session, project_id, me, loaded),
    ]
    if include_derived:
        raws += [*_prose(session, project_id, me), *_commits(session, project_id, me)]
    wanted: set[str] | None = None
    if types:
        wanted = {e.type.name for (tok, _, _), e in loaded.vocab.entries.items() if tok in types}
        wanted |= set(types)
    raws = [
        r
        for r in raws
        if (direction == "both" or r.direction == direction)
        and (wanted is None or r.type in wanted or r.label in wanted)
    ]
    return _views(session, project_id, raws)
