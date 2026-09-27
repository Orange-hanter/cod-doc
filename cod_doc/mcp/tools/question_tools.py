"""MCP tools: question.* — открытые вопросы проекта (OQM-003).

Вопрос — сущность БД, а не документ: эти тулы не создают ``document`` /
``section`` и ничего не проецируют в markdown. Спецификация —
``docs/system/capabilities/decisions-and-questions.md`` §2.

Exposed under the ``standard``/``full`` profiles only — ``minimal`` and
``agent`` are explicit allowlists in ``cod_doc/mcp/profiles.py``.

Activity events (ADO-040): every write goes through ``question_service``,
which emits in the same transaction. The wrappers do NOT emit a second event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:  # noqa: C901 — один register на семью тулов
    """Register question.* tools on the given FastMCP instance."""

    @mcp.tool(name="question_create")
    def question_create(
        project: str,
        title: str,
        question: str,
        context: str | None = None,
        priority: str = "medium",
        owner: str | None = None,
        options: list[dict[str, str]] | None = None,
        links: list[dict[str, str]] | None = None,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Open a question: a formulation without a decision yet.

        Use a question (not an ADR, not a task) when there is no answer and no
        chosen option yet; once decided, record an ADR and call
        ``question_resolve(by_adr=...)``.

        priority: critical | high | medium | low.
        options: optional answer options ``[{"title": ..., "body": ...}]``
          (body — markdown pros/cons).
        links: optional ``[{"to_kind", "to_ref", "relation", "note"?}]`` — see
          ``question_link`` for kinds and relations.

        Returns the full question card (same shape as ``question_get``).
        """
        from cod_doc.domain.entities import Priority, QuestionLinkKind, QuestionRelation
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            created = question_service.create(
                session,
                project_id=project_id,
                title=title,
                question=question,
                context=context,
                priority=Priority(priority),
                owner=owner,
                options=[(o["title"], o.get("body")) for o in options or []],
                author=author,
                reason=reason,
            )
            for edge in links or []:
                question_service.link(
                    session,
                    project_id=project_id,
                    question_id=created.question_id,
                    to_kind=QuestionLinkKind(edge["to_kind"]),
                    to_ref=edge["to_ref"],
                    relation=QuestionRelation(edge.get("relation", "about")),
                    note=edge.get("note"),
                    author=author,
                )
            fresh = question_service.get(session, project_id, created.question_id)
            assert fresh is not None
            return question_service.question_to_dict(session, fresh)

    @mcp.tool(name="question_get")
    def question_get(project: str, question_id: str) -> dict[str, Any] | None:
        """One question with context, options and links (with last verify result).

        Returns null if not found.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            found = question_service.get(session, project_id, question_id)
            if found is None:
                return None
            return question_service.question_to_dict(session, found)

    @mcp.tool(name="question_list")
    def question_list(
        project: str,
        status: str | None = "open",
        owner: str | None = None,
        priority: str | None = None,
        linked_kind: str | None = None,
        linked_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """List questions; open first, then by priority.

        status: open (default) | resolved | dropped | null for all.
        linked_kind + linked_ref: only questions linked to that target, e.g.
          ``task`` + ``ADO-042`` or ``code`` + ``cod_doc/app.py``.
        """
        from cod_doc.domain.entities import Priority, QuestionLinkKind, QuestionStatus
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        linked = (
            (QuestionLinkKind(linked_kind), linked_ref)
            if linked_kind is not None and linked_ref is not None
            else None
        )
        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            found = question_service.list_for_project(
                session,
                project_id,
                status=QuestionStatus(status) if status else None,
                owner=owner,
                priority=Priority(priority) if priority else None,
                linked_to=linked,
            )
            return [question_service.question_summary(q) for q in found]

    @mcp.tool(name="question_update")
    def question_update(
        project: str,
        question_id: str,
        title: str | None = None,
        question: str | None = None,
        context: str | None = None,
        priority: str | None = None,
        owner: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Patch fields of a question. Empty string clears ``context`` / ``owner``."""
        from cod_doc.domain.entities import Priority
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            updated = question_service.update(
                session,
                project_id=project_id,
                question_id=question_id,
                title=title,
                question=question,
                context=context,
                priority=Priority(priority) if priority else None,
                owner=owner,
                author=author,
                reason=reason,
            )
            return question_service.question_to_dict(session, updated)

    @mcp.tool(name="question_resolve")
    def question_resolve(
        project: str,
        question_id: str,
        resolution: str | None = None,
        by_adr: str | None = None,
        chosen_option: int | None = None,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Close an open question with an answer.

        Needs at least one of: ``resolution`` (answer text), ``by_adr``
        (``ADR-NNN`` — also records a ``resolved_by`` link) or
        ``chosen_option`` (option position). Closing without an answer is
        ``question_drop``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            resolved = question_service.resolve(
                session,
                project_id=project_id,
                question_id=question_id,
                resolution=resolution,
                by_adr=by_adr,
                chosen_option=chosen_option,
                author=author,
                reason=reason,
            )
            return question_service.question_to_dict(session, resolved)

    @mcp.tool(name="question_drop")
    def question_drop(
        project: str,
        question_id: str,
        resolution: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Close a question without an answer; ``resolution`` says why."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            dropped = question_service.drop(
                session,
                project_id=project_id,
                question_id=question_id,
                resolution=resolution,
                author=author,
                reason=reason,
            )
            return question_service.question_to_dict(session, dropped)

    @mcp.tool(name="question_reopen")
    def question_reopen(
        project: str,
        question_id: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Return a resolved/dropped question to ``open``; the old answer is cleared."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            reopened = question_service.reopen(
                session,
                project_id=project_id,
                question_id=question_id,
                author=author,
                reason=reason,
            )
            return question_service.question_to_dict(session, reopened)

    @mcp.tool(name="question_option_add")
    def question_option_add(
        project: str,
        question_id: str,
        title: str,
        body: str | None = None,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Append an answer option. Positions are stable ids: removal leaves a gap."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            opt = question_service.add_option(
                session,
                project_id=project_id,
                question_id=question_id,
                title=title,
                body=body,
                author=author,
            )
            return question_service.option_to_dict(opt)

    @mcp.tool(name="question_option_update")
    def question_option_update(
        project: str,
        question_id: str,
        position: int,
        title: str | None = None,
        body: str | None = None,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Patch an option's title/body. Empty ``body`` clears it."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            opt = question_service.update_option(
                session,
                project_id=project_id,
                question_id=question_id,
                position=position,
                title=title,
                body=body,
                author=author,
            )
            return question_service.option_to_dict(opt)

    @mcp.tool(name="question_option_remove")
    def question_option_remove(
        project: str,
        question_id: str,
        position: int,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Remove an answer option by position."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            question_service.remove_option(
                session,
                project_id=project_id,
                question_id=question_id,
                position=position,
                author=author,
            )
            return {"question_id": question_id, "removed": position}

    @mcp.tool(name="question_link")
    def question_link(
        project: str,
        question_id: str,
        to_kind: str,
        to_ref: str,
        relation: str = "about",
        note: str | None = None,
        detach: bool = False,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Attach (or, with detach=true, remove) one question edge.

        to_kind: document | section | task | adr | story | scenario | finding |
          code | url.
          section — ``<doc_key>#<anchor>``; code — project-relative
          ``path``, ``path#symbol`` or ``path#L10-L20``.
        relation: about | blocks | addressed_by | resolved_by | see_also.

        The ref is checked for shape here; existence is checked by
        ``question_verify`` (a question often predates the task that answers it).
        """
        from cod_doc.domain.entities import QuestionLinkKind, QuestionRelation
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            kind = QuestionLinkKind(to_kind)
            rel = QuestionRelation(relation)
            if detach:
                removed = question_service.unlink(
                    session,
                    project_id=project_id,
                    question_id=question_id,
                    to_kind=kind,
                    to_ref=to_ref,
                    relation=rel,
                    author=author,
                )
                return {"question_id": question_id, "detached": removed}
            edge = question_service.link(
                session,
                project_id=project_id,
                question_id=question_id,
                to_kind=kind,
                to_ref=to_ref,
                relation=rel,
                note=note,
                author=author,
            )
            resolved, broken_reason = question_service.check_edge(session, project_id, kind, to_ref)
            return {
                "question_id": question_id,
                "attached": True,
                "link": question_service.link_to_dict(edge),
                "target_exists": resolved,
                "broken_reason": broken_reason,
            }

    @mcp.tool(name="question_verify")
    def question_verify(project: str, question_id: str | None = None) -> dict[str, Any]:
        """Re-check links of one question (or all) and stamp resolved/broken.

        Code refs use the same rule as document ``code`` links: file under the
        project root, ``L10-L20`` within the file, symbol present. ``url``
        links are not checked (counted as ``unchecked``).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            report = question_service.verify_links(
                session, project_id=project_id, question_id=question_id
            )
            return {
                "checked": report.checked,
                "ok": report.ok,
                "broken": report.broken,
                "unchecked": report.unchecked,
                "broken_links": [
                    {
                        "question_id": b.question_id,
                        "to_kind": b.to_kind,
                        "to_ref": b.to_ref,
                        "relation": b.relation,
                        "broken_reason": b.broken_reason,
                    }
                    for b in report.broken_links
                ],
            }

    @mcp.tool(name="question_import")
    def question_import(
        project: str,
        doc_key: str,
        dry_run: bool = True,
        keep_doc: bool = False,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Move a legacy ``type: open-question`` document into question rows.

        **dry_run defaults to true**: the apply step deletes the document from
        the DB and its markdown file from disk. Review the plan, then call
        again with ``dry_run=false``.

        A registry document (many ``### OQ-NNN`` items) becomes one question
        per item; a single-question document maps its Question section,
        ``### Option …`` headings, navigation links and blocked tasks.
        Sections that look like a decision are reported in ``warnings`` —
        the question stays open for a human to resolve. ``incoming_links``
        counts links from other documents that will break.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import question_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            result = question_service.import_document(
                session,
                project_id=project_id,
                doc_key=doc_key,
                author=author,
                dry_run=dry_run,
                delete_document=not keep_doc,
            )
            return question_service.import_result_to_dict(result)
