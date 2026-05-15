"""Web pages — server-rendered HTML routes.

Aggregates one APIRouter per page module (index, project, docs, tasks,
plans, revisions, settings). External callers keep importing the single
`router` symbol from this package, exactly as they did when pages.py was
a single file.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    adr,
    code_refs,
    comments,
    commits,
    costs,
    docs,
    index,
    metrics,
    navigator,
    plans,
    project,
    revisions,
    routines,
    run,
    search,
    settings,
    standards,
    stories,
    tasks,
)

router = APIRouter()
router.include_router(index.router)
router.include_router(project.router)
router.include_router(plans.router)
router.include_router(revisions.router)
router.include_router(tasks.router)
router.include_router(stories.router)
router.include_router(settings.router)
router.include_router(routines.router)
router.include_router(costs.router)
router.include_router(run.router)
router.include_router(navigator.router)
router.include_router(standards.router)
# ADRs — list/show/new/graph + supersede form (ADR-004/005/006).
# Registered BEFORE docs.router because docs has a `/p/{slug}/docs/...`
# greedy match that wouldn't shadow `/adr` anyway, but keep next to its
# read-only siblings for clarity.
router.include_router(adr.router)
# OBI-002 metrics dashboard
router.include_router(metrics.router)
# OBI-011 commit↔task links
router.include_router(commits.router)
# OBI-021 code-refs panel + preview endpoint
router.include_router(code_refs.router)
# OBI-040 unified FTS5 search
router.include_router(search.router)
# `comments.router` before `docs.router`: comments routes are nested under
# `/p/{slug}/docs/{doc_key:path}/comments` but each pins the trailing
# `/comments…` suffix, so they would not shadow the docs catch-all on
# their own. Register them earlier anyway for clarity — the docs
# catch-all `/p/{slug}/docs/{doc_key:path}` always matches last.
router.include_router(comments.router)
# `docs.router` last — its `/p/{slug}/docs/{doc_key:path}` route is a
# greedy catch-all that would shadow more-specific siblings if registered
# first.
router.include_router(docs.router)

__all__ = ["router"]
