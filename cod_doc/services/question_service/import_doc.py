"""Import legacy ``type: open-question`` documents into the question entity.

Before OQM questions lived as documents. Two shapes occur in real corpora
(Restate, 2026-09):

- **single** — one document per question: sections *Question*, *Background*,
  *Options* (``### Option A: …`` / ``### Вариант A — …``), *Decision Matrix*,
  *Recommendation*, *Navigation* … Some documents nest those as ``## …``
  inside one section; nested headings are split out first, so both layouts
  map the same way.
- **registry** — one document listing many questions as ``### OQ-NNN Title``
  items under *Open Items* / *Resolved Archive*, plus a summary table
  ``| ID | Вопрос | Модуль-владелец | … | Блокирует задач | Статус | …``.
  Each item becomes its own question.

Mapping (single): the question section → ``question``; option headings →
options; the navigation section and the preamble give links (relative
``.md`` links resolve to documents/sections of the project, ``http(s)`` to
``url``); task ids in frontmatter ``blocking`` and in the preamble's
«Блокирует/Blocks» line become ``blocks`` edges; everything else, the
preamble included, is concatenated into ``context`` under its own heading,
so nothing the document said is lost.

The document is then deleted from the DB and its markdown file from disk —
a question must not live in two places. ``dry_run`` builds the same plan and
writes nothing. Re-importing a document already imported is refused.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from sqlalchemy import select

from cod_doc.domain.entities import (
    DocumentType,
    Priority,
    QuestionLinkKind,
    QuestionRelation,
    QuestionStatus,
)
from cod_doc.infra.models import (
    DocumentModel,
    OpenQuestionModel,
    ProjectModel,
    SectionModel,
)
from cod_doc.services import activity_service, doc_service, link_service

from . import crud, links

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_TASK_ID = re.compile(r"\b([A-Z]{2,5}-\d{3}[A-Z]?)\b")
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_OPTION_HEADING = re.compile(r"^(?:option|вариант)\b", re.IGNORECASE)
_REGISTRY_ITEM = re.compile(r"^### (OQ-\d+)\s+(.+?)\s*$", re.MULTILINE)
_BLOCKS_LINE = re.compile(r"(?:блокирует|blocks)\s*:?\**\s*:?(.*)$", re.IGNORECASE)
_TITLE_PREFIX = re.compile(r"^OQ\s+[—-]\s+")
_MIN_REGISTRY_ITEMS = 2
_OWNER_MAX = 64

_QUESTION_HEADINGS = ("question", "вопрос")
_OPTION_HEADINGS = ("options", "варианты")
_NAV_HEADINGS = ("navigation", "навигация")
_RESOLVED_HEADINGS = ("resolved", "решённые", "решенные", "archive")
_DECISION_HEADINGS = ("решение", "resolution", "decision")


class AlreadyImportedError(ValueError):
    """The document was imported before — its questions already exist."""


class NotAQuestionDocumentError(ValueError):
    """The document is not ``type: open-question``."""


@dataclass(slots=True)
class PlannedQuestion:
    title: str
    question: str
    context: str | None = None
    options: list[tuple[str, str | None]] = field(default_factory=list)
    links: list[tuple[QuestionLinkKind, str, QuestionRelation]] = field(default_factory=list)
    status: QuestionStatus = QuestionStatus.OPEN
    resolution: str | None = None
    owner: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ImportPlan:
    doc_key: str
    mode: str
    questions: list[PlannedQuestion]
    file_path: str | None
    incoming_links: int
    skipped_links: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ImportResult:
    plan: ImportPlan
    created: list[str] = field(default_factory=list)
    document_deleted: bool = False
    file_deleted: bool = False


# --------------------------------------------------------------------------- #
# text helpers                                                                 #
# --------------------------------------------------------------------------- #


def _norm(heading: str) -> str:
    return heading.strip().lower()


def _split_nested(heading: str, body: str) -> list[tuple[str, str]]:
    """``## Sub`` headings inside a section body become blocks of their own."""
    parts = re.split(r"^## +(.+?)\s*$", body, flags=re.MULTILINE)
    blocks: list[tuple[str, str]] = []
    if parts[0].strip():
        blocks.append((heading, parts[0].strip()))
    blocks.extend((parts[i], parts[i + 1].strip()) for i in range(1, len(parts), 2))
    return blocks


