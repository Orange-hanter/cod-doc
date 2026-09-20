"""Web pages for test scenarios — the authoring half of [RFC 24 §9].

Routes:
- ``GET /p/{slug}/scenarios``               — list grouped by capability.
- ``GET /p/{slug}/scenarios/{scenario_id}`` — one scenario in full.

Read-only on purpose. Scenarios are authored through `cod-doc scenario` or
the `scenario_*` MCP tools, and the markdown under `docs/system/scenarios/`
is a projection of these rows — a web form that wrote a fourth way in would
have to answer "which of you is the source?", and the answer is settled.

This page shows **intentions only**: whether a test proves a scenario is
producer-derived evidence, served separately by `/structure/scenarios`
(STR-*). The two cannot be joined yet — there is no `scenario_assessment`
table, and the producer keys its scenarios on `obligationRef`, not on
`scenario_id`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import ScenarioKind, ScenarioStatus
from cod_doc.services import projection_service, scenario_service

if TYPE_CHECKING:
    from cod_doc.domain.entities import Scenario
    from cod_doc.services.scenario_service import ScenarioCoverage

router = APIRouter()

# Options come from the enums, never from a hand-kept list — `pages/adr.py`
# does the same, and the enum is already in RFC 24 §9 order.
KIND_OPTIONS = [k.value for k in ScenarioKind]
STATUS_OPTIONS = [s.value for s in ScenarioStatus]

# `live` is the default view: everything not retired. `all` is the escape
# hatch. A checkbox could not express "only retired".
STATUS_FILTERS = ["live", *STATUS_OPTIONS, "all"]

_STATUS_ICON = {
    "draft": "✏️",
    "confirmed": "✅",
    "retired": "🗄️",
}
_KIND_ICON = {
    "happy_path": "🟢",
    "error_path": "🔴",
    "boundary_value": "📐",
    "invariant": "🔒",
    "integration": "🔗",
}

# DriftStatus → an existing badge modifier. No page-only badge names.
_DRIFT_BADGE = {
    "in_sync": "badge-success",
    "stale_export": "badge-warning",
    "edited_in_place": "badge-warning",
    "missing": "badge-error",
}
_DRIFT_HEALTH = {
    "in_sync": "success",
    "stale_export": "warning",
    "edited_in_place": "warning",
    "missing": "error",
}
_DRIFT_UNAVAILABLE: dict[str, Any] = {
    "level": "muted",
    "label": "недоступно",
    "badge": "",
    "total": 0,
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


def _keep(scenario: Scenario, *, kind: str | None, status: str) -> bool:
    if kind and scenario.kind.value != kind:
        return False
    if status == "all":
        return True
    if status == "live":
        return scenario.status is not ScenarioStatus.RETIRED
    return scenario.status.value == status


def _kind_chips(coverage: ScenarioCoverage) -> list[dict[str, Any]]:
    """Kind histogram in RFC order, so the same kind sits in the same place."""
    return [
        {"kind": k, "icon": _KIND_ICON.get(k, "•"), "count": coverage.by_kind.get(k, 0)}
        for k in KIND_OPTIONS
        if coverage.by_kind.get(k)
    ]


def _link_href(slug: str, to_kind: str, to_ref: str) -> str:
    """Resolve an edge to a route that exists.

    Refs are shape-validated only (`scenario_service/links.py`), so a target
    may dangle; the label still renders, the link just 404s like any other
    stale link.
    """
    if to_kind == "task":
        return f"/p/{slug}/tasks/{to_ref}"
    if to_kind == "story":
        return f"/p/{slug}/stories/{to_ref}"
    if to_kind == "document":
        return f"/p/{slug}/docs/{to_ref}"
    if to_kind == "criterion":
        # `<STORY-ID>#<position>` — the anchor is emitted by story_show.html.
        story, _, position = to_ref.partition("#")
        if position:
            return f"/p/{slug}/stories/{story}#crit-{position}"
        return f"/p/{slug}/stories/{story}"
    return ""


def _drift_by_group(
    session: Session,
    project_id: int,
    root: Path,
    group_keys: list[str],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Projection drift for the scenario documents, in one service call.

    Returns ``(per-group badge modifier, summary card)``. Only groups with a
    problem appear in the first dict — `ProjectDriftReport.issues` carries
    nothing else — so a healthy group renders no chip and the summary card
    carries the number.
    """
    if not group_keys:
        return {}, dict(_DRIFT_UNAVAILABLE)

    paths = [f"{scenario_service.doc_key_for(g)}.md" for g in group_keys]
    try:
        report = projection_service.detect_project_drift(
            session, project_id, root_path=root, paths=paths
        )
    except (OSError, ValueError):
        # A project without a docs/ tree must not 500 the list.
        return {}, dict(_DRIFT_UNAVAILABLE)

    if not report.total_docs:
        return {}, dict(_DRIFT_UNAVAILABLE)

    per_group: dict[str, str] = {}
    for issue in report.issues:
        group = issue.doc_key.rsplit("/", 1)[-1]
        per_group[group] = _DRIFT_BADGE.get(issue.report.status.value, "badge-warning")

    worst = "in_sync"
    if per_group:
        worst = "missing" if "badge-error" in per_group.values() else "edited_in_place"
    card = {
        "level": _DRIFT_HEALTH[worst],
        "label": "в синхроне" if worst == "in_sync" else f"расхождений: {len(per_group)}",
        "badge": _DRIFT_BADGE[worst],
        "total": report.total_docs,
    }
    return per_group, card


