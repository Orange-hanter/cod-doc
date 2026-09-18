"""MCP tools: scenario.* — test-scenario authoring ([RFC 24 §9], TSC-007).

This family **writes intentions**: what should be true, in the RFC's own
scenario vocabulary. It is not the evidence surface. RFC 24 §14 reserves
``structure_get`` / ``structure_context`` / ``structure_drift`` /
``structure_scenarios`` / ``structure_diff`` for the producer side, where
``structure_scenarios`` (STR-004) **reads intention ⨝ evidence**. The two
coexist; no name reserved for STR-* is consumed here.

Coverage verdicts (``covered | partial | missing | unverifiable``) are not
accepted by any tool in this file — they are derived from producer evidence
and live in ``scenario_assessment`` (STR-002).

Exposed under the ``standard``/``full`` profiles only — ``minimal`` and
``agent`` are explicit allowlists in ``cod_doc/mcp/profiles.py``, so these
names never leak into them.

Activity events (ADO-040): every write goes through ``scenario_service``,
which emits in the same transaction. The wrappers deliberately do NOT emit a
second event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from cod_doc.domain.entities import Scenario


def _serialize(scenario: Scenario) -> dict[str, Any]:
    """Self-sufficient scenario payload (no follow-up call needed for the basics)."""
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "kind": scenario.kind.value,
        "status": scenario.status.value,
        "provenance": scenario.provenance.value,
        "group_key": scenario.group_key,
        "doc_key": scenario.doc_key,
        "section_anchor": scenario.section_anchor,
        "preconditions": scenario.preconditions,
        "expected": scenario.expected,
        "notes": scenario.notes,
        "position": scenario.position,
    }


def register(mcp: FastMCP) -> None:
    """Register scenario.* tools on the given FastMCP instance."""

    @mcp.tool(name="scenario_create")
    def scenario_create(
        title: str,
        kind: str,
        preconditions: str,
        expected: str,
        steps: list[str],
        project: str | None = None,
        group_key: str | None = None,
        doc_key: str | None = None,
        section_anchor: str | None = None,
        notes: str | None = None,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Author one test scenario.

        kind: happy_path | error_path | boundary_value | invariant | integration
        (RFC 24 §9 — the vocabulary is fixed, do not invent values).

        Either group_key or doc_key is required; with doc_key the group
        defaults to the document's basename, which is the projection filename.
        Anchor scenarios on the capability document that states the obligation.

        Status starts at `draft`. Coverage is NOT set here and cannot be:
        whether a test proves this scenario is producer-derived evidence.
        """
        from cod_doc.domain.entities import ScenarioKind
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service, validation

        validation.validate_scenario_kind(kind)

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            created = scenario_service.create(
                session,
                project_id=project_id,
                title=title,
                kind=ScenarioKind(kind),
                preconditions=preconditions,
                expected=expected,
                steps=steps,
                group_key=group_key,
                doc_key=doc_key,
                section_anchor=section_anchor,
                notes=notes,
                author=author,
            )
            return _serialize(created)

    @mcp.tool(name="scenario_get")
    def scenario_get(scenario_id: str, project: str | None = None) -> dict[str, Any] | None:
        """Get one scenario with its steps and links. Returns null if not found."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            found = scenario_service.get(session, project_id, scenario_id)
            if found is None or found.row_id is None:
                return None
            payload = _serialize(found)
            payload["steps"] = [s.text for s in scenario_service.list_steps(session, found.row_id)]
            payload["links"] = [
                {
                    "to_kind": link.to_kind.value,
                    "to_ref": link.to_ref,
                    "relation": link.relation.value,
                }
                for link in scenario_service.list_links(session, found.row_id)
            ]
            return payload

    @mcp.tool(name="scenario_list")
    def scenario_list(
        project: str | None = None,
        group_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """List scenarios, optionally narrowed to one group."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            scenarios = (
                scenario_service.list_for_group(session, project_id, group_key)
                if group_key
                else scenario_service.list_for_project(session, project_id)
            )
            return [_serialize(s) for s in scenarios]

    @mcp.tool(name="scenario_update")
    def scenario_update(
        scenario_id: str,
        project: str | None = None,
        title: str | None = None,
        kind: str | None = None,
        preconditions: str | None = None,
        expected: str | None = None,
        notes: str | None = None,
        status: str | None = None,
        section_anchor: str | None = None,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Patch the given fields; everything else is left alone.

        status: draft | confirmed. Use `scenario_retire` for retirement.
        Passing an RFC 24 §9 coverage verdict here is rejected with SCV-003.
        """
        from cod_doc.domain.entities import ScenarioKind, ScenarioStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service, validation

        # Validate before coercing: a coverage verdict must fail with SCV-003's
        # explanation, not with a bare "not a valid ScenarioStatus".
        if status is not None:
            validation.validate_scenario_status(status)
        if kind is not None:
            validation.validate_scenario_kind(kind)

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            updated = scenario_service.update(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                author=author,
                title=title,
                kind=ScenarioKind(kind) if kind else None,
                preconditions=preconditions,
                expected=expected,
                notes=notes,
                status=ScenarioStatus(status) if status else None,
                section_anchor=section_anchor,
            )
            return _serialize(updated)

    @mcp.tool(name="scenario_retire")
    def scenario_retire(
        scenario_id: str,
        project: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Retire a scenario. The row and its id are kept; ids are never reused."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            retired = scenario_service.retire(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                author=author,
                reason=reason,
            )
            return _serialize(retired)

    @mcp.tool(name="scenario_set_steps")
    def scenario_set_steps(
        scenario_id: str,
        steps: list[str],
        project: str | None = None,
        author: str = "mcp",
    ) -> list[str]:
        """Replace every step of the scenario, renumbering from the start.

        One action per step, in order. Steps are actions, not assertions —
        the assertion belongs in `expected`.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            saved = scenario_service.set_steps(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                steps=steps,
                author=author,
            )
            return [s.text for s in saved]

    @mcp.tool(name="scenario_link")
    def scenario_link(
        scenario_id: str,
        to_kind: str,
        to_ref: str,
        relation: str,
        project: str | None = None,
        author: str = "mcp",
        detach: bool = False,
    ) -> dict[str, Any]:
        """Attach (or, with detach=true, remove) one scenario edge.

        to_kind: task | story | document | criterion
          (`criterion` refs a story acceptance row as `<STORY-ID>#<position>`).
        relation: verifies | specified_in | exercised_by

        Targets are checked for shape, not existence: a scenario is usually
        written before the task that implements it.
        """
        from cod_doc.domain.entities import ScenarioLinkKind, ScenarioRelation
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            if detach:
                removed = scenario_service.unlink(
                    session,
                    project_id=project_id,
                    scenario_id=scenario_id,
                    to_kind=ScenarioLinkKind(to_kind),
                    to_ref=to_ref,
                    relation=ScenarioRelation(relation),
                    author=author,
                )
                return {"scenario_id": scenario_id, "detached": removed}
            scenario_service.link(
                session,
                project_id=project_id,
                scenario_id=scenario_id,
                to_kind=ScenarioLinkKind(to_kind),
                to_ref=to_ref,
                relation=ScenarioRelation(relation),
                author=author,
            )
            return {"scenario_id": scenario_id, "attached": True}

    @mcp.tool(name="scenario_export")
    def scenario_export(
        project: str | None = None,
        group_key: str | None = None,
        force: bool = False,
        dry_run: bool = False,
        author: str = "mcp",
    ) -> list[dict[str, Any]]:
        """Write docs/system/scenarios/<group>.md from the scenarios in the DB.

        Markdown is the artefact, the tables are the source. A file that was
        edited by hand is refused rather than overwritten — fix the scenarios
        and re-export, or run `doc import` to take the edit back into the DB.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service

        sf, entry = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            if group_key:
                results = [
                    scenario_service.export_group(
                        session,
                        project_id=project_id,
                        group_key=group_key,
                        root_path=entry.root,
                        author=author,
                        force=force,
                        dry_run=dry_run,
                    )
                ]
            else:
                results = scenario_service.export_all(
                    session,
                    project_id=project_id,
                    root_path=entry.root,
                    author=author,
                    force=force,
                    dry_run=dry_run,
                )
            return [
                {"path": str(r.path), "written": r.written, "document_id": r.document_id}
                for r in results
            ]

    @mcp.tool(name="scenario_coverage")
    def scenario_coverage(
        project: str | None = None,
        group_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Authoring coverage per group — how much has been written down.

        NOT test coverage. Whether a test proves a scenario is RFC 24 §9
        evidence produced elsewhere and is deliberately absent from this
        payload.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import scenario_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            report = (
                [scenario_service.group_coverage(session, project_id, group_key)]
                if group_key
                else scenario_service.project_coverage(session, project_id)
            )
            return [
                {
                    "group_key": c.group_key,
                    "total": c.total,
                    "draft": c.draft,
                    "confirmed": c.confirmed,
                    "retired": c.retired,
                    "by_kind": c.by_kind,
                    "missing_kinds": c.missing_kinds,
                }
                for c in report
            ]
