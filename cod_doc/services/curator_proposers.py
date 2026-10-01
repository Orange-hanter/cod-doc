"""ACU-012 (RFC 28 §3.4–3.5): LLM-предлагатель новой цели для битых ссылок.

Ссылка сломалась — цель переехала, переименована или удалена. Предлагатель
не придумывает новую цель: он находит кандидатов детерминированно (индекс
файлов для ссылок на код, полнотекстовый поиск для ссылок на документы) и
просит лёгкую модель **выбрать** одного из списка или сказать «ни один».
Ответ вне списка отбрасывается — модель не может предложить то, чего нет.

Выбор становится approval ``doc_patch`` (ACU-008) с операцией из реестра
``curator_ops`` и diff; применит его человек одобрением (ACU-009).

Покрываются два вида битых ссылок из очереди куратора:

- ``link`` — ссылка в теле секции документа → ``link_retarget``;
- ``question_link`` — ссылка открытого вопроса на код или документ →
  ``question_link_retarget``.

Ссылки на задачи, истории и ADR не трогаются: сменить такой id — решение
по смыслу, а не поиск переехавшего файла.

Ошибка бэкенда (``AIBackendError``) уходит в отчёт и не останавливает
остальные ссылки.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.models import DocumentModel, LinkModel, RepoFileModel, SectionModel
from cod_doc.services import (
    approval_service,
    curator_ops,
    doc_service,
    question_service,
    revision_service,
    search_service,
)
from cod_doc.services.ai_text import AIBackendError

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from cod_doc.services.ai_text import LiteReply

    Chooser = Callable[[str], LiteReply]

#: Сколько кандидатов показывать модели: больше — выбор хуже и промпт дороже.
MAX_CANDIDATES = 5
#: Сколько символов секции идёт в промпт как контекст ссылки.
_EXCERPT_CHARS = 600

_DOC_LINK_KINDS = frozenset({"canonical", "section", "markdown", "wiki"})
_MD_TARGET = re.compile(r"\]\(([^)\s#?]+)")
_WIKI_TARGET = re.compile(r"\[\[([^\]|#]+)")


@dataclass(slots=True)
class ProposerStats:
    """Итог предлагателей за прогон: что предложено, что оставлено, сколько стоило."""

    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    proposed: list[dict[str, Any]] = field(default_factory=list)
    reported: list[dict[str, str]] = field(default_factory=list)
    #: Пункты очереди ``(kind, ref)``, по которым есть предложение.
    handled: set[tuple[str, str]] = field(default_factory=set)

    @property
    def created(self) -> int:
        return sum(1 for p in self.proposed if p["outcome"] == "created")


def link_target(raw: str) -> str | None:
    """Цель ссылки, как она написана, без якоря: ``[x](a.md#y)`` → ``a.md``."""
    for pattern in (_MD_TARGET, _WIKI_TARGET):
        found = pattern.search(raw)
        if found:
            return found.group(1).strip() or None
    return None


def _choose(
    chooser: Chooser, prompt: str, candidates: list[str], stats: ProposerStats
) -> tuple[str | None, str]:
    """Спросить модель; вернуть (кандидат или None, обоснование или причина отказа)."""
    reply = chooser(prompt)
    stats.llm_calls += 1
    stats.tokens_in += reply.tokens_in
    stats.tokens_out += reply.tokens_out
    found = re.search(r"\{.*\}", reply.text, re.DOTALL)
    try:
        answer: Any = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, dict):
        return None, "модель ответила не JSON-ом"
    choice, why = answer.get("choice"), str(answer.get("why") or "").strip()
    if not isinstance(choice, int) or isinstance(choice, bool):
        return None, "модель не выбрала номер"
    if choice == 0:
        return None, why or "модель не нашла подходящего кандидата"
    if not 1 <= choice <= len(candidates):
        return None, f"модель выбрала {choice} — вне списка из {len(candidates)}"
    return candidates[choice - 1], why or "выбор модели"


