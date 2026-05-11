"""Web pages — server-rendered HTML routes.

Aggregates one APIRouter per page module (index, project, docs, tasks,
plans, revisions, settings). External callers keep importing the single
`router` symbol from this package, exactly as they did when pages.py was
a single file.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import costs, docs, index, navigator, plans, project, revisions, routines, run, settings, stories, tasks

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
# `docs.router` last — its `/p/{slug}/docs/{doc_key:path}` route is a
# greedy catch-all that would shadow more-specific siblings if registered
# first.
router.include_router(docs.router)

__all__ = ["router"]
