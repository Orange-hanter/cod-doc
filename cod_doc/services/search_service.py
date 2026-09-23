"""OBI-040: unified search across docs / tasks / stories / ADRs via FTS5.

Public API:
- ``reindex_all(session, project_id)`` — wipe + repopulate index.
- ``ensure_index(session, project_id)`` — RFC 25 §3.2 (CUR-007): reindex
  only if the index is still empty (used by ``ctx_search`` for lazy bootstrap).
- ``search(session, project_id, query, *, scope=None, limit=20,
  project_ids=None)`` — ranked hits (bm25) grouped by kind. ``limit`` is
  applied **per kind** (CUR-011), so a doc-heavy corpus cannot crowd
  task/adr/... hits out of the result entirely. ``project_ids`` widens the
  query to several projects of the **same** DB (CUR-013 / RFC 22 §3.6).
- ``resolve_cross_project_ids(session, project=..., projects=[...])`` —
  slug → row_id for the extra projects of a cross-project search, with the
  shared-``db_url`` (hub) precondition enforced.

Index entries:
- ``kind='task'``     ref=task_id (e.g. ADR-001)  title=task.title         body=description+acceptance
- ``kind='doc'``      ref=doc_key                  title=document.title     body=preamble + all section bodies
- ``kind='story'``    ref=story_id                 title=narrative (truncated)  body=narrative + acceptance criteria
- ``kind='adr'``      ref=adr_id                   title=adr.title          body=context+decision+alternatives+consequences
- ``kind='finding'``  ref=finding_uid              title=finding.title      body=source + kind + path + body

FTS5 BM25 ranking by default; we surface the relevance score with each
hit so the UI can show a confidence bar.

CUR-010: ``search``/``ensure_index``/``reindex_all`` raise
``SearchIndexMissing`` (instead of a raw ``sqlalchemy.exc.OperationalError``)
when ``db_search_idx`` doesn't exist yet — a DB that predates migration
0023. Presentation surfaces (CLI/REST/web) turn it into a clean error;
MCP's ``ctx_search`` lets it propagate as-is.

CUR-012 (RFC 25 §C): the index is maintained **incrementally** from the
write path, not only by ``reindex_all``. Two generic entry points do the
work — ``upsert_entity`` / ``delete_entity`` — plus per-kind hooks
(``index_task`` / ``index_story`` / ``index_adr`` / ``index_finding``)
that services call inside their own mutation transaction. Before this,
only documents had an incremental path (ADO-030), so tasks, stories,
ADRs and findings reached the index only after somebody remembered to run
``search --reindex``: on the live DB that meant 63 indexed rows against
442 tasks, and ``ensure_index`` could not repair it because it only fires
on a *completely* empty index.

The payload of each row (title + body) is built by one ``_*_payload``
function per kind, shared by ``reindex_all`` and the incremental hooks —
a full reindex and an incremental update therefore cannot drift apart.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    FindingModel,
    ProjectModel,
    SectionModel,
    StoryAcceptanceModel,
    TaskModel,
    UserStoryModel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from cod_doc.config import Config


_LOG = logging.getLogger(__name__)

_VALID_SCOPES = frozenset({"task", "doc", "story", "adr", "finding"})

# A story has no title column — the narrative's opening is used instead.
_STORY_TITLE_BUDGET = 120

# CUR-012: a dismissed finding is triaged away; keeping it searchable puts
# noise the operator explicitly rejected back into every ``ctx_search``
# result. ``reindex_all`` and ``index_finding`` share this predicate, so
# the full rebuild and the incremental hook agree on what belongs in the
# index.
# ``resolved`` добавлен вместе с автозакрытием находок: вылеченный пробел —
# уже не работа, и в выдаче `ctx_search` на дефолтном профиле `agent` ему
# делать нечего. Правка безопасна для существующих данных: до этого статус
# `resolved` не проставлял никто.
_FINDING_UNINDEXED_STATUSES = frozenset({"dismissed", "resolved"})

_MISSING_INDEX_MESSAGE = "FTS index table db_search_idx is missing — run `alembic upgrade head`"


class SearchIndexMissing(RuntimeError):
    """``db_search_idx`` (FTS5 virtual table, migration 0023) doesn't exist.

    CUR-010: every DB touched before migration 0023 was applied raises a raw
    ``sqlalchemy.exc.OperationalError`` on the first search/reindex — this
    maps that specific failure to a typed exception presentation surfaces
    can turn into a clean error instead of a stack trace.
    """

    def __init__(self) -> None:
        super().__init__(_MISSING_INDEX_MESSAGE)


def _is_missing_index_error(exc: OperationalError) -> bool:
    """True if ``exc`` is sqlite's 'no such table: db_search_idx'.

    Any other ``OperationalError`` (locked DB, disk I/O, ...) is a different
    failure mode and must not be swallowed into ``SearchIndexMissing``.
    """
    msg = str(exc)
    return "no such table" in msg and "db_search_idx" in msg


# bm25() weight positions are 1:1 with the columns declared in
# ``db_search_idx`` (migration 20260515_0023_fts5_index.py): kind, ref,
# project_id UNINDEXED, title, body. SQLite still assigns UNINDEXED columns a
# slot in the bm25() argument list even though they hold no tokens — passing
# fewer than 5 weights silently shifts title/body onto the wrong columns
# (e.g. ``bm25(db_search_idx, 10.0, 1.0)`` would weight *kind* and *ref*, not
# title/body). CUR-011: title matches rank above body matches.
_BM25_WEIGHT_KIND = 1.0
_BM25_WEIGHT_REF = 1.0
_BM25_WEIGHT_PROJECT_ID = 1.0  # UNINDEXED — no tokens, weight is a no-op
_BM25_WEIGHT_TITLE = 10.0
_BM25_WEIGHT_BODY = 1.0

_SNIPPET_COL_BUDGET = 12


def _execute_guarded(session: Session, sql: str, params: dict[str, Any]) -> None:
    """Run one write against ``db_search_idx``, mapping the CUR-010 failure.

    Every index write funnels through here, so "no such table:
    db_search_idx" becomes :class:`SearchIndexMissing` in exactly one
    place instead of at each call site. Any other ``OperationalError``
    (locked DB, disk I/O, ...) propagates untouched.
    """
    try:
        session.execute(text(sql), params)
    except OperationalError as exc:
        if _is_missing_index_error(exc):
            raise SearchIndexMissing() from exc
        raise


def _wipe(session: Session, project_id: int) -> None:
    _execute_guarded(
        session,
        "DELETE FROM db_search_idx WHERE project_id = :p",
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
    _execute_guarded(
        session,
        "INSERT INTO db_search_idx (kind, ref, project_id, title, body) "
        "VALUES (:kind, :ref, :pid, :title, :body)",
        {
            "kind": kind,
            "ref": ref,
            "pid": project_id,
            "title": title or "",
            "body": body or "",
        },
    )


# --------------------------------------------------------------------------- #
# Row payloads — one builder per kind.                                          #
#                                                                               #
# CUR-012: ``reindex_all`` and the incremental write-path hooks both go         #
# through these, so a rebuilt row and an incrementally updated row are          #
# byte-identical. Changing what a kind indexes means changing exactly one       #
# function here.                                                                #
# --------------------------------------------------------------------------- #


def _task_payload(t: TaskModel) -> tuple[str, str]:
    """(title, body) for a task — description + acceptance + blocked_reason."""
    body_parts = [t.description or "", t.acceptance or "", t.blocked_reason or ""]
    return t.title or "", "\n".join(b for b in body_parts if b)


def _doc_payload(session: Session, d: DocumentModel) -> tuple[str, str]:
    """(title, body) for a document — preamble + every section body."""
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
    return d.title or "", body.strip()


def _story_payload(session: Session, s: UserStoryModel) -> tuple[str, str]:
    """(title, body) for a story — narrative + acceptance criteria."""
    crits = list(
        session.execute(
            select(StoryAcceptanceModel.criterion)
            .where(StoryAcceptanceModel.story_id == s.row_id)
            .order_by(StoryAcceptanceModel.position)
        ).scalars()
    )
    narrative = s.narrative or ""
    return narrative[:_STORY_TITLE_BUDGET], narrative + "\n\n" + "\n- ".join(crits)


def _adr_payload(a: ADRModel) -> tuple[str, str]:
    """(title, body) for an ADR — the full decision payload."""
    body = "\n\n".join(filter(None, [a.context, a.decision, a.alternatives, a.consequences]))
    return a.title or "", body


def _finding_payload(f: FindingModel) -> tuple[str, str]:
    """(title, body) for a finding — source + kind + path + body."""
    body_parts = [f.source or "", f.kind or "", f.path or "", f.body or ""]
    return f.title or "", "\n".join(b for b in body_parts if b)


def _finding_is_indexable(f: FindingModel) -> bool:
    return f.status not in _FINDING_UNINDEXED_STATUSES


def _index_doc(session: Session, project_id: int, d: DocumentModel) -> None:
    """Insert one FTS row for a document (used by the full rebuild)."""
    title, body = _doc_payload(session, d)
    _insert(session, kind="doc", ref=d.doc_key, project_id=project_id, title=title, body=body)


# --------------------------------------------------------------------------- #
# Incremental index maintenance                                                 #
# --------------------------------------------------------------------------- #


def delete_entity(session: Session, *, kind: str, ref: str, project_id: int) -> None:
    """Drop the FTS row for one entity. Idempotent — a missing row is fine."""
    _execute_guarded(
        session,
        "DELETE FROM db_search_idx WHERE project_id = :p AND kind = :k AND ref = :r",
        {"p": project_id, "k": kind, "r": ref},
    )


def upsert_entity(
    session: Session,
    *,
    kind: str,
    ref: str,
    project_id: int,
    title: str,
    body: str,
) -> None:
    """Replace the FTS row for one entity (delete-then-insert, idempotent).

    ``db_search_idx`` is an FTS5 virtual table without a unique constraint
    to conflict on, so "upsert" is spelled as delete + insert. Runs inside
    the caller's transaction: no commit of its own.

    Raises :class:`SearchIndexMissing` on a DB that predates migration 0023.
    """
    delete_entity(session, kind=kind, ref=ref, project_id=project_id)
    _insert(session, kind=kind, ref=ref, project_id=project_id, title=title, body=body)


def upsert_doc(session: Session, *, project_id: int, doc_key: str) -> None:
    """ADO-030: incremental FTS update for a single document.

    Called from the import/update path so freshly imported docs are
    searchable without a manual ``--reindex``. A missing document just
    clears the stale row.
    """
    d = session.execute(
        select(DocumentModel).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()
    if d is None:
        delete_entity(session, kind="doc", ref=doc_key, project_id=project_id)
        return
    title, body = _doc_payload(session, d)
    upsert_entity(session, kind="doc", ref=doc_key, project_id=project_id, title=title, body=body)


def delete_doc(session: Session, *, project_id: int, doc_key: str) -> None:
    """Remove one FTS row for a document (used by the doc delete path)."""
    delete_entity(session, kind="doc", ref=doc_key, project_id=project_id)


# --------------------------------------------------------------------------- #
# Write-path hooks (CUR-012)                                                    #
#                                                                               #
# These are what ``task_service`` / ``story_service`` / ``adr_service`` /       #
# ``finding_service`` call after a mutation, inside the same transaction.       #
# They are deliberately **best-effort**: the FTS index is a derived artefact,   #
# and a DB that predates migration 0023 must not lose the ability to create a   #
# task. Only ``SearchIndexMissing`` is swallowed (logged at WARNING, repairable #
# with ``cod-doc search --reindex`` once the migration runs) — every other      #
# failure propagates and rolls the mutation back with it.                       #
# --------------------------------------------------------------------------- #


def _warn_not_indexed(kind: str, ref: str) -> None:
    _LOG.warning(
        "db_search_idx is missing — %s %s was not indexed; "
        "run `alembic upgrade head` then `cod-doc search --reindex`",
        kind,
        ref,
    )


def _upsert_best_effort(
    session: Session,
    *,
    kind: str,
    ref: str,
    project_id: int,
    title: str,
    body: str,
) -> None:
    try:
        upsert_entity(session, kind=kind, ref=ref, project_id=project_id, title=title, body=body)
    except SearchIndexMissing:
        _warn_not_indexed(kind, ref)


def _delete_best_effort(session: Session, *, kind: str, ref: str, project_id: int) -> None:
    try:
        delete_entity(session, kind=kind, ref=ref, project_id=project_id)
    except SearchIndexMissing:
        _warn_not_indexed(kind, ref)


def index_doc(session: Session, *, project_id: int, doc_key: str) -> None:
    """ADO-211: refresh the FTS row for one document after a write.

    Called by ``doc_service`` mutations and ``import_service.import_markdown``.
    Until ADO-211 only ``import_or_update_markdown`` indexed, so a document
    made through ``doc_create`` stayed invisible to search while the index was
    non-empty (``ensure_index`` only rebuilds an empty one). Best-effort like
    the other hooks here: a DB without migration 0023 still accepts the write.
    """
    try:
        upsert_doc(session, project_id=project_id, doc_key=doc_key)
    except SearchIndexMissing:
        _warn_not_indexed("doc", doc_key)


def unindex_doc(session: Session, *, project_id: int, doc_key: str) -> None:
    """ADO-211: drop a document's FTS row — the old key on ``rename``."""
    _delete_best_effort(session, kind="doc", ref=doc_key, project_id=project_id)