def _parse_options(body: str) -> tuple[list[tuple[str, str | None]], str]:
    """``### Option …`` headings → options; returns (options, leftover prose)."""
    parts = re.split(r"^### +(.+?)\s*$", body, flags=re.MULTILINE)
    leftover = parts[0].strip()
    options: list[tuple[str, str | None]] = []
    for i in range(1, len(parts), 2):
        title, text = parts[i], parts[i + 1].strip()
        if _OPTION_HEADING.match(title):
            options.append((title, text or None))
        else:
            leftover += f"\n\n### {title}\n\n{text}"
    return options, leftover.strip()


def _task_ids(text: str) -> list[str]:
    seen: list[str] = []
    for tid in _TASK_ID.findall(text):
        if tid not in seen:
            seen.append(tid)
    return seen


def _blocked_tasks(frontmatter: dict[str, object], preamble: str) -> list[str]:
    raw = frontmatter.get("blocking")
    found = _task_ids(" ".join(map(str, raw)) if isinstance(raw, list) else str(raw or ""))
    for line in preamble.splitlines():
        match = _BLOCKS_LINE.search(line)
        if match:
            found += [t for t in _task_ids(match.group(1)) if t not in found]
    return found


# --------------------------------------------------------------------------- #
# link resolution                                                              #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Linker:
    session: Session
    project_id: int
    doc_path: str
    skipped: list[str]

    def resolve(self, href: str) -> tuple[QuestionLinkKind, str] | None:
        if href.startswith(("http://", "https://")):
            return QuestionLinkKind.URL, href
        path, _, fragment = unquote(href).partition("#")
        if not path.endswith(".md"):
            self.skipped.append(href)
            return None
        target = posixpath.normpath(posixpath.join(posixpath.dirname(self.doc_path), path))
        doc_key = self.session.execute(
            select(DocumentModel.doc_key).where(
                DocumentModel.project_id == self.project_id,
                (DocumentModel.path == target) | (DocumentModel.doc_key == target[: -len(".md")]),
            )
        ).scalar_one_or_none()
        if doc_key is None:
            self.skipped.append(href)
            return None
        if fragment:
            return QuestionLinkKind.SECTION, f"{doc_key}#{fragment}"
        return QuestionLinkKind.DOCUMENT, doc_key

    def links_in(self, text: str) -> list[tuple[QuestionLinkKind, str, QuestionRelation]]:
        out: list[tuple[QuestionLinkKind, str, QuestionRelation]] = []
        for _label, href in _MD_LINK.findall(text):
            resolved = self.resolve(href)
            if resolved is not None:
                edge = (*resolved, QuestionRelation.SEE_ALSO)
                if edge not in out:
                    out.append(edge)
        return out


# --------------------------------------------------------------------------- #
# planning                                                                     #
# --------------------------------------------------------------------------- #


def _load(
    session: Session, project_id: int, doc_key: str
) -> tuple[DocumentModel, list[tuple[str, str, str]]]:
    doc = session.execute(
        select(DocumentModel).where(
            DocumentModel.project_id == project_id, DocumentModel.doc_key == doc_key
        )
    ).scalar_one_or_none()
    if doc is None:
        raise doc_service.DocumentNotFoundError(f"document {doc_key!r}")
    if doc.type != DocumentType.OPEN_QUESTION.value:
        raise NotAQuestionDocumentError(f"{doc_key} is {doc.type!r}, not open-question")
    sections = [
        (row.anchor, row.heading, row.body or "")
        for row in session.execute(
            select(SectionModel)
            .where(SectionModel.document_id == doc.row_id)
            .order_by(SectionModel.position)
        ).scalars()
    ]
    return doc, sections


