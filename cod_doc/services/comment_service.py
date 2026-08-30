"""DocComment service — section-anchored and document-level review notes.

Comments are user-authored meta on a document: short notes pinned to a
section (Google-Docs-style side bubbles) or to the whole doc. They are
not versioned (no Revision rows) — they exist in their own lane, survive
section edits, and are removed via explicit user action (resolve/delete)
or via the AI batch-rework flow that consumes them.

Public API:
- `create` — add a new comment (section or doc-level).
- `list_for_document` — read all comments for a doc, ordered by created.
- `update_status` — flip status to 'open' | 'resolved' | 'applied'.
- `delete` — hard-remove a comment.
- `apply_open_with_ai` — batch the open comments and ask the LLM to
  produce a revised body per affected section; returns drafts the caller
  decides what to do with (patch_section happens at the route layer so
  the user can preview / approve).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.infra.models import DocCommentModel, DocumentModel, SectionModel
from cod_doc.services import activity_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.config import Config


VALID_STATUS = frozenset({"open", "resolved", "applied"})


class CommentNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class DocComment:
    row_id: int
    document_id: int
    section_id: int | None
    anchor: str | None
    quote: str | None
    body: str
    author: str
    status: str
    created: datetime
    last_updated: datetime


def _to_domain(m: DocCommentModel) -> DocComment:
    return DocComment(
        row_id=m.row_id,
        document_id=m.document_id,
        section_id=m.section_id,
        anchor=m.anchor,
        quote=m.quote,
        body=m.body,
        author=m.author,
        status=m.status,
        created=m.created,
        last_updated=m.last_updated,
    )


def create(
    session: Session,
    *,
    document_id: int,
    body: str,
    author: str = "human:web",
    section_id: int | None = None,
    anchor: str | None = None,
    quote: str | None = None,
) -> DocComment:
    """Persist a new comment. ``section_id`` None → document-level comment.

    If ``section_id`` is given but ``anchor`` isn't, the anchor is looked
    up so the bubble can be rendered without an extra join later.
    """
    if not body.strip():
        raise ValueError("Comment body is empty.")

    doc = session.get(DocumentModel, document_id)
    if doc is None:
        raise ValueError(f"Document #{document_id} not found")

    if section_id is not None and anchor is None:
        sec = session.get(SectionModel, section_id)
        if sec is None:
            raise ValueError(f"Unknown section #{section_id}")
        if sec.document_id != document_id:
            raise ValueError(f"Section #{section_id} does not belong to document #{document_id}")
        anchor = sec.anchor

    now = datetime.now(UTC)
    model = DocCommentModel(
        document_id=document_id,
        section_id=section_id,
        anchor=anchor,
        quote=(quote or None),
        body=body.strip(),
        author=author,
        status="open",
        created=now,
        last_updated=now,
    )
    session.add(model)
    session.flush()
    activity_service.emit_for_write(
        session,
        doc.project_id,
        "comment.created",
        author,
        scope_kind="comment",
        scope_id=str(model.row_id),
        payload={
            "document_id": document_id,
            "section_id": section_id,
            "anchor": anchor,
        },
        summary=f"Comment #{model.row_id} created on document {document_id}",
    )
    return _to_domain(model)


def list_for_document(
    session: Session,
    document_id: int,
    *,
    include_resolved: bool = True,
) -> list[DocComment]:
    """Return all comments for the doc, newest last (creation order)."""
    stmt = select(DocCommentModel).where(DocCommentModel.document_id == document_id)
    if not include_resolved:
        stmt = stmt.where(DocCommentModel.status == "open")
    stmt = stmt.order_by(DocCommentModel.created)
    return [_to_domain(m) for m in session.execute(stmt).scalars()]


def get(session: Session, comment_id: int) -> DocComment | None:
    m = session.get(DocCommentModel, comment_id)
    return _to_domain(m) if m else None


def update_status(session: Session, *, comment_id: int, new_status: str) -> DocComment:
    if new_status not in VALID_STATUS:
        raise ValueError(f"Unknown status: {new_status}")
    m = session.get(DocCommentModel, comment_id)
    if m is None:
        raise CommentNotFoundError(f"comment #{comment_id}")
    m.status = new_status
    m.last_updated = datetime.now(UTC)
    session.flush()

    doc = session.get(DocumentModel, m.document_id)
    project_id = doc.project_id if doc is not None else None
    if project_id is not None:
        activity_service.emit_for_write(
            session,
            project_id,
            "comment.status_changed",
            "system",
            scope_kind="comment",
            scope_id=str(comment_id),
            payload={"document_id": m.document_id, "new_status": new_status},
            summary=f"Comment #{comment_id} status → {new_status}",
        )
    return _to_domain(m)


def delete(session: Session, comment_id: int) -> bool:
    m = session.get(DocCommentModel, comment_id)
    if m is None:
        return False
    doc = session.get(DocumentModel, m.document_id)
    project_id = doc.project_id if doc is not None else None
    session.delete(m)
    session.flush()
    if project_id is not None:
        activity_service.emit_for_write(
            session,
            project_id,
            "comment.deleted",
            "system",
            scope_kind="comment",
            scope_id=str(comment_id),
            payload={"document_id": m.document_id},
            summary=f"Comment #{comment_id} deleted",
        )
    return True


# ── AI batch apply ─────────────────────────────────────────────────────────

_APPLY_SYSTEM_PROMPT = """\
You are a senior technical-documentation editor. The user gives you a
section of a document together with a list of review comments. Rewrite
the section body so that every comment is addressed.

