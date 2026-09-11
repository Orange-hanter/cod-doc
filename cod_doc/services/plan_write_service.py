"""Write-path for plans and sections (MCP + CLI).

``plan_service`` stays read-only. Create/append used to live inside MCP
``plan_create`` / ``plan_section_create``; both surfaces now call this module.

No Plan revision is written (the MCP path never did). Activity events
``plan.created`` / ``plan.section_created`` close the PCA-912 gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from cod_doc.domain.entities import EntityKind, Plan, PlanSection
from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
from cod_doc.services import activity_service

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session


class PlanSectionView(TypedDict):
    section_id: int | None
    letter: str
    title: str
    slug: str
    position: int


class CreatedPlanView(TypedDict):
    plan_id: int | None
    scope: str
    principle: str | None
    sections: list[PlanSectionView]


def _section_view(sec: PlanSection) -> PlanSectionView:
    return {
        "section_id": sec.row_id,
        "letter": sec.letter,
        "title": sec.title,
        "slug": sec.slug,
        "position": sec.position,
    }


def _position(spec: Mapping[str, object], default: int) -> int:
    raw = spec.get("position", default)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return int(str(raw))


def create_plan(
    session: Session,
    *,
    project_id: int,
    scope: str,
    principle: str,
    sections: Sequence[Mapping[str, object]] | None,
    author: str,
) -> CreatedPlanView:
    """Insert a plan and optional seed sections.

    Raises ``ValueError`` if ``scope`` already exists or a section spec is
    missing ``letter``/``title``.
    """
    plan_repo = PlanRepository(session)
    if plan_repo.get_by_scope(scope) is not None:
        raise ValueError(f"Plan with scope '{scope}' already exists.")
    new_plan = plan_repo.add(Plan(project_id=project_id, scope=scope, principle=principle))
    assert new_plan.row_id is not None
    plan_id = new_plan.row_id

    sec_repo = PlanSectionRepository(session)
    seeded: list[PlanSectionView] = []
    for spec in sections or []:
        if not spec.get("letter") or not spec.get("title"):
            raise ValueError(f"section spec must include 'letter' and 'title' (got {spec!r})")
        sec = sec_repo.add(
            PlanSection(
                plan_id=plan_id,
                letter=str(spec["letter"]).upper(),
                title=str(spec["title"]),
                slug=str(spec.get("slug") or spec["title"]).strip(),
                position=_position(spec, len(seeded)),
            )
        )
        seeded.append(_section_view(sec))

    activity_service.emit_for_write(
        session,
        project_id,
        "plan.created",
        author,
        scope_kind=EntityKind.PLAN,
        scope_id=scope,
        payload={"principle": principle, "section_count": len(seeded)},
        summary=f"Plan {scope} created",
    )
    return {
        "plan_id": plan_id,
        "scope": scope,
        "principle": principle,
        "sections": seeded,
    }


def add_section(
    session: Session,
    *,
    plan_id: int,
    letter: str,
    title: str,
    slug: str | None,
    position: int | None,
    author: str,
) -> PlanSectionView:
    """Append a section to an existing plan.

    Raises ``ValueError`` if the plan is missing or the letter is taken.
    """
    plan_repo = PlanRepository(session)
    plan = plan_repo.get(plan_id)
    if plan is None:
        raise ValueError(f"Plan '{plan_id}' not found.")
    plan_scope = plan.scope

    sec_repo = PlanSectionRepository(session)
    current = sec_repo.list_for_plan(plan_id)
    if any(s.letter.upper() == letter.upper() for s in current):
        raise ValueError(f"Section letter '{letter}' already exists in plan '{plan_scope}'.")
    sec = sec_repo.add(
        PlanSection(
            plan_id=plan_id,
            letter=letter.upper(),
            title=title,
            slug=slug or title.strip(),
            position=position if position is not None else len(current),
        )
    )
    activity_service.emit_for_write(
        session,
        plan.project_id,
        "plan.section_created",
        author,
        scope_kind=EntityKind.PLAN,
        scope_id=plan_scope,
        payload={"letter": sec.letter, "title": sec.title, "position": sec.position},
        summary=f"Section {sec.letter} added to {plan_scope}",
    )
    return _section_view(sec)