def index_task(session: Session, task: TaskModel) -> None:
    """Refresh the FTS row for one task after a ``task_service`` mutation."""
    title, body = _task_payload(task)
    _upsert_best_effort(
        session,
        kind="task",
        ref=task.task_id,
        project_id=task.project_id,
        title=title,
        body=body,
    )


def index_story(session: Session, story: UserStoryModel) -> None:
    """Refresh the FTS row for one story (narrative or criteria changed)."""
    title, body = _story_payload(session, story)
    _upsert_best_effort(
        session,
        kind="story",
        ref=story.story_id,
        project_id=story.project_id,
        title=title,
        body=body,
    )


def index_adr(session: Session, adr: ADRModel) -> None:
    """Refresh the FTS row for one ADR after an ``adr_service`` mutation."""
    title, body = _adr_payload(adr)
    _upsert_best_effort(
        session,
        kind="adr",
        ref=adr.adr_id,
        project_id=adr.project_id,
        title=title,
        body=body,
    )


def index_finding(session: Session, finding: FindingModel) -> None:
    """Refresh the FTS row for one finding; a dismissed finding is removed."""
    if not _finding_is_indexable(finding):
        _delete_best_effort(
            session,
            kind="finding",
            ref=finding.finding_uid,
            project_id=finding.project_id,
        )
        return
    title, body = _finding_payload(finding)
    _upsert_best_effort(
        session,
        kind="finding",
        ref=finding.finding_uid,
        project_id=finding.project_id,
        title=title,
        body=body,
    )