def _prompt(where: str, raw: str, reason: str | None, excerpt: str, candidates: list[str]) -> str:
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates, start=1))
    return (
        "Ты помогаешь чинить битую ссылку в проектной документации.\n"
        f"Где: {where}\nСсылка: {raw}\nПочему битая: {reason or 'цель не найдена'}\n"
        f"Фрагмент вокруг ссылки:\n---\n{excerpt}\n---\n"
        f"Кандидаты на новую цель:\n{numbered}\n\n"
        "Выбери номер кандидата, на который ссылка должна указывать, или 0, если ни "
        "один не подходит по смыслу. Не выдумывай целей вне списка.\n"
        'Ответь только JSON: {"choice": <номер>, "why": "<одна фраза>"}'
    )


def _code_candidates(session: Session, project_id: int, old_path: str) -> list[str]:
    name = PurePosixPath(old_path).name
    if not name:
        return []
    rows = session.execute(
        select(RepoFileModel.path)
        .where(
            RepoFileModel.project_id == project_id,
            (RepoFileModel.path == name) | RepoFileModel.path.like(f"%/{name}"),
        )
        .order_by(RepoFileModel.path)
        .limit(MAX_CANDIDATES)
    ).scalars()
    return [path for path in rows if path != old_path.lstrip("/")]


def _doc_candidates(session: Session, project_id: int, old: str, exclude: str | None) -> list[str]:
    stem = PurePosixPath(old.removeprefix("doc:")).stem
    query = re.sub(r"[-_./]+", " ", stem).strip()
    if not query:
        return []
    hits = search_service.search(
        session, project_id=project_id, query=query, scope="doc", limit=MAX_CANDIDATES + 1
    )
    keys = [str(hit["ref"]) for hit in hits["by_kind"].get("doc", [])]
    return [key for key in keys if key != exclude][:MAX_CANDIDATES]


def _render_doc_target(session: Session, project_id: int, old: str, doc_key: str) -> str:
    """Новая цель в той же форме, что старая: ``doc:key``, путь ``.md`` или ключ."""
    if old.startswith("doc:"):
        return f"doc:{doc_key}"
    if old.endswith(".md"):
        doc = doc_service.get(session, project_id, doc_key)
        path = doc.path if doc is not None else f"{doc_key}.md"
        return f"/{path}" if old.startswith("/") else path
    return doc_key


