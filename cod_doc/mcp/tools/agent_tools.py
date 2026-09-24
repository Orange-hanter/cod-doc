"""Cycle-5 agent-centric MCP tools (AGT-001..AGT-007).

Each tool composes internal CRUD operations so the AI agent doesn't have
to chain 5-10 calls to collect context:

- ``agent_capabilities()`` — L0 entry-point (AGT-002).
- ``agent_pick(project, agent_id)`` — atomic task + context + navigation card (AGT-003).
- ``agent_get(task_id, what)`` — opt-in deep fetch (AGT-004).
- ``agent_report(task_id, kind, message)`` — progress/blocker/approval dispatcher (AGT-005).
- ``agent_complete(task_id, ...)`` — guarded done + release (AGT-006).
- ``agent_release(task_id, reason?)`` — give up without done (AGT-007).

Since RFC 25 §3.2 (CUR-008) only ``agent_capabilities`` and
``agent_report`` are part of the default ``agent`` (doc-curator)
allowlist; ``agent_pick`` / ``agent_get`` / ``agent_complete`` /
``agent_release`` stay registered here but are exposed only under
standard/full — see ``cod_doc/mcp/profiles.py::AGENT_TOOLS``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register agent_* tools on the given FastMCP instance."""

    # ----------------------------------------------------------------- #
    # AGT-002: agent_capabilities — slimmed L0 entry-point.              #
    # ----------------------------------------------------------------- #

    @mcp.tool(name="agent_capabilities")
    def agent_capabilities() -> dict[str, Any]:
        """Single-call L0 bootstrap for an AI agent (AGT-002).

        Returns the minimum self-describing snapshot the agent needs to
        start work — without the 100+ internal tool list, family counts,
        or profile-info noise that ``capabilities()`` (admin surface)
        carries. Stays under 4KB for tight context budgets.

        The tool lives on every profile, and the payload declares the
        agent's role derived from the active profile:

        - ``agent`` (and any unrecognised profile) → ``doc-curator``
          (RFC 25 §3.2, CUR-008): ``forbidden`` lists the task protocol and
          ``next_action_hint`` steers towards drift and search.
        - ``minimal`` / ``standard`` / ``full`` → ``coder`` (RFC 27 F5):
          nothing is forbidden and ``next_action_hint`` walks the
          checkout → complete protocol.

        Shape (agent)::

            {
              "server_version": "1.1.0",
              "profile": "agent",
              "role": "doc-curator",
              "forbidden": ["agent_pick", "task_checkout", "task_complete"],
              "skills": [
                {"name": "orchestrator", "description": "..."}, ...
              ],
              "task_status_canonical": [
                "backlog", "todo", "in_progress", "in_review",
                "blocked", "done", "cancelled"
              ],
              "task_status_legacy_aliases": {"pending": "todo", ...},
              "default_project": "<slug>" | null,
              "orchestrator_skill": "cod_doc/skills/orchestrator/SKILL.md",
              "next_action_hint": "Call ctx_drift(project=...) then
                                   ctx_search(project=..., query=...) —
                                   do not pick implementation tasks."
            }

        Shape (minimal/standard/full) — same keys, differing in::

            {
              "profile": "standard",
              "role": "coder",
              "forbidden": [],
              "next_action_hint": "Call task_next_ready(project=...) or
                                   plan_ready → task_checkout → work →
                                   task_complete(commit_sha); questions
                                   about docs — ctx_search/context_get."
            }
        """
        from cod_doc import __version__ as version
        from cod_doc.mcp.tools import _workspace
        from cod_doc.mcp.tools.skill_tools import iter_skill_records
        from cod_doc.services.task_status_machine import (
            _LEGACY_ALIASES,
            ALLOWED_TRANSITIONS,
        )

        canonical = sorted(
            set(ALLOWED_TRANSITIONS.keys())
            | {dst for dsts in ALLOWED_TRANSITIONS.values() for dst in dsts}
        )

        # Skills are a *menu* at L0: enough to recognise a name, no more.
        # Full bodies arrive inline in agent_pick's task card (or via
        # agent_get) — so this payload must not grow with the catalog.
        # The one-liner (trigger list dropped, first sentence) is shared
        # with the curator card: skill_service.one_liner.
        from cod_doc.services.skill_service import one_liner

        skills = [
            {
                "name": r.get("name"),
                "description": one_liner(r.get("description")),
            }
            for r in iter_skill_records()
        ]
        default = _workspace.get()
        profile = _active_profile()

        if profile in ("minimal", "standard", "full"):
            # RFC 27 F5: these surfaces serve the coding agent, whose
            # protocol (checkout → complete) CLAUDE.md and task-flow require.
            role = "coder"
            forbidden: list[str] = []
            slug = "<slug>" if default is None else f"'{default}'"
            hint = (
                f"Call task_next_ready(project={slug}) or plan_ready → "
                "task_checkout → work → task_complete(commit_sha); "
                "questions about docs — ctx_search/context_get."
            )
            if default is None:
                hint += " No workspace default is set, so pass `project` explicitly on every call."
        else:
            # RFC 25 §3.2: the agent profile (and any unknown one, as the
            # safe default) curates documentation and takes no tasks.
            role = "doc-curator"
            forbidden = ["agent_pick", "task_checkout", "task_complete"]
            hint = (
                "Call ctx_drift(project=<slug>) then "
                "ctx_search(project=<slug>, query=...) — do not pick "
                "implementation tasks. No workspace default is set, so pass "
                "`project` explicitly on every call."
                if default is None
                else (
                    f"Call ctx_drift(project='{default}') then "
                    f"ctx_search(project='{default}', query=...) — "
                    "do not pick implementation tasks."
                )
            )

        return {
            "server_version": version,
            "profile": profile,
            # Role and forbidden list follow the profile and ride in the
            # payload, so a client that never reads the orchestrator skill
            # still knows whether it may take implementation tasks.
            "role": role,
            "forbidden": forbidden,
            "skills": skills,
            "task_status_canonical": canonical,
            "task_status_legacy_aliases": dict(_LEGACY_ALIASES),
            "default_project": default,
            "orchestrator_skill": "cod_doc/skills/orchestrator/SKILL.md",
            "next_action_hint": hint,
        }

    # ----------------------------------------------------------------- #
    # AGT-003..AGT-007 — MCP wrappers over services.agent_service.      #
    # Bodies live in ``cod_doc.services.agent_service`` and have full   #
    # test coverage (see tests/services/test_agent_pick.py and          #
    # tests/services/test_agent_workflow.py). This module is the        #
    # thin wrapper layer: open a session, resolve the project, and      #
    # delegate to the service. See proposal 20 (Multi-Agent Standup)    #
    # for the agent-coordination patterns built on top.                 #
    # ----------------------------------------------------------------- #

    @mcp.tool(name="agent_pick")
    def agent_pick(
        project: str,
        agent_id: str,
        plan_scope: str | None = None,
    ) -> dict[str, Any]:
        """Atomically acquire the next ready task with full context (AGT-003).

        Composes ``ready_for_project`` (deps filter) + lock filter +
        ``task_checkout`` + ``context_get`` + skill-body inlining into ONE
        call. Replaces the typical 5-call cold-start sequence.

        Idempotent on (project, agent_id): if the caller already holds a
        checkout in this project, returns that task's card with
        ``idempotent_replay: true``.

        Returns the *task card* (see acceptance of AGT-003)::

            {
              task: {task_id, title, type, priority, status, description,
                     acceptance, blocked_by, affects_files, story_id, ...},
              context: {
                plan: {scope, section_letter, section_title, section_position},
                story: {story_id, narrative, acceptance_criteria, ...} | null,
                related_docs: [...],
                siblings: [{task_id, title, status, why}, ...],
                affected_files: [path, ...],
                recent_history: [{kind, summary, actor_id, at}, ...]
              },
              navigation: {
                applicable_skills: [{name, description, body}, ...],
                next_actions: [hint strings],
                success_criteria: [parsed acceptance items],
                legal_status_transitions: ["todo", "in_review", "blocked",
                                           "done", "cancelled"]
              }
            }

        When the ready set is empty: ``{task: null, reason: "no_ready_tasks"}``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools._db import require_project_id, session_factory
        from cod_doc.services import agent_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return agent_service.pick(
                session,
                project_id=project_id,
                agent_id=agent_id,
                plan_scope=plan_scope,
            )

    @mcp.tool(name="agent_get")
    def agent_get(
        project: str,
        task_id: str,
        what: str,
        ref: str | None = None,
    ) -> dict[str, Any]:
        """Opt-in deep fetch when ``agent_pick``'s card didn't include something (AGT-004).

        ``what`` values:
        - ``full_doc_body`` — ``ref`` = doc_key.
        - ``related_task`` — ``ref`` = task_id.
        - ``story_full`` — ``ref`` = story_id.
        - ``plan_export`` — ``ref`` = plan_scope.
        - ``file_content`` — admin-only (use standard-profile read_file).

        On miss returns a structured hint with ``legal_what``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools._db import require_project_id, session_factory
        from cod_doc.services import agent_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return agent_service.get(
                session,
                project_id=project_id,
                task_id=task_id,
                what=what,
                ref=ref,
            )

    @mcp.tool(name="agent_report")
    def agent_report(
        project: str,
        task_id: str,
        kind: str,
        message: str,
        agent_id: str = "agent",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Unified dispatcher for non-done outcomes (AGT-005).

        ``kind`` values:
        - ``progress`` — log a progress note.
        - ``blocker`` — set blocked_reason and transition.
        - ``approval_request`` — create approval row linked to task.
          ``payload`` may carry ``approval_type``.
        - ``needs_context`` — informational; emits activity event.

        Returns ``{ok, kind, ..., next_actions}``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools._db import require_project_id, session_factory
        from cod_doc.services import agent_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return agent_service.report(
                session,
                project_id=project_id,
                task_id=task_id,
                kind=kind,
                message=message,
                payload=payload,
                agent_id=agent_id,
            )

    @mcp.tool(name="agent_complete")
    def agent_complete(
        project: str,
        task_id: str,
        agent_id: str,
        commit_sha: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Guarded done + release lock in one transaction (AGT-006).

        Validates blocked_by are all done, marks task done, releases
        checkout. Returns ``{ok, task_id, status, commit_sha, next_actions}``
        or ``{ok: false, hint, code?}`` on validation failure.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools._db import require_project_id, session_factory
        from cod_doc.services import agent_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return agent_service.complete(
                session,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                commit_sha=commit_sha,
                summary=summary,
            )

    @mcp.tool(name="agent_release")
    def agent_release(
        project: str,
        task_id: str,
        agent_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Release checkout lock without transitioning to done (AGT-007)."""
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools._db import require_project_id, session_factory
        from cod_doc.services import agent_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return agent_service.release(
                session,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                reason=reason,
            )


def _active_profile() -> str:
    """Defer import to avoid circular: server depends on agent_tools."""
    if profile := os.environ.get("COD_DOC_ACTIVE_PROFILE"):
        return profile
    try:
        from cod_doc.mcp.server import get_active_profile

        return get_active_profile()
    except Exception:
        return "agent"