def reindex_all(session: Session, project_id: int) -> dict[str, int]:
    """Drop project rows from index, then rebuild from canonical tables.

    Raises ``SearchIndexMissing`` if ``db_search_idx`` doesn't exist yet
    (DB predates migration 0023) — the ``_wipe`` DELETE is the first
    statement to touch the table, and every index write goes through
    ``_execute_guarded``, which raises it.

    Row payloads come from the same ``_*_payload`` builders the incremental
    write-path hooks use (CUR-012), so a rebuild produces byte-identical
    rows to the ones those hooks wrote.
    """
    _wipe(session, project_id)

    counts: dict[str, int] = {"task": 0, "doc": 0, "story": 0, "adr": 0, "finding": 0}

    # Tasks — description + acceptance + blocked_reason in body.
    for t in session.execute(select(TaskModel).where(TaskModel.project_id == project_id)).scalars():
        title, body = _task_payload(t)
        _insert(session, kind="task", ref=t.task_id, project_id=project_id, title=title, body=body)
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
        title, body = _story_payload(session, s)
        _insert(
            session, kind="story", ref=s.story_id, project_id=project_id, title=title, body=body
        )
        counts["story"] += 1

    # ADRs — full decision payload.
    for a in session.execute(select(ADRModel).where(ADRModel.project_id == project_id)).scalars():
        title, body = _adr_payload(a)
        _insert(session, kind="adr", ref=a.adr_id, project_id=project_id, title=title, body=body)
        counts["adr"] += 1

    # Findings — source + kind + path + body. Dismissed ones stay out
    # (see ``_FINDING_UNINDEXED_STATUSES``).
    for f in session.execute(
        select(FindingModel).where(FindingModel.project_id == project_id)
    ).scalars():
        if not _finding_is_indexable(f):
            continue
        title, body = _finding_payload(f)
        _insert(
            session,
            kind="finding",
            ref=f.finding_uid,
            project_id=project_id,
            title=title,
            body=body,
        )
        counts["finding"] += 1

    session.flush()
    counts["total"] = sum(counts[k] for k in ("task", "doc", "story", "adr", "finding"))
    return counts


