"""MCP tools: story.* — user story operations (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory, story_to_dict

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register story.* tools on the given FastMCP instance."""

    @mcp.tool(name="story_list")
    def story_list(project: str) -> list[dict[str, Any]]:
        """List all user stories for a project."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            stories = story_service.list_for_project(session, project_id)
        return [story_to_dict(s) for s in stories]

    @mcp.tool(name="story_get")
    def story_get(project: str, story_id: str) -> dict[str, Any] | None:
        """Get a story with its acceptance criteria and links. Returns null if not found."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            s = story_service.get(session, story_id)
            if s is None:
                return None
            acceptance = story_service.list_acceptance(session, story_id)
            links = story_service.list_links(session, story_id)

        result = story_to_dict(s)
        result["acceptance"] = [
            {"position": a.position, "criterion": a.criterion, "met": a.met} for a in acceptance
        ]
        result["links"] = [
            {"to_kind": lk.to_kind.value, "to_ref": lk.to_ref, "relation": lk.relation.value}
            for lk in links
        ]
        return result

    @mcp.tool(name="story_create")
    def story_create(
        project: str,
        story_id: str,
        persona: str,
        narrative: str,
        priority: str,
        status: str = "draft",
        acceptance: list[str] | None = None,
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a user story. story_id format: US-NNN (e.g. US-001).
        priority: critical|high|medium|low. status: draft|accepted|delivered|deferred.
        acceptance: optional list of acceptance criteria strings.
        """
        from cod_doc.domain.entities import Priority, UserStoryStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import StoryAlreadyExistsError
        from cod_doc.services.validation import ValidationError

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = require_project_id(session, project)
                s = story_service.create(
                    session,
                    project_id=project_id,
                    story_id=story_id,
                    persona=persona,
                    narrative=narrative,
                    priority=Priority(priority),
                    author=author,
                    status=UserStoryStatus(status),
                    acceptance=acceptance or None,
                    reason=reason,
                )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        except StoryAlreadyExistsError:
            raise ValueError(f"Story '{story_id}' already exists.") from None
        out = story_to_dict(s)
        if dry_run:
            out["dry_run"] = True
        return out

    @mcp.tool(name="story_update_status")
    def story_update_status(
        project: str,
        story_id: str,
        new_status: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Update a story's status. new_status: draft | accepted | delivered | deferred."""
        from cod_doc.domain.entities import UserStoryStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import StoryNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                s = story_service.update_status(
                    session,
                    story_id=story_id,
                    new_status=UserStoryStatus(new_status),
                    author=author,
                    reason=reason,
                )
        except StoryNotFoundError:
            raise ValueError(f"Story '{story_id}' not found.") from None
        return story_to_dict(s)

    @mcp.tool(name="story_add_criterion")
    def story_add_criterion(
        project: str,
        story_id: str,
        criterion: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Append an acceptance criterion to a story. Returns the new criterion with its position."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import StoryNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                ac = story_service.add_criterion(
                    session, story_id=story_id, criterion=criterion, author=author
                )
        except StoryNotFoundError:
            raise ValueError(f"Story '{story_id}' not found.") from None
        return {
            "story_id": story_id,
            "position": ac.position,
            "criterion": ac.criterion,
            "met": ac.met,
        }

    @mcp.tool(name="story_link")
    def story_link(
        project: str,
        story_id: str,
        to_kind: str,
        to_ref: str,
        relation: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Link a story to a task, document, or module.
        to_kind: task | document | module.
        relation: implemented_by | specified_in | owned_by.
        to_ref: task_id / doc_key / module_id depending on to_kind.
        """
        from cod_doc.domain.entities import StoryLinkKind, StoryRelation
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import BrokenLinkError, StoryNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                lk = story_service.link(
                    session,
                    story_id=story_id,
                    to_kind=StoryLinkKind(to_kind),
                    to_ref=to_ref,
                    relation=StoryRelation(relation),
                    author=author,
                )
        except StoryNotFoundError:
            raise ValueError(f"Story '{story_id}' not found.") from None
        except BrokenLinkError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "story_id": story_id,
            "to_kind": lk.to_kind.value,
            "to_ref": lk.to_ref,
            "relation": lk.relation.value,
        }

    @mcp.tool(name="story_coverage")
    def story_coverage(project: str, story_id: str) -> dict[str, Any]:
        """Return derived coverage status for a story (task progress + acceptance met).
        status: draft | accepted | in-progress | delivered | deferred.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import StoryNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                cov = story_service.coverage(session, story_id)
        except StoryNotFoundError:
            raise ValueError(f"Story '{story_id}' not found.") from None
        return {
            "story_id": cov.story_id,
            "status": cov.status.value,
            "tasks_total": cov.tasks_total,
            "tasks_done": cov.tasks_done,
            "tasks_in_progress": cov.tasks_in_progress,
            "acceptance_total": cov.acceptance_total,
            "acceptance_met": cov.acceptance_met,
        }
