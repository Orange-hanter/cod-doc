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
    auth,
    code_refs,
    comments,
    commits,
    costs,
    docs,
    index,
    metrics,
    navigator,
    palette,
    plans,
    project,
    proposals,
    questions,
    revisions,
    roadmap,
    routines,
    run,
    scenarios,
    search,
    settings,
    standards,
    stories,
    tasks,
    ui_kit,
)

router = APIRouter()
router.include_router(index.router)
# 0056: OIDC-вход веба (/auth/oidc/login|callback, /auth/logout) — ранний,
# хоть пути и не пересекаются с docs catch-all, для читаемости.
router.include_router(auth.router)
router.include_router(project.router)
router.include_router(plans.router)
# RFC 36 / ROAD-007: дорожная карта — очередь проекта и прогноз, перестановка планов.
router.include_router(roadmap.router)
router.include_router(revisions.router)
router.include_router(tasks.router)
router.include_router(stories.router)
router.include_router(settings.router)
router.include_router(routines.router)
router.include_router(costs.router)
router.include_router(run.router)
router.include_router(navigator.router)
router.include_router(standards.router)
# Кит Ledger — каталог макросов `_ui/ledger.html` (редизайн 2026-11).
router.include_router(ui_kit.router)
# RFC 24 §9 scenarios — read-only list + detail (authoring half).
router.include_router(scenarios.router)
# OQM-004 open questions — list, card and write forms (DB-only entity).
router.include_router(questions.router)
# ACU-011 (RFC 28 §3.6): предложения фонового куратора — doc_patch с diff.
router.include_router(proposals.router)
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
# WUI-006 search palette (Cmd/Ctrl+K) — fragment for every page
router.include_router(palette.router)
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
