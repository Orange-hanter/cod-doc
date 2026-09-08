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
            keys = {sec.row_id: sec.key for sec in story_service.list_sections(session, project_id)}
        return [story_to_dict(s, keys.get(s.section_id)) for s in stories]

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
            section_key = None
            if s.section_id is not None:
                keys = {
                    sec.row_id: sec.key
                    for sec in story_service.list_sections(session, s.project_id)
                }
                section_key = keys.get(s.section_id)

        result = story_to_dict(s, section_key)
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

    @mcp.tool(name="story_set_criterion_met")
    def story_set_criterion_met(
        project: str,
        story_id: str,
        position: int,
        met: bool = True,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Mark an acceptance criterion met (or unmet) by its position.

        Coverage only reports `delivered` once every criterion is met and every
        linked task is done, so this is what closes a story out.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import AcceptanceNotFoundError, StoryNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                ac = story_service.set_criterion_met(
                    session, story_id=story_id, position=position, met=met, author=author
                )
        except StoryNotFoundError:
            raise ValueError(f"Story '{story_id}' not found.") from None
        except AcceptanceNotFoundError:
            raise ValueError(
                f"Story '{story_id}' has no acceptance criterion at position {position}."
            ) from None
        return {
            "story_id": story_id,
            "position": ac.position,
            "criterion": ac.criterion,
            "met": ac.met,
        }

    @mcp.tool(name="story_section_list")
    def story_section_list(project: str) -> list[dict[str, Any]]:
        """List story sections (product modules) with how many stories each holds."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            sections = story_service.list_sections(session, project_id)
            all_stories = story_service.list_for_project(session, project_id)

        counts: dict[int | None, int] = {}
        for st in all_stories:
            counts[st.section_id] = counts.get(st.section_id, 0) + 1
        return [
            {
                "key": sec.key,
                "title": sec.title,
                "position": sec.position,
                "stories": counts.get(sec.row_id, 0),
            }
            for sec in sections
        ]

    @mcp.tool(name="story_section_create")
    def story_section_create(
        project: str,
        key: str,
        title: str,
        position: int | None = None,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Create a story section (product module).

        key: slug of lowercase letters, digits and dashes (e.g. 'module-1').
        position: display order; defaults to the end of the list.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import SectionAlreadyExistsError
        from cod_doc.services.validation import ValidationError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                sec = story_service.create_section(
                    session,
                    project_id=project_id,
                    key=key,
                    title=title,
                    position=position,
                    author=author,
                )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        except SectionAlreadyExistsError:
            raise ValueError(f"Section '{key}' already exists in {project}.") from None
        return {"key": sec.key, "title": sec.title, "position": sec.position}

    @mcp.tool(name="story_set_section")
    def story_set_section(
        project: str,
        story_id: str,
        key: str | None = None,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Assign a story to a section. Pass key=null to detach it."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import story_service
        from cod_doc.services.story_service import SectionNotFoundError, StoryNotFoundError

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                sec = story_service.assign_section(
                    session, story_id=story_id, key=key, author=author
                )
                result = (
                    {"key": sec.key, "title": sec.title, "position": sec.position}
                    if sec is not None
                    else None
                )
        except StoryNotFoundError:
            raise ValueError(f"Story '{story_id}' not found.") from None
        except SectionNotFoundError:
            raise ValueError(f"Section '{key}' not found in {project}.") from None
        return {"story_id": story_id, "section": result}
