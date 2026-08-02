"""Semantic link-suggestion engine (PCA-422).

Uses the existing ChromaDB index (file-level embeddings, built by
``cod_doc.core.reindex``) to find candidate link targets for sections
that have no outgoing links, then stores suggestions in ``link_suggestion``.

Algorithm (per proposal 15 §2.3.2):
1. Query top-K similar docs from ChromaDB using section body as query text.
2. Map each hit (file path) to a Document in the DB.
3. Rerank by lexical signals (+title-mention, +doc_key-mention).
4. Filter by similarity threshold (default 0.78).
5. Upsert top-N suggestions into ``link_suggestion`` (idempotent).

Design constraints:
- No new embedding stack — reuses ``reindex.get_collection()``.
- Suggestions are *never* written to the ``link`` table automatically.
- Running the suggester multiple times is safe (upsert on triple key).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.config import Config

log = logging.getLogger("cod_doc.services.link_service.semantic")

DEFAULT_THRESHOLD = 0.78
DEFAULT_K = 20  # candidates from ChromaDB
DEFAULT_TOP_N = 5  # suggestions stored per section


@dataclass
class SuggestionResult:
    """Summary returned by backfill_project."""

    sections_processed: int = 0
    suggestions_created: int = 0
    suggestions_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections_processed": self.sections_processed,
            "suggestions_created": self.suggestions_created,
            "suggestions_skipped": self.suggestions_skipped,
            "errors": self.errors,
        }


def _rerank_score(
    base_score: float,
    section_body: str,
    candidate_title: str,
    candidate_doc_key: str,
) -> float:
    """Apply lexical bonuses from proposal 15 §2.3.2."""
    body_lower = section_body.lower()
    title_lower = candidate_title.lower()
    key_lower = candidate_doc_key.lower()

    score = base_score
    if title_lower and title_lower in body_lower:
        score += 0.10
    if key_lower and key_lower in body_lower:
        score += 0.15
    return min(score, 1.0)


def _upsert_suggestion(
    session: Session,
    *,
    from_section_id: int,
    to_doc_key: str,
    to_section_id: int | None,
    score: float,
    evidence: dict[str, Any],
) -> bool:
    """Insert or update a suggestion row.  Returns True if created, False if updated."""
    from sqlalchemy import select, update

    from cod_doc.infra.models.link_suggestions import LinkSuggestionModel

    now = datetime.now(UTC)
    stmt = select(LinkSuggestionModel).where(
        LinkSuggestionModel.from_section_id == from_section_id,
        LinkSuggestionModel.to_doc_key == to_doc_key,
        LinkSuggestionModel.to_section_id == to_section_id,
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        # Only update if score improved; never overwrite accepted/rejected state.
        if existing.state == "pending" and score > existing.score:
            session.execute(
                update(LinkSuggestionModel)
                .where(LinkSuggestionModel.row_id == existing.row_id)
                .values(score=score, evidence=json.dumps(evidence), updated_at=now)
            )
        return False  # not a new row

    session.add(
        LinkSuggestionModel(
            from_section_id=from_section_id,
            to_doc_key=to_doc_key,
            to_section_id=to_section_id,
            score=score,
            evidence=json.dumps(evidence),
            state="pending",
            created_at=now,
            updated_at=now,
        )
    )
    return True


def suggest_for_section(
    session: Session,
    section_id: int,
    config: Config,
    *,
    k: int = DEFAULT_K,
    threshold: float = DEFAULT_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Generate semantic link suggestions for one section.

    Returns a list of suggestion dicts (regardless of *dry_run*).
    When *dry_run* is False, suggestions are upserted into ``link_suggestion``.
    """
    from sqlalchemy import select

    from cod_doc.core import reindex as _reindex
    from cod_doc.infra.models.documents import DocumentModel, SectionModel

    # 1. Load section body
    sec = session.get(SectionModel, section_id)
    if sec is None or not sec.body:
        return []

    # Get the parent document to know the project context
    doc = session.get(DocumentModel, sec.document_id)
    if doc is None:
        return []

    # 2. Query ChromaDB
    try:
        collection = _reindex.get_collection(
            config.chroma_path,
            config.api_key,
            config.base_url,
            config.embedding_model,
            config.embedding_backend,
        )
        # PCA-930: explicit check — querying an empty collection raises an error.
        if collection.count() == 0:
            log.info(
                "ChromaDB collection is empty — run 'cod-doc reindex' before suggest. "
                "section_id=%s skipped.",
                section_id,
            )
            return []
        results = collection.query(
            query_texts=[sec.body[:4096]],
            n_results=min(k, max(1, collection.count())),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        log.warning("ChromaDB query failed for section_id=%s: %s", section_id, exc)
        return []

    hits_docs = (results.get("documents") or [[]])[0]
    hits_metas = (results.get("metadatas") or [[]])[0]
    hits_distances = (results.get("distances") or [[]])[0]

    # 3. Resolve file paths → Document in DB, rerank, filter
    suggestions: list[dict[str, Any]] = []
    seen_doc_keys: set[str] = set()

    for _snippet, meta, dist in zip(hits_docs, hits_metas, hits_distances, strict=False):
        base_score = round(1.0 - dist, 4)
        if base_score < threshold:
            continue

        rel_path: str = meta.get("path", "")
        if not rel_path:
            continue

        # Map relative path → doc_key (same rule as _derive_doc_key in import_service)
        candidate_key = rel_path
        if candidate_key.endswith(".md"):
            candidate_key = candidate_key[:-3]
        if candidate_key.startswith("docs/"):
            candidate_key = candidate_key[5:]

        # Skip self
        if candidate_key == doc.doc_key:
            continue
        if candidate_key in seen_doc_keys:
            continue
        seen_doc_keys.add(candidate_key)

        # Look up candidate document
        stmt = select(DocumentModel).where(
            DocumentModel.project_id == doc.project_id,
            DocumentModel.doc_key == candidate_key,
        )
        cand_doc = session.execute(stmt).scalar_one_or_none()
        if cand_doc is None:
            continue

        reranked = _rerank_score(base_score, sec.body, cand_doc.title or "", cand_doc.doc_key)
        if reranked < threshold:
            continue

        evidence = {
            "base_score": base_score,
            "final_score": reranked,
            "path": rel_path,
        }
        suggestions.append(
            {
                "from_section_id": section_id,
                "to_doc_key": cand_doc.doc_key,
                "to_section_id": None,
                "score": reranked,
                "evidence": evidence,
            }
        )

    # 4. Sort and cap
    suggestions.sort(key=lambda s: s["score"], reverse=True)
    suggestions = suggestions[:top_n]

    # 5. Persist
    if not dry_run:
        for s in suggestions:
            _upsert_suggestion(
                session,
                from_section_id=s["from_section_id"],
                to_doc_key=s["to_doc_key"],
                to_section_id=s["to_section_id"],
                score=s["score"],
                evidence=s["evidence"],
            )

    return suggestions


def backfill_project(
    session: Session,
    project_id: int,
    config: Config,
    *,
    apply_above: float | None = None,
    dry_run: bool = False,
    threshold: float = DEFAULT_THRESHOLD,
    k: int = DEFAULT_K,
    top_n: int = DEFAULT_TOP_N,
) -> SuggestionResult:
    """Run semantic suggestion for every section of the project.

    When *apply_above* is set, suggestions scoring above that value
    are automatically accepted (state → 'accepted') after being stored.
    When *dry_run* is True, nothing is written to the DB.
    """
    from sqlalchemy import select

    from cod_doc.infra.models.documents import DocumentModel, SectionModel

    result = SuggestionResult()

    # Collect all section IDs for the project in a stable order
    stmt = (
        select(SectionModel.row_id)
        .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
        .where(DocumentModel.project_id == project_id)
        .order_by(SectionModel.row_id)
    )
    section_ids = [row[0] for row in session.execute(stmt)]

    for sid in section_ids:
        result.sections_processed += 1
        try:
            suggs = suggest_for_section(
                session,
                sid,
                config,
                k=k,
                threshold=threshold,
                top_n=top_n,
                dry_run=dry_run,
            )
            created = len(suggs)
            result.suggestions_created += created

            if not dry_run and apply_above is not None and suggs:
                _auto_accept_above(session, sid, apply_above)

        except Exception as exc:
            result.errors.append(f"section {sid}: {exc}")
            log.warning("suggest_for_section failed for section_id=%s: %s", sid, exc)

    return result


def _auto_accept_above(session: Session, from_section_id: int, threshold: float) -> None:
    """Accept pending suggestions with score > threshold for a section."""
    from sqlalchemy import update

    from cod_doc.infra.models.link_suggestions import LinkSuggestionModel

    session.execute(
        update(LinkSuggestionModel)
        .where(
            LinkSuggestionModel.from_section_id == from_section_id,
            LinkSuggestionModel.state == "pending",
            LinkSuggestionModel.score > threshold,
        )
        .values(state="accepted", updated_at=datetime.now(UTC))
    )


def list_suggestions(
    session: Session,
    *,
    from_section_id: int | None = None,
    state: str = "pending",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return suggestion rows as plain dicts."""
    from sqlalchemy import select

    from cod_doc.infra.models.link_suggestions import LinkSuggestionModel

    stmt = select(LinkSuggestionModel).where(LinkSuggestionModel.state == state)
    if from_section_id is not None:
        stmt = stmt.where(LinkSuggestionModel.from_section_id == from_section_id)
    stmt = stmt.order_by(LinkSuggestionModel.score.desc()).limit(limit)

    rows = session.execute(stmt).scalars().all()
    return [
        {
            "row_id": r.row_id,
            "from_section_id": r.from_section_id,
            "to_doc_key": r.to_doc_key,
            "to_section_id": r.to_section_id,
            "score": r.score,
            "evidence": json.loads(r.evidence) if r.evidence else {},
            "state": r.state,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def list_suggestions_for_document(
    session: Session,
    *,
    project_id: int,
    document_id: int,
    state: str = "pending",
) -> list[dict[str, Any]]:
    """Return suggestion rows grouped per section for a single document.

    Each row carries ``section_id`` so the caller can group by section.
    Used by the web doc-show page to render the «Suggested links» panel.
    """
    from sqlalchemy import select

    from cod_doc.infra.models.documents import SectionModel
    from cod_doc.infra.models.link_suggestions import LinkSuggestionModel

    sec_ids = (
        session.execute(select(SectionModel.row_id).where(SectionModel.document_id == document_id))
        .scalars()
        .all()
    )
    if not sec_ids:
        return []

    rows = (
        session.execute(
            select(LinkSuggestionModel)
            .where(
                LinkSuggestionModel.from_section_id.in_(sec_ids),
                LinkSuggestionModel.state == state,
            )
            .order_by(
                LinkSuggestionModel.from_section_id,
                LinkSuggestionModel.score.desc(),
            )
        )
        .scalars()
        .all()
    )

    return [
        {
            "row_id": r.row_id,
            "from_section_id": r.from_section_id,
            "to_doc_key": r.to_doc_key,
            "to_section_id": r.to_section_id,
            "score": r.score,
        }
        for r in rows
    ]


def get_suggestion(session: Session, row_id: int) -> dict[str, Any] | None:
    """Return a single suggestion row as a plain dict, or None."""
    from cod_doc.infra.models.link_suggestions import LinkSuggestionModel

    r = session.get(LinkSuggestionModel, row_id)
    if r is None:
        return None
    return {
        "row_id": r.row_id,
        "from_section_id": r.from_section_id,
        "to_doc_key": r.to_doc_key,
        "to_section_id": r.to_section_id,
        "score": r.score,
        "state": r.state,
    }


def update_suggestion_state(
    session: Session,
    row_id: int,
    new_state: str,
) -> bool:
    """Change state of a suggestion (pending → accepted | rejected).  Returns True if found."""
    from sqlalchemy import update

    from cod_doc.infra.models.link_suggestions import LinkSuggestionModel

    if new_state not in ("accepted", "rejected", "pending"):
        raise ValueError(f"Invalid state: {new_state!r}")

    row = session.get(LinkSuggestionModel, row_id)
    if row is None:
        return False

    session.execute(
        update(LinkSuggestionModel)
        .where(LinkSuggestionModel.row_id == row_id)
        .values(state=new_state, updated_at=datetime.now(UTC))
    )
    return True