def _plan_single(
    doc: DocumentModel,
    sections: list[tuple[str, str, str]],
    linker: _Linker,
    frontmatter: dict[str, object],
) -> PlannedQuestion:
    blocks = [b for _a, heading, body in sections for b in _split_nested(heading, body)]
    question: str | None = None
    options: list[tuple[str, str | None]] = []
    edges: list[tuple[QuestionLinkKind, str, QuestionRelation]] = []
    context_parts: list[str] = [doc.preamble.strip()] if (doc.preamble or "").strip() else []
    warnings: list[str] = []
    for heading, body in blocks:
        key = _norm(heading)
        if question is None and key.startswith(_QUESTION_HEADINGS):
            question = body
        elif key.startswith(_NAV_HEADINGS):
            edges += linker.links_in(body)
        elif key.startswith(_OPTION_HEADINGS) or _OPTION_HEADING.match(key):
            found, leftover = _parse_options(body)
            options += found
            if leftover:
                context_parts.append(f"## {heading}\n\n{leftover}")
        else:
            if key in _DECISION_HEADINGS:
                warnings.append(
                    f"section «{heading}» looks like a decision; it went to context and the "
                    "question stays open — resolve or drop it by hand"
                )
            context_parts.append(f"## {heading}\n\n{body}")
    edges += [e for e in linker.links_in(doc.preamble or "") if e not in edges]
    edges += [
        (QuestionLinkKind.TASK, tid, QuestionRelation.BLOCKS)
        for tid in _blocked_tasks(frontmatter, doc.preamble or "")
    ]
    owner = str(frontmatter.get("decision_owner") or doc.owner or "") or None
    return PlannedQuestion(
        title=_TITLE_PREFIX.sub("", doc.title).strip() or doc.doc_key,
        question=(question or doc.title).strip(),
        context="\n\n".join(context_parts).strip() or None,
        options=options,
        links=edges,
        owner=owner[:_OWNER_MAX] if owner else None,
        warnings=warnings,
    )


def _summary_rows(sections: list[tuple[str, str, str]]) -> dict[str, dict[str, str]]:
    """``| ID | Вопрос | … |`` tables keyed by OQ id, columns by header name."""
    rows: dict[str, dict[str, str]] = {}
    for _a, _h, body in sections:
        header: list[str] | None = None
        for line in body.splitlines():
            if not line.startswith("|"):
                header = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = [_norm(c) for c in cells]
            elif cells and re.fullmatch(r"OQ-\d+", cells[0]):
                rows[cells[0]] = dict(zip(header, cells, strict=False))
    return rows


def _registry_item(
    oq_id: str,
    title: str,
    body: str,
    *,
    resolved_section: bool,
    row: dict[str, str],
) -> PlannedQuestion:
    status_cell = _norm(row.get("статус", ""))
    inline = re.search(r"^- Статус:\s*(\w+)", body, re.MULTILINE)
    resolved = resolved_section or status_cell == "resolved"
    if inline and _norm(inline.group(1)) == "open":
        resolved = False
    blocks = [
        tid
        for tid in _task_ids(row.get("блокирует задач", ""))
        if f"{tid} (unblocked)" not in row.get("блокирует задач", "")
    ]
    owner = row.get("модуль-владелец") or None
    return PlannedQuestion(
        title=f"{oq_id} {title}",
        question=row.get("вопрос") or title,
        context=None if resolved else body.strip() or None,
        status=QuestionStatus.RESOLVED if resolved else QuestionStatus.OPEN,
        resolution=(body.strip() or "resolved in the registry") if resolved else None,
        links=[(QuestionLinkKind.TASK, t, QuestionRelation.BLOCKS) for t in blocks],
        owner=owner[:_OWNER_MAX] if owner else None,
    )


def _plan_registry(sections: list[tuple[str, str, str]]) -> list[PlannedQuestion]:
    rows = _summary_rows(sections)
    planned: list[PlannedQuestion] = []
    for _anchor, heading, body in sections:
        items = list(_REGISTRY_ITEM.finditer(body))
        resolved_section = any(word in _norm(heading) for word in _RESOLVED_HEADINGS)
        for i, match in enumerate(items):
            end = items[i + 1].start() if i + 1 < len(items) else len(body)
            planned.append(
                _registry_item(
                    match.group(1),
                    match.group(2),
                    body[match.end() : end],
                    resolved_section=resolved_section,
                    row=rows.get(match.group(1), {}),
                )
            )
    return planned


