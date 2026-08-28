"""OBI-040: unified search across docs / tasks / stories / ADRs via FTS5.

Public API:
- ``reindex_all(session, project_id)`` — wipe + repopulate index.
- ``search(session, project_id, query, *, scope=None, limit=20)`` —
  ranked hits (bm25) grouped by kind.

Index entries:
- ``kind='task'``     ref=task_id (e.g. ADR-001)  title=task.title         body=description+acceptance
- ``kind='doc'``      ref=doc_key                  title=document.title     body=preamble + all section bodies
- ``kind='story'``    ref=story_id                 title=narrative (truncated)  body=narrative + acceptance criteria
- ``kind='adr'``      ref=adr_id                   title=adr.title          body=context+decision+alternatives+consequences
- ``kind='finding'``  ref=finding_uid              title=finding.title      body=source + kind + path + body

FTS5 BM25 ranking by default; we surface the relevance score with each
hit so the UI can show a confidence bar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select, text

from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    FindingModel,
    SectionModel,
    StoryAcceptanceModel,
    TaskModel,
    UserStoryModel,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_VALID_SCOPES = frozenset({"task", "doc", "story", "adr", "finding"})


def _wipe(session: Session, project_id: int) -> None:
    session.execute(
        text("DELETE FROM db_search_idx WHERE project_id = :p"),
        {"p": project_id},
    )


def _insert(
    session: Session,
    *,
    kind: str,
    ref: str,
    project_id: int,
    title: str,
    body: str,
) -> None:
    session.execute(
        text(
            "INSERT INTO db_search_idx (kind, ref, project_id, title, body) "
            "VALUES (:kind, :ref, :pid, :title, :body)"
        ),
        {
            "kind": kind,
            "ref": ref,
            "pid": project_id,
            "title": title or "",
            "body": body or "",
        },
    )


def _index_doc(session: Session, project_id: int, d: DocumentModel) -> None:
    """Insert one FTS row for a document (preamble + section bodies)."""
    sections = list(
        session.execute(
            select(SectionModel)
            .where(SectionModel.document_id == d.row_id)
            .order_by(SectionModel.position)
        ).scalars()
    )
    body = "\n\n".join(
        [d.preamble or ""] + [(s.heading or "") + "\n" + (s.body or "") for s in sections]
    )
    _insert(
        session,
        kind="doc",
        ref=d.doc_key,
        project_id=project_id,
        title=d.title or "",
        body=body.strip(),
    )


def upsert_doc(session: Session, *, project_id: int, doc_key: str) -> None:
    """ADO-030: incremental FTS update for a single document.

    Called from the import/update path so freshly imported docs are
    searchable without a manual ``--reindex``. Delete-then-insert keeps it
    idempotent; a missing document just clears the stale row.
    """
    session.execute(
        text("DELETE FROM db_search_idx WHERE project_id = :p AND kind = 'doc' AND ref = :r"),
        {"p": project_id, "r": doc_key},
    )
    d = session.execute(
        select(DocumentModel).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()
    if d is not None:
        _index_doc(session, project_id, d)


def reindex_all(session: Session, project_id: int) -> dict[str, int]:
    """Drop project rows from index, then rebuild from canonical tables."""
    _wipe(session, project_id)

    counts: dict[str, int] = {"task": 0, "doc": 0, "story": 0, "adr": 0, "finding": 0}

    # Tasks — description + acceptance + blocked_reason in body.
    for t in session.execute(select(TaskModel).where(TaskModel.project_id == project_id)).scalars():
        body_parts = [t.description or "", t.acceptance or "", t.blocked_reason or ""]
        _insert(
            session,
            kind="task",
            ref=t.task_id,
            project_id=project_id,
            title=t.title or "",
            body="\n".join(b for b in body_parts if b),
        )
        counts["task"] += 1

    # Documents — preamble + concatenated section bodies.
    for d in session.execute(
        select(DocumentModel).where(DocumentModel.project_id == project_id)
    ).scalars():
        _index_doc(session, project_id, d)
        counts["doc"] += 1

    # Stories — narrative + acceptance criteria.
    for s in session.execute(
        select(UserStoryModel).where(UserStoryModel.project_id == project_id)
    ).scalars():
        crits = list(
            session.execute(
                select(StoryAcceptanceModel.criterion)
                .where(StoryAcceptanceModel.story_id == s.row_id)
                .order_by(StoryAcceptanceModel.position)
            ).scalars()
        )
        body = (s.narrative or "") + "\n\n" + "\n- ".join(crits)
        title = (s.narrative or "")[:120]
        _insert(
            session,
            kind="story",
            ref=s.story_id,
            project_id=project_id,
            title=title,
            body=body,
        )
        counts["story"] += 1

    # ADRs — full decision payload.
    for a in session.execute(select(ADRModel).where(ADRModel.project_id == project_id)).scalars():
        body = "\n\n".join(
            filter(
                None,
                [
                    a.context,
                    a.decision,
                    a.alternatives,
                    a.consequences,
                ],
            )
        )
        _insert(
            session,
            kind="adr",
            ref=a.adr_id,
            project_id=project_id,
            title=a.title or "",
            body=body,
        )
        counts["adr"] += 1

    # Findings — source + kind + path + body.
    for f in session.execute(
        select(FindingModel).where(FindingModel.project_id == project_id)
    ).scalars():
        body_parts = [f.source or "", f.kind or "", f.path or "", f.body or ""]
        _insert(
            session,
            kind="finding",
            ref=f.finding_uid,
            project_id=project_id,
            title=f.title or "",
            body="\n".join(b for b in body_parts if b),
        )
        counts["finding"] += 1

    session.flush()
    counts["total"] = sum(counts[k] for k in ("task", "doc", "story", "adr", "finding"))
    return counts


def _escape_fts(query: str) -> str:
    """Make user query safe for FTS5 MATCH.

    FTS5 has its own mini-DSL (``AND OR NOT NEAR``, double quotes for
    phrases). For a free-text input box we strip dangerous tokens and
    fall back to a phrase if anything goes wrong.
    """
    q = query.strip()
    if not q:
        return ""
    # Drop FTS5 operators / unbalanced quotes — treat input as plain.
    safe = q.replace('"', "").replace("(", " ").replace(")", " ")
    # Quote each token for prefix-safe matching, append * for prefix.
    tokens = [tok for tok in safe.split() if tok]
    if not tokens:
        return ""
    return " ".join(f'"{t}"*' for t in tokens)


def search(
    session: Session,
    *,
    project_id: int,
    query: str,
    scope: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return ranked hits grouped by ``kind``.

    Shape::

        {
          "query": "...",
          "total": N,
          "by_kind": {
            "task": [{ref, title, snippet, score}, ...],
            "doc":  [...],
            "story": [...],
            "adr":  [...]
          }
        }
    """
    if scope is not None and scope not in _VALID_SCOPES:
        raise ValueError(f"invalid scope {scope!r}; expected one of {sorted(_VALID_SCOPES)}")

    fts_query = _escape_fts(query)
    if not fts_query:
        return {"query": query, "total": 0, "by_kind": {k: [] for k in sorted(_VALID_SCOPES)}}

    # Column index -1 lets FTS5 pick the column with the strongest match
    # — so a title-only hit still shows a useful snippet, and body matches
    # surface naturally too.
    sql = """
        SELECT kind, ref, title,
               snippet(db_search_idx, -1, '<mark>', '</mark>', '…', 12) AS snippet,
               bm25(db_search_idx) AS score
        FROM db_search_idx
        WHERE project_id = :pid AND db_search_idx MATCH :q
    """
    params: dict[str, Any] = {"pid": project_id, "q": fts_query, "lim": limit}
    if scope is not None:
        sql += " AND kind = :scope"
        params["scope"] = scope
    sql += " ORDER BY score LIMIT :lim"

    rows = session.execute(text(sql), params).all()
    by_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in sorted(_VALID_SCOPES)}
    for r in rows:
        by_kind.setdefault(r.kind, []).append(
            {
                "ref": r.ref,
                "title": r.title,
                "snippet": r.snippet,
                "score": round(float(r.score), 4),
            }
        )
    return {"query": query, "total": len(rows), "by_kind": by_kind}