def ensure_index(session: Session, project_id: int) -> dict[str, Any]:
    """RFC 25 §3.2 (CUR-007): lazy reindex for ``ctx_search``.

    A brand-new project (or one whose index was wiped) has zero rows in
    ``db_search_idx`` — searching it silently returns nothing forever unless
    someone remembers to run ``reindex_all``. This is the guard: called
    before every ``ctx_search`` query, it checks the current per-kind row
    counts and, only when the index is completely empty, runs a full
    ``reindex_all`` to populate it. A non-empty index is left untouched —
    this is a one-shot bootstrap, not a periodic refresh.

    Raises ``SearchIndexMissing`` if ``db_search_idx`` doesn't exist yet.
    """
    try:
        rows = session.execute(
            text(
                "SELECT kind, count(*) AS n FROM db_search_idx WHERE project_id = :pid GROUP BY kind"
            ),
            {"pid": project_id},
        ).all()
    except OperationalError as exc:
        if _is_missing_index_error(exc):
            raise SearchIndexMissing() from exc
        raise
    by_kind: dict[str, int] = {r.kind: int(r.n) for r in rows}
    total = sum(by_kind.values())
    if total == 0:
        counts = reindex_all(session, project_id)
        by_kind = {k: counts[k] for k in ("task", "doc", "story", "adr", "finding")}
        return {"total": counts["total"], "by_kind": by_kind, "reindexed": True}
    return {"total": total, "by_kind": by_kind, "reindexed": False}