def plan_import(session: Session, *, project_id: int, doc_key: str) -> ImportPlan:
    """What an import of ``doc_key`` would create; writes nothing."""
    doc, sections = _load(session, project_id, doc_key)
    frontmatter = doc.frontmatter_json if isinstance(doc.frontmatter_json, dict) else {}
    skipped: list[str] = []
    linker = _Linker(session, project_id, doc.path or f"{doc_key}.md", skipped)
    registry_items = sum(len(_REGISTRY_ITEM.findall(body)) for _a, _h, body in sections)
    if registry_items >= _MIN_REGISTRY_ITEMS:
        mode, questions = "registry", _plan_registry(sections)
    else:
        mode, questions = "single", [_plan_single(doc, sections, linker, frontmatter)]
    incoming = link_service.list_incoming_for_doc(session, project_id, doc_key)
    return ImportPlan(
        doc_key=doc_key,
        mode=mode,
        questions=questions,
        file_path=doc.path,
        incoming_links=len(incoming),
        skipped_links=skipped,
    )


# --------------------------------------------------------------------------- #
# apply                                                                        #
# --------------------------------------------------------------------------- #


def _create_one(
    session: Session, project_id: int, doc_key: str, planned: PlannedQuestion, author: str
) -> str:
    qid = crud.create(
        session,
        project_id=project_id,
        title=planned.title,
        question=planned.question,
        context=planned.context,
        owner=planned.owner,
        priority=Priority.MEDIUM,
        options=planned.options,
        source_doc_key=doc_key,
        author=author,
        reason=f"import {doc_key}",
    ).question_id
    for kind, ref, relation in planned.links:
        links.link(
            session,
            project_id=project_id,
            question_id=qid,
            to_kind=kind,
            to_ref=ref,
            relation=relation,
            author=author,
        )
    if planned.status is QuestionStatus.RESOLVED:
        crud.resolve(
            session,
            project_id=project_id,
            question_id=qid,
            resolution=planned.resolution,
            author=author,
            reason=f"import {doc_key}",
        )
    return qid


def _delete_file(session: Session, project_id: int, rel_path: str | None) -> bool:
    root = session.execute(
        select(ProjectModel.root_path).where(ProjectModel.row_id == project_id)
    ).scalar_one_or_none()
    if not root or not rel_path:
        return False
    target = (Path(root) / rel_path).resolve()
    if not target.is_relative_to(Path(root).resolve()) or not target.is_file():
        return False
    target.unlink()
    return True


def import_document(
    session: Session,
    *,
    project_id: int,
    doc_key: str,
    author: str,
    delete_document: bool = True,
    dry_run: bool = False,
) -> ImportResult:
    """Turn an ``open-question`` document into question rows, then remove it.

    ``dry_run`` returns the plan only. With ``delete_document=False`` the
    document stays (both copies then exist — meant for a trial run).
    """
    already = session.execute(
        select(OpenQuestionModel.question_id).where(
            OpenQuestionModel.project_id == project_id,
            OpenQuestionModel.source_doc_key == doc_key,
        )
    ).first()
    if already is not None:
        raise AlreadyImportedError(f"{doc_key} was already imported (e.g. {already[0]})")
    plan = plan_import(session, project_id=project_id, doc_key=doc_key)
    result = ImportResult(plan=plan)
    if dry_run:
        return result

    result.created = [_create_one(session, project_id, doc_key, q, author) for q in plan.questions]
    if delete_document:
        doc_service.delete(
            session,
            project_id=project_id,
            doc_key=doc_key,
            author=author,
            reason=f"moved to open questions {', '.join(result.created)}",
        )
        result.document_deleted = True
        result.file_deleted = _delete_file(session, project_id, plan.file_path)

    activity_service.emit_for_write(
        session,
        project_id,
        "question.imported",
        author,
        scope_kind="doc",
        scope_id=doc_key,
        payload={
            "mode": plan.mode,
            "questions": result.created,
            "document_deleted": result.document_deleted,
            "file_deleted": result.file_deleted,
        },
        summary=f"{doc_key} → {len(result.created)} question(s)",
    )
    session.flush()
    return result