@router.get("/p/{slug}/scenarios", response_class=HTMLResponse)
def scenarios_list(
    request: Request,
    slug: str,
    kind: str | None = None,
    group: str | None = None,
    status: str = "live",
) -> HTMLResponse:
    """List scenarios grouped by the capability they are anchored to."""
    proj = get_project(slug)
    if status not in STATUS_FILTERS:
        status = "live"

    blocks: list[dict[str, Any]] = []
    totals = {"scenarios": 0, "groups": 0, "confirmed": 0, "draft": 0}
    drift_card: dict[str, Any] = dict(_DRIFT_UNAVAILABLE)
    group_options: list[str] = []
    db_available = False

    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            scenarios = scenario_service.list_for_project(session, project_db_id)
            # Coverage drives the group list, not the filtered rows: a group
            # whose scenarios are all retired must still appear.
            coverage = scenario_service.project_coverage(session, project_db_id)
            group_options = [c.group_key for c in coverage]

            by_group: dict[str, list[dict[str, Any]]] = {}
            for scenario in scenarios:
                if _keep(scenario, kind=kind, status=status):
                    by_group.setdefault(scenario.group_key, []).append(_row(scenario))

            drift_per_group, drift_card = _drift_by_group(
                session, project_db_id, Path(proj.entry.path), group_options
            )

            filtering = bool(kind) or bool(group) or status != "live"
            for cov in coverage:
                if group and cov.group_key != group:
                    continue
                rows = by_group.get(cov.group_key, [])
                if not rows and filtering:
                    continue
                blocks.append(
                    {
                        "group_key": cov.group_key,
                        "rows": rows,
                        "kind_chips": _kind_chips(cov),
                        "missing_kinds": list(cov.missing_kinds),
                        "drift_badge": drift_per_group.get(cov.group_key, ""),
                        "doc_key": scenario_service.doc_key_for(cov.group_key),
                        "capability_doc_key": f"docs/system/capabilities/{cov.group_key}",
                    }
                )

            totals = {
                "scenarios": sum(c.total for c in coverage),
                "groups": len(coverage),
                "confirmed": sum(c.confirmed for c in coverage),
                "draft": sum(c.draft for c in coverage),
            }

    return templates.TemplateResponse(
        request,
        "project/scenarios_list.html",
        {
            "project": proj.entry,
            "db_available": db_available,
            "blocks": blocks,
            "totals": totals,
            "drift": drift_card,
            "kind_filter": kind,
            "group_filter": group,
            "status_filter": status,
            "kind_options": KIND_OPTIONS,
            "group_options": group_options,
            "status_options": STATUS_FILTERS,
            "shown": sum(len(b["rows"]) for b in blocks),
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
            "href": _link_href(slug, link.to_kind.value, link.to_ref),
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
            "author": found.author,
            "created": found.created,
            "last_updated": found.last_updated,
            "row_id": found.row_id,
            "steps": steps,
            "links": links,
            "projection_doc_key": scenario_service.doc_key_for(found.group_key),
        },
    )