def split_project_slugs(raw: str | None) -> list[str]:
    """``--projects a,b`` → ``['a', 'b']``; пустое/пробельное отбрасывается.

    Живёт в сервисе, а не в CLI: обе поверхности (``cod-doc search`` и
    ``cod-doc ctx search``) обязаны разбирать список одинаково.
    """
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def resolve_cross_project_ids(
    session: Session,
    *,
    project: str,
    projects: Sequence[str],
    config: Config | None = None,
) -> dict[str, int]:
    """CUR-013 / RFC 22 §3.6: слаги соседних проектов → ``project.row_id``.

    Кросс-проектный поиск — это ``WHERE project_id IN (...)`` по одному
    FTS5-индексу, а не слияние выдач нескольких БД: bm25 относителен корпусу,
    и склейка независимых шкал даёт фальшивый рейтинг (RFC 22 §2.2, факт 1).
    Поэтому precondition жёсткий: каждый слаг обязан резолвиться в тот же
    ``db_url``, что и базовый проект (hub-режим). Иначе — ``ValueError``,
    а не тихо пустая выдача по чужому индексу.

    Возвращает только **дополнительные** проекты (базовый ``project``
    исключён — его ``project_id`` вызывающий уже знает), сохраняя порядок
    аргумента; дубликаты схлопываются.
    """
    from cod_doc.config import Config as _Config
    from cod_doc.infra.db import db_url_for_entry
    from cod_doc.infra.repositories import ProjectRepository

    cfg = config if config is not None else _Config.load()
    base_entry = cfg.get_project(project)
    if base_entry is None:
        raise ValueError(f"Project not found: {project!r}")
    base_url = db_url_for_entry(base_entry)

    repo = ProjectRepository(session)
    resolved: dict[str, int] = {}
    for slug in projects:
        if not slug or slug == project or slug in resolved:
            continue
        entry = cfg.get_project(slug)
        if entry is None:
            raise ValueError(f"Project not found: {slug!r}")
        url = db_url_for_entry(entry)
        if url != base_url:
            raise ValueError(
                "cross-project search requires a shared db_url (hub mode): "
                f"{slug} resolves to {url}"
            )
        proj = repo.get_by_slug(slug)
        if proj is None or proj.row_id is None:
            raise ValueError(f"Project '{slug}' not in DB — run 'cod-doc project add' first.")
        resolved[slug] = proj.row_id
    return resolved


