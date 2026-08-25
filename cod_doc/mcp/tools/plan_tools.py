"""MCP tools: plan.* — plan queries (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory, task_to_dict

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _require_plan_id(session: Any, plan_scope: str) -> int:
    from cod_doc.infra.repositories import PlanRepository

    plan = PlanRepository(session).get_by_scope(plan_scope)
    if plan is None or plan.row_id is None:
        raise ValueError(f"Plan '{plan_scope}' not found.")
    return plan.row_id


def register(mcp: FastMCP) -> None:
    """Register plan.* tools on the given FastMCP instance."""

    @mcp.tool(name="plan_create")
    def plan_create(
        project: str,
        scope: str,
        principle: str = "from-rfc",
        sections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a new Plan in the project, optionally with initial sections.

        Closes cycle-2 gap G1 (PCA-901): no MCP-API for bootstrapping a plan.
        Without this, callers had to use ``PlanRepository.add()`` directly via
        Python — broke MCP-only sessions trying to seed a new direction.

        Parameters
        ----------
        project:    project slug (must exist in DB).
        scope:      plan scope identifier (unique per DB, e.g.
                    ``paperclip-adoption-task-plan``).
        principle:  free-text origin tag (e.g. ``from-rfc`` / ``from-capability``).
        sections:   optional list of ``{"letter", "title", "slug", "position"}`` to
                    seed at create time. Each section gets validated by service.

        Returns ``{"plan_id", "scope", "principle", "sections": [...]}``.
        Raises ``ValueError`` if scope already exists.
        """
        from cod_doc.domain.entities import Plan, PlanSection
        from cod_doc.infra.db import transactional
        from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            plan_repo = PlanRepository(session)
            existing = plan_repo.get_by_scope(scope)
            if existing is not None:
                raise ValueError(f"Plan with scope '{scope}' already exists.")
            new_plan = plan_repo.add(Plan(project_id=project_id, scope=scope, principle=principle))
            assert new_plan.row_id is not None
            plan_id = new_plan.row_id

            sec_repo = PlanSectionRepository(session)
            seeded: list[dict[str, Any]] = []
            for spec in sections or []:
                if not spec.get("letter") or not spec.get("title"):
                    raise ValueError(
                        f"section spec must include 'letter' and 'title' (got {spec!r})"
                    )
                sec = sec_repo.add(
                    PlanSection(
                        plan_id=plan_id,
                        letter=str(spec["letter"]).upper(),
                        title=str(spec["title"]),
                        slug=str(spec.get("slug") or spec["title"]).strip(),
                        position=int(spec.get("position", len(seeded))),
                    )
                )
                seeded.append(
                    {
                        "section_id": sec.row_id,
                        "letter": sec.letter,
                        "title": sec.title,
                        "slug": sec.slug,
                        "position": sec.position,
                    }
                )

        return {
            "plan_id": plan_id,
            "scope": scope,
            "principle": principle,
            "sections": seeded,
        }

    @mcp.tool(name="plan_freeze")
    def plan_freeze(
        project: str,
        plan_scope: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """COD-052: snapshot a plan's current projection into a frozen Document.

        Renders Progress Overview / Next Batch / Dependency Graph and stores
        them as an immutable ``EXECUTION_LOG`` document keyed
        ``frozen/<scope>/<UTC timestamp>``. Append-only: each call creates a new
        snapshot. Returns ``{frozen_doc_key, document_id, title}``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            doc = plan_service.freeze_projection(session, plan_id, author=author, reason=reason)
        return {
            "frozen_doc_key": doc.doc_key,
            "document_id": doc.row_id,
            "title": doc.title,
        }

    @mcp.tool(name="plan_sections_list")
    def plan_sections_list(project: str, plan_scope: str) -> list[dict[str, Any]]:
        """List all sections of a plan with task counts (PCA-941).

        Closes a discoverability gap: ``task_create`` requires a ``section_letter``
        argument, but before this tool an agent had to call ``plan_export`` and
        parse markdown to discover valid letters. Now one cheap call returns
        ``[{section_id, letter, title, slug, position, task_count, done_count}]``
        sorted by ``position``.

        Use this before ``task_create`` to confirm the section_letter exists
        in the target plan.
        """
        from sqlalchemy import case, func, select

        from cod_doc.infra.db import transactional
        from cod_doc.infra.models import TaskModel
        from cod_doc.infra.repositories import PlanSectionRepository

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            sections = PlanSectionRepository(session).list_for_plan(plan_id)

            rows = session.execute(
                select(
                    TaskModel.section_id,
                    func.count(TaskModel.row_id).label("total"),
                    func.sum(case((TaskModel.status == "done", 1), else_=0)).label("done"),
                )
                .where(TaskModel.plan_id == plan_id)
                .group_by(TaskModel.section_id)
            ).all()
            counts: dict[int, tuple[int, int]] = {
                r.section_id: (int(r.total or 0), int(r.done or 0)) for r in rows
            }

            return [
                {
                    "section_id": s.row_id,
                    "letter": s.letter,
                    "title": s.title,
                    "slug": s.slug,
                    "position": s.position,
                    "task_count": counts.get(s.row_id or -1, (0, 0))[0],
                    "done_count": counts.get(s.row_id or -1, (0, 0))[1],
                }
                for s in sorted(sections, key=lambda x: x.position)
            ]

    @mcp.tool(name="plan_section_create")
    def plan_section_create(
        project: str,
        plan_scope: str,
        letter: str,
        title: str,
        slug: str | None = None,
        position: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Append a section to an existing plan.

        Closes cycle-2 gap G1 (PCA-901), section-create half. Position
        defaults to ``count(existing) + 0`` so callers can omit it for tail
        appends.
        """
        from cod_doc.domain.entities import PlanSection
        from cod_doc.infra.db import transactional
        from cod_doc.infra.repositories import PlanSectionRepository

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            sec_repo = PlanSectionRepository(session)
            current = sec_repo.list_for_plan(plan_id)
            if any(s.letter.upper() == letter.upper() for s in current):
                raise ValueError(
                    f"Section letter '{letter}' already exists in plan '{plan_scope}'."
                )
            sec = sec_repo.add(
                PlanSection(
                    plan_id=plan_id,
                    letter=letter.upper(),
                    title=title,
                    slug=slug or title.strip(),
                    position=position if position is not None else len(current),
                )
            )
        out = {
            "section_id": sec.row_id,
            "plan_scope": plan_scope,
            "letter": sec.letter,
            "title": sec.title,
            "slug": sec.slug,
            "position": sec.position,
        }
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="plan_progress")
    def plan_progress(project: str, plan_scope: str) -> dict[str, Any]:
        """Return derived progress for a plan: total/done/remaining per section and overall."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            progress = plan_service.recalc(session, plan_id)
        return {
            "scope": progress.scope,
            "total": progress.total,
            "done": progress.done,
            "in_progress": progress.in_progress,
            "remaining": progress.remaining,
            "status": progress.status.value,
            "sections": [
                {
                    "letter": s.letter,
                    "title": s.title,
                    "total": s.total,
                    "done": s.done,
                    "remaining": s.remaining,
                    "status": s.status.value,
                }
                for s in progress.sections
            ],
        }

    @mcp.tool(name="plan_ready")
    def plan_ready(
        project: str,
        plan_scope: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List tasks ready to start: pending with all blocking deps done, priority-ordered."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            tasks = plan_service.ready(session, plan_id, limit=limit)
        return [task_to_dict(t) for t in tasks]

    @mcp.tool(name="plan_audit")
    def plan_audit(project: str, plan_scope: str) -> dict[str, Any]:
        """Run integrity checks: cycle detection + done-drift.
        Returns cycles (task_id lists) and done tasks with unfinished blocking deps.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            report = plan_service.audit(session, plan_id)
        return {
            "issues_total": report.issues_total,
            "cycles": report.cycles,
            "done_with_unfinished_blocks": report.done_with_unfinished_blocks,
            "critical_path_length": report.critical_path_length,
        }

    @mcp.tool(name="plan_export")
    def plan_export(project: str, plan_scope: str) -> dict[str, str]:
        """Export markdown projections for a plan.
        Returns dict with keys: progress_overview, next_batch, dependency_graph.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            projections = plan_service.export(session, plan_id)
        return projections

    @mcp.tool(name="plan_critical_path")
    def plan_critical_path(project: str, plan_scope: str) -> dict[str, Any]:
        """Return the longest sequential dependency chain in the plan."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            require_project_id(session, project)
            plan_id = _require_plan_id(session, plan_scope)
            result = plan_service.critical_path(session, plan_id)
        return {
            "length": result.length,
            "task_ids": result.task_ids,
            "chain": [
                {
                    "task_id": e.task_id,
                    "title": e.title,
                    "status": e.status.value,
                    "depth": e.depth,
                }
                for e in result.chain
            ],
        }

    @mcp.tool(name="plan_forward_chain")
    def plan_forward_chain(project: str, task_id: str) -> list[dict[str, Any]]:
        """Return prerequisites of task_id: tasks that must complete BEFORE it.
        Each entry has task_id, title, status, depth (1 = direct prerequisite).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service
        from cod_doc.services.plan_service import TaskNotFoundInPlanError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                require_project_id(session, project)
                chain = plan_service.forward_chain(session, task_id)
        except TaskNotFoundInPlanError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return [
            {"task_id": e.task_id, "title": e.title, "status": e.status.value, "depth": e.depth}
            for e in chain
        ]

    @mcp.tool(name="plan_reverse_chain")
    def plan_reverse_chain(project: str, task_id: str) -> list[dict[str, Any]]:
        """Return dependents of task_id: tasks that become unblocked when it completes.
        Each entry has task_id, title, status, depth (1 = directly unblocked).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import plan_service
        from cod_doc.services.plan_service import TaskNotFoundInPlanError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                require_project_id(session, project)
                chain = plan_service.reverse_chain(session, task_id)
        except TaskNotFoundInPlanError:
            raise ValueError(f"Task '{task_id}' not found.") from None
        return [
            {"task_id": e.task_id, "title": e.title, "status": e.status.value, "depth": e.depth}
            for e in chain
        ]