Rules:
- Preserve markdown formatting (headings, lists, fences, links, tables).
- Preserve the original language of the section.
- Address each comment substantively — do not just paraphrase. If a
  comment asks to remove something, remove it; if it asks for a fact or
  example, add it.
- Preserve sections of the original body that the comments do not touch.
- Return ONLY the rewritten body — no preface, no apology, no JSON wrapper.
"""


@dataclass(slots=True)
class SectionRework:
    section_id: int | None
    anchor: str | None
    heading: str
    old_body: str
    new_body: str
    comment_ids: list[int]


@dataclass(slots=True)
class DocRework:
    section_reworks: list[SectionRework]
    doc_level_summary: str
    doc_level_comment_ids: list[int]
    skipped: list[dict[str, Any]]


def _call_rewrite(body: str, comments: list[str], cfg: Config) -> str:
    from cod_doc.services.ai_text import AIBackendError

    if not cfg.api_key:
        raise AIBackendError("LLM backend not configured: set the API key in /settings.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIBackendError("openai package is not installed.") from exc

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    comments_block = "\n".join(f"- {c}" for c in comments)
    user_msg = f"Comments to apply:\n{comments_block}\n\nSection body:\n{body}"
    try:
        completion = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": _APPLY_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=cfg.max_tokens,
            temperature=0.3,
        )
    except Exception as exc:
        raise AIBackendError(f"LLM call failed: {exc}") from exc
    content = (completion.choices[0].message.content or "").strip()
    if not content:
        raise AIBackendError("LLM returned empty content.")
    if content.startswith("```"):
        lines = content.splitlines()
        lines = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
        content = "\n".join(lines)
    return content


def apply_open_with_ai(
    session: Session,
    *,
    document_id: int,
    cfg: Config,
) -> DocRework:
    """Group open comments by section and ask the LLM to rewrite each
    section body in light of the comments attached to it.

    Document-level comments are returned as ``doc_level_summary`` so the
    UI can surface them; they do not modify any single section directly.
    The caller decides whether to apply the per-section drafts (typically
    by calling ``doc_service.patch_section``) and to flip the comment
    statuses to 'applied'.

    The DB session is read-only here — no comments or sections are
    mutated. Mutation happens at the route layer once the user accepts.
    """
    doc = session.get(DocumentModel, document_id)
    if doc is None:
        raise ValueError(f"Document #{document_id} not found")

    open_comments = [
        c
        for c in list_for_document(session, document_id, include_resolved=False)
        if c.status == "open"
    ]

    # Bucket by section_id (None bucket → doc-level).
    buckets: dict[int | None, list[DocComment]] = {}
    for c in open_comments:
        buckets.setdefault(c.section_id, []).append(c)

    section_reworks: list[SectionRework] = []
    skipped: list[dict[str, Any]] = []
    for section_id, comments in buckets.items():
        if section_id is None:
            continue
        sec = session.get(SectionModel, section_id)
        if sec is None:
            skipped.append(
                {
                    "section_id": section_id,
                    "reason": "section no longer exists",
                    "comment_ids": [c.row_id for c in comments],
                }
            )
            continue
        comment_texts = [(f"[quote: {c.quote!r}] " if c.quote else "") + c.body for c in comments]
        try:
            new_body = _call_rewrite(sec.body, comment_texts, cfg)
        except Exception as exc:
            skipped.append(
                {
                    "section_id": section_id,
                    "anchor": sec.anchor,
                    "heading": sec.heading,
                    "reason": f"AI call failed: {exc}",
                    "comment_ids": [c.row_id for c in comments],
                }
            )
            continue
        section_reworks.append(
            SectionRework(
                section_id=section_id,
                anchor=sec.anchor,
                heading=sec.heading,
                old_body=sec.body,
                new_body=new_body,
                comment_ids=[c.row_id for c in comments],
            )
        )

    doc_level = buckets.get(None, [])
    doc_summary = "\n\n".join(c.body for c in doc_level)
    return DocRework(
        section_reworks=section_reworks,
        doc_level_summary=doc_summary,
        doc_level_comment_ids=[c.row_id for c in doc_level],
        skipped=skipped,
    )