def _slug_by_id(session: Session, project_ids: Sequence[int]) -> dict[int, str]:
    """Один запрос ``row_id → slug`` на всю выдачу (CUR-013)."""
    rows = session.execute(
        select(ProjectModel.row_id, ProjectModel.slug).where(ProjectModel.row_id.in_(project_ids))
    ).all()
    return {int(r.row_id): str(r.slug) for r in rows}


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
    project_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Return ranked hits grouped by ``kind``.

    Ranking is ``bm25(db_search_idx)`` — SQLite's convention is that a
    *smaller* (more negative) score means a *better* match, so hits are
    ordered ascending by score within each kind. Title matches are weighted
    ``_BM25_WEIGHT_TITLE`` (10x) over body matches, so a title hit outranks
    a body-only hit on the same query.

    ``limit`` caps hits **per kind** (CUR-011), not globally: a doc-heavy
    corpus can no longer crowd every task/adr/... hit out of the result —
    each kind gets its own top-``limit`` window via
    ``ROW_NUMBER() OVER (PARTITION BY kind ORDER BY score)``. ``total`` is
    the sum of hits actually returned across kinds (i.e. after the per-kind
    cap), not the count of all underlying matches.

    ``project_ids`` (CUR-013 / RFC 22 §3.6) widens the query to several
    projects that live in the **same** database: ``WHERE project_id IN (...)``
    over the one FTS5 index, so bm25 keeps a single scale and the per-kind
    window is applied to the **merged** result — not per project. Проверку
    «все проекты в одной БД» делает :func:`resolve_cross_project_ids`.
    ``project_id`` остаётся обязательным и всегда входит в набор, поэтому
    старые вызовы ведут себя ровно как раньше.

    Shape::

        {
          "query": "...",
          "total": N,
          "by_kind": {
            "task": [{ref, title, snippet, score, project}, ...],
            "doc":  [...],
            "story": [...],
            "adr":  [...],
            "finding": [...]
          }
        }

    ``project`` в каждом хите — слаг проекта-владельца (``None``, если строка
    ``project`` исчезла из БД, а индекс ещё помнит её id).

    Raises ``SearchIndexMissing`` if ``db_search_idx`` doesn't exist yet
    (empty/whitespace ``query`` short-circuits before touching the table,
    so that case never triggers the guard).
    """
    if scope is not None and scope not in _VALID_SCOPES:
        raise ValueError(f"invalid scope {scope!r}; expected one of {sorted(_VALID_SCOPES)}")

    fts_query = _escape_fts(query)
    if not fts_query:
        return {"query": query, "total": 0, "by_kind": {k: [] for k in sorted(_VALID_SCOPES)}}

    # Набор проектов: базовый всегда внутри (обратная совместимость), лишние
    # дубликаты схлопнуты. Плейсхолдеры именованные — `IN (...)` через text().
    ids = sorted({project_id, *(project_ids or [])})
    id_placeholders = ", ".join(f":pid_{i}" for i in range(len(ids)))

    # Column index -1 lets FTS5 pick the column with the strongest match
    # — so a title-only hit still shows a useful snippet, and body matches
    # surface naturally too.
    scored_sql = f"""
        SELECT kind, ref, title, project_id,
               snippet(db_search_idx, -1, '<mark>', '</mark>', '…', :snip_budget) AS snippet,
               bm25(db_search_idx, :w_kind, :w_ref, :w_project, :w_title, :w_body) AS score
        FROM db_search_idx
        WHERE project_id IN ({id_placeholders}) AND db_search_idx MATCH :q
    """
    params: dict[str, Any] = {
        **{f"pid_{i}": pid for i, pid in enumerate(ids)},
        "q": fts_query,
        "lim": limit,
        "snip_budget": _SNIPPET_COL_BUDGET,
        "w_kind": _BM25_WEIGHT_KIND,
        "w_ref": _BM25_WEIGHT_REF,
        "w_project": _BM25_WEIGHT_PROJECT_ID,
        "w_title": _BM25_WEIGHT_TITLE,
        "w_body": _BM25_WEIGHT_BODY,
    }
    if scope is not None:
        scored_sql += " AND kind = :scope"
        params["scope"] = scope

    # Per-kind LIMIT: rank within each kind via ROW_NUMBER() before capping,
    # so e.g. limit=5 yields up to 5 task hits *and* up to 5 doc hits rather
    # than one shared LIMIT that a doc-heavy match set exhausts alone.
    sql = f"""
        WITH scored AS ({scored_sql}),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY kind ORDER BY score) AS rn
            FROM scored
        )
        SELECT kind, ref, title, project_id, snippet, score FROM ranked WHERE rn <= :lim
        ORDER BY kind, score
    """

    try:
        rows = session.execute(text(sql), params).all()
    except OperationalError as exc:
        if _is_missing_index_error(exc):
            raise SearchIndexMissing() from exc
        raise
    # Запрос к таблице `project`, не к FTS — своего SearchIndexMissing-гарда
    # он не требует.
    slugs = _slug_by_id(session, ids)
    by_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in sorted(_VALID_SCOPES)}
    for r in rows:
        by_kind.setdefault(r.kind, []).append(
            {
                "ref": r.ref,
                "title": r.title,
                "snippet": r.snippet,
                "score": round(float(r.score), 4),
                "project": slugs.get(int(r.project_id)),
            }
        )
    total = sum(len(hits) for hits in by_kind.values())
    return {"query": query, "total": total, "by_kind": by_kind}
