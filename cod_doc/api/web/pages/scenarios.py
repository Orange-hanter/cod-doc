"""Web pages for test scenarios — the authoring half of [RFC 24 §9].

Routes:
- ``GET /p/{slug}/scenarios``               — list grouped by capability.
- ``GET /p/{slug}/scenarios/{scenario_id}`` — one scenario in full.

Read-only on purpose. Scenarios are authored through `cod-doc scenario` or
the `scenario_*` MCP tools, and the markdown under `docs/system/scenarios/`
is a projection of these rows — a web form that wrote a fourth way in would
have to answer "which of you is the source?", and the answer is already
settled.

This page shows **intentions only**: whether a test proves a scenario is
producer-derived evidence served separately by `/structure/scenarios`
(STR-*). The two are deliberately not joined here — see the note the
template renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import scenario_service

if TYPE_CHECKING:
    from cod_doc.domain.entities import Scenario

router = APIRouter()

_STATUS_ICON = {
    "draft": "✏️",
    "confirmed": "✅",
    "retired": "🗄️",
}

# RFC 24 §9 order, so the eye finds the same kind in the same place in
# every group.
_KIND_ORDER = [
    "happy_path",
    "error_path",
    "boundary_value",
    "invariant",
    "integration",
]
_KIND_ICON = {
    "happy_path": "🟢",
    "error_path": "🔴",
    "boundary_value": "📐",
    "invariant": "🔒",
    "integration": "🔗",
}


def _row(scenario: Scenario) -> dict[str, Any]:
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "kind": scenario.kind.value,
        "kind_icon": _KIND_ICON.get(scenario.kind.value, "•"),
        "status": scenario.status.value,
        "status_icon": _STATUS_ICON.get(scenario.status.value, "•"),
        "group_key": scenario.group_key,
        "doc_key": scenario.doc_key,
    }


@router.get("/p/{slug}/scenarios", response_class=HTMLResponse)
def scenarios_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    kind: str | None = None,
    include_retired: bool = False,
) -> HTMLResponse:
    """List scenarios grouped by the capability they are anchored to."""
    proj = get_project(slug)
    session, project_id = db

    scenarios = scenario_service.list_for_project(session, project_id)
    if kind:
        scenarios = [s for s in scenarios if s.kind.value == kind]
    if not include_retired:
        scenarios = [s for s in scenarios if s.status.value != "retired"]

    groups: dict[str, list[dict[str, Any]]] = {}
    for scenario in scenarios:
        groups.setdefault(scenario.group_key, []).append(_row(scenario))

    coverage = {c.group_key: c for c in scenario_service.project_coverage(session, project_id)}

    blocks: list[dict[str, Any]] = []
    for group_key in sorted(groups):
        cov = coverage.get(group_key)
        blocks.append(
            {
                "group_key": group_key,
                "rows": groups[group_key],
                "missing_kinds": list(cov.missing_kinds) if cov else [],
                "total": cov.total if cov else len(groups[group_key]),
                "doc_key": scenario_service.doc_key_for(group_key),
                "capability_doc_key": f"docs/system/capabilities/{group_key}",
            }
        )

    return templates.TemplateResponse(
        request,
        "project/scenarios_list.html",
        {
            "project": proj.entry,
            "blocks": blocks,
            "kind_filter": kind,
            "kind_options": _KIND_ORDER,
            "include_retired": include_retired,
            "total": len(scenarios),
        },
    )


@router.get("/p/{slug}/scenarios/{scenario_id}", response_class=HTMLResponse)
def scenario_show(
    request: Request,
    slug: str,
    scenario_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """One scenario: preconditions, ordered steps, expected result, links."""
    proj = get_project(slug)
    session, project_id = db

    found = scenario_service.get(session, project_id, scenario_id)
    if found is None or found.row_id is None:
        raise HTTPException(404, f"Сценарий не найден: {scenario_id}")

    steps = [s.text for s in scenario_service.list_steps(session, found.row_id)]
    links = [
        {
            "relation": link.relation.value,
            "to_kind": link.to_kind.value,
            "to_ref": link.to_ref,
        }
        for link in scenario_service.list_links(session, found.row_id)
    ]

    return templates.TemplateResponse(
        request,
        "project/scenario_show.html",
        {
            "project": proj.entry,
            "scenario": _row(found),
            "preconditions": found.preconditions,
            "expected": found.expected,
            "notes": found.notes,
            "section_anchor": found.section_anchor,
            "provenance": found.provenance.value,
            "author": found.author,
            "steps": steps,
            "links": links,
            "projection_doc_key": scenario_service.doc_key_for(found.group_key),
            "capability_doc_key": (
                f"docs/system/capabilities/{found.group_key}" if found.doc_key else None
            ),
        },
    )
