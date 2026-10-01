"""ACU-009 (RFC 28 §3.6): закрытый реестр операций, которые исполняет одобренный doc_patch.

Approval ``doc_patch`` несёт операцию и её аргументы, а не текст, который
надо выполнить: исполняется только то, что перечислено здесь, и только
через сервисы, которые сами пишут ревизию и событие. Незнакомая операция —
ошибка, а не попытка угадать.

У каждой операции две половины:

- ``head`` — текущая голова ревизий сущности, на которую рассчитана правка.
  Предложение помнит голову на момент составления (``base_revision_id``);
  разошлась — документ поменяли после предложения, и правка могла устареть.
- ``apply`` — сама правка от имени того, кто одобрил.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, QuestionLinkKind, QuestionRelation
from cod_doc.infra.models import SectionModel
from cod_doc.services import doc_service, doc_tree_service, question_service, revision_service

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

Args = dict[str, Any]


class CuratorOpError(ValueError):
    """Предложение нельзя исполнить: незнакомая операция или нет её предмета."""


@dataclass(frozen=True, slots=True)
class CuratorOp:
    head: Callable[[Session, int, Args], str | None]
    apply: Callable[[Session, int, Args, str], dict[str, Any]]


# --------------------------------------------------------------------- #
# link_retarget — сменить цель ссылки в теле секции                      #
# --------------------------------------------------------------------- #


def retarget_links(body: str, old: str, new: str) -> str:
    """Заменить цель ``old`` на ``new`` в markdown- и wiki-ссылках тела.

    Меняется только цель ссылки: ``](old)``, ``](old#якорь)``, ``[[old]]``,
    ``[[old|подпись]]``, ``[[old#якорь]]``. То же слово в обычном тексте не
    трогается — иначе правка ссылки переписала бы прозу.
    """
    target = re.escape(old)
    body = re.sub(rf"\]\({target}(?=[)#\s])", f"]({new}", body)
    return re.sub(rf"\[\[{target}(?=[\]|#])", f"[[{new}", body)


def _section(session: Session, project_id: int, args: Args) -> SectionModel:
    doc = doc_service.get(session, project_id, str(args["doc_key"]))
    if doc is None or doc.row_id is None:
        raise CuratorOpError(f"документа {args['doc_key']!r} больше нет")
    section = session.execute(
        select(SectionModel).where(
            SectionModel.document_id == doc.row_id, SectionModel.anchor == str(args["anchor"])
        )
    ).scalar_one_or_none()
    if section is None:
        raise CuratorOpError(f"секции {args['doc_key']}#{args['anchor']} больше нет")
    return section


def _link_retarget_head(session: Session, project_id: int, args: Args) -> str | None:
    section = _section(session, project_id, args)
    return revision_service.head_for_entity(session, EntityKind.SECTION, section.row_id)


def _link_retarget_apply(
    session: Session, project_id: int, args: Args, author: str
) -> dict[str, Any]:
    section = _section(session, project_id, args)
    new_body = retarget_links(section.body, str(args["old"]), str(args["new"]))
    if new_body == section.body:
        raise CuratorOpError(f"в {args['doc_key']}#{args['anchor']} нет ссылки на {args['old']!r}")
    doc_service.patch_section(
        session,
        document_id=section.document_id,
        anchor=section.anchor,
        new_body=new_body,
        author=author,
        reason=f"curator doc_patch: link_retarget {args['old']} → {args['new']}",
    )
    return {"doc_key": args["doc_key"], "anchor": args["anchor"], "new": args["new"]}


# --------------------------------------------------------------------- #
# doc_set_node — положить документ в раздел дерева                       #
# --------------------------------------------------------------------- #


def _document_id(session: Session, project_id: int, args: Args) -> int:
    doc = doc_service.get(session, project_id, str(args["doc_key"]))
    if doc is None or doc.row_id is None:
        raise CuratorOpError(f"документа {args['doc_key']!r} больше нет")
    return doc.row_id


def _doc_set_node_head(session: Session, project_id: int, args: Args) -> str | None:
    return revision_service.head_for_entity(
        session, EntityKind.DOCUMENT, _document_id(session, project_id, args)
    )


def _doc_set_node_apply(
    session: Session, project_id: int, args: Args, author: str
) -> dict[str, Any]:
    _document_id(session, project_id, args)
    node = doc_tree_service.assign(
        session,
        project_id=project_id,
        doc_key=str(args["doc_key"]),
        node_key=str(args["node_key"]),
        author=author,
        reason="curator doc_patch: doc_set_node",
    )
    return {"doc_key": args["doc_key"], "node_key": node.node_key if node else None}


# --------------------------------------------------------------------- #
# question_link_retarget — перевесить ссылку открытого вопроса           #
# --------------------------------------------------------------------- #


def _question_head(session: Session, project_id: int, args: Args) -> str | None:
    question = question_service.get(session, project_id, str(args["question_id"]))
    if question is None or question.row_id is None:
        raise CuratorOpError(f"вопроса {args['question_id']!r} больше нет")
    return revision_service.head_for_entity(session, EntityKind.QUESTION, question.row_id)


def _question_link_retarget_apply(
    session: Session, project_id: int, args: Args, author: str
) -> dict[str, Any]:
    kind = QuestionLinkKind(str(args["to_kind"]))
    relation = QuestionRelation(str(args["relation"]))
    question_id = str(args["question_id"])
    reason = f"curator doc_patch: question_link_retarget {args['old']} → {args['new']}"
    detached = question_service.unlink(
        session,
        project_id=project_id,
        question_id=question_id,
        to_kind=kind,
        to_ref=str(args["old"]),
        relation=relation,
        author=author,
        reason=reason,
    )
    if not detached:
        raise CuratorOpError(f"у {question_id} нет ссылки {kind.value}:{args['old']}")
    question_service.link(
        session,
        project_id=project_id,
        question_id=question_id,
        to_kind=kind,
        to_ref=str(args["new"]),
        relation=relation,
        author=author,
        reason=reason,
    )
    return {"question_id": question_id, "new": args["new"]}


CURATOR_OPS: dict[str, CuratorOp] = {
    "link_retarget": CuratorOp(_link_retarget_head, _link_retarget_apply),
    "doc_set_node": CuratorOp(_doc_set_node_head, _doc_set_node_apply),
    "question_link_retarget": CuratorOp(_question_head, _question_link_retarget_apply),
}


def require(op: str) -> CuratorOp:
    """Операция из реестра или :class:`CuratorOpError` — исполнять чужое нельзя."""
    try:
        return CURATOR_OPS[op]
    except KeyError:
        raise CuratorOpError(
            f"операция {op!r} не входит в реестр CURATOR_OPS: {sorted(CURATOR_OPS)}"
        ) from None