def _unified(before: str, after: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


def propose_link_retargets(
    session: Session,
    project_id: int,
    *,
    chooser: Chooser,
    max_proposals: int,
    author: str,
    stats: ProposerStats,
) -> None:
    """Предложить новые цели для битых ссылок документов и вопросов."""
    # Кандидаты на документы ищутся полнотекстом; пустой индекс молча дал бы
    # «нет кандидатов» каждой ссылке (тот же сторож, что у ctx_search).
    search_service.ensure_index(session, project_id)
    _propose_doc_links(session, project_id, chooser, max_proposals, author, stats)
    _propose_question_links(session, project_id, chooser, max_proposals, author, stats)


def _propose_doc_links(
    session: Session,
    project_id: int,
    chooser: Chooser,
    max_proposals: int,
    author: str,
    stats: ProposerStats,
) -> None:
    rows = session.execute(
        select(LinkModel, SectionModel, DocumentModel)
        .join(SectionModel, SectionModel.row_id == LinkModel.from_section_id)
        .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
        .where(LinkModel.project_id == project_id, LinkModel.resolved.is_(False))
        .order_by(DocumentModel.doc_key, SectionModel.position, LinkModel.row_id)
    ).all()
    for link, section, doc in rows:
        if stats.created >= max_proposals:
            return
        ref = f"{doc.doc_key}#{section.anchor}"
        old = link_target(link.raw)
        if old is None or link.kind not in _DOC_LINK_KINDS | {"code"}:
            continue
        if link.kind == "code":
            candidates = _code_candidates(session, project_id, old)
        else:
            candidates = _doc_candidates(session, project_id, old, exclude=doc.doc_key)
        if not candidates:
            stats.reported.append({"kind": "link", "ref": ref, "reason": "нет кандидатов"})
            continue
        try:
            picked, why = _choose(
                chooser,
                _prompt(
                    ref, link.raw, link.broken_reason, section.body[:_EXCERPT_CHARS], candidates
                ),
                candidates,
                stats,
            )
        except AIBackendError as exc:
            stats.reported.append({"kind": "link", "ref": ref, "reason": f"LLM: {exc}"})
            continue
        if picked is None:
            stats.reported.append({"kind": "link", "ref": ref, "reason": why})
            continue
        new = (
            picked if link.kind == "code" else _render_doc_target(session, project_id, old, picked)
        )
        after = curator_ops.retarget_links(section.body, old, new)
        if after == section.body:
            stats.reported.append(
                {"kind": "link", "ref": ref, "reason": "цель не заменилась в тексте"}
            )
            continue
        args = {"doc_key": doc.doc_key, "anchor": section.anchor, "old": old, "new": new}
        result = approval_service.request_doc_patch(
            session,
            project_id,
            op="link_retarget",
            args=args,
            diff=_unified(section.body, after, ref),
            rationale=why,
            base_revision_id=revision_service.head_for_entity(
                session, EntityKind.SECTION, section.row_id
            ),
            requested_by=author,
        )
        stats.proposed.append(
            {"kind": "link", "ref": ref, "op": "link_retarget", **_outcome(result)}
        )
        stats.handled.add(("link", ref))


def _propose_question_links(
    session: Session,
    project_id: int,
    chooser: Chooser,
    max_proposals: int,
    author: str,
    stats: ProposerStats,
) -> None:
    for broken in question_service.broken_links(session, project_id=project_id):
        if stats.created >= max_proposals:
            return
        kind = str(broken.to_kind)
        if kind not in {"code", "document"}:
            continue
        ref = f"{broken.question_id} → {kind}:{broken.to_ref}"
        path, _, fragment = broken.to_ref.partition("#")
        if kind == "code":
            candidates = _code_candidates(session, project_id, path)
        else:
            candidates = _doc_candidates(session, project_id, path, exclude=None)
        if not candidates:
            stats.reported.append({"kind": "question_link", "ref": ref, "reason": "нет кандидатов"})
            continue
        question = question_service.get(session, project_id, broken.question_id)
        excerpt = f"{question.title}\n{question.question}" if question is not None else ""
        try:
            picked, why = _choose(
                chooser,
                _prompt(
                    broken.question_id, broken.to_ref, broken.broken_reason, excerpt, candidates
                ),
                candidates,
                stats,
            )
        except AIBackendError as exc:
            stats.reported.append({"kind": "question_link", "ref": ref, "reason": f"LLM: {exc}"})
            continue
        if picked is None:
            stats.reported.append({"kind": "question_link", "ref": ref, "reason": why})
            continue
        new = f"{picked}#{fragment}" if fragment else picked
        args = {
            "question_id": broken.question_id,
            "to_kind": kind,
            "relation": str(broken.relation),
            "old": broken.to_ref,
            "new": new,
        }
        result = approval_service.request_doc_patch(
            session,
            project_id,
            op="question_link_retarget",
            args=args,
            diff=f"-{kind}:{broken.to_ref}\n+{kind}:{new}\n",
            rationale=why,
            base_revision_id=curator_ops.CURATOR_OPS["question_link_retarget"].head(
                session, project_id, args
            ),
            requested_by=author,
        )
        stats.proposed.append(
            {
                "kind": "question_link",
                "ref": ref,
                "op": "question_link_retarget",
                **_outcome(result),
            }
        )
        stats.handled.add(("question_link", ref))


def _outcome(result: approval_service.DocPatchRequest) -> dict[str, Any]:
    return {
        "outcome": result.outcome,
        "approval_id": result.approval.approval_id if result.approval else None,
    }
