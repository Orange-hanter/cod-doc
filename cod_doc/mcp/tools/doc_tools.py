"""MCP tools: doc.* — document operations (COD-032)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import actor_kind_for_author
from cod_doc.mcp.tools._db import doc_to_dict, require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register doc.* tools on the given FastMCP instance."""

    @mcp.tool(name="doc_list")
    def doc_list(project: str) -> list[dict[str, Any]]:
        """List all documents for a project."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            docs = doc_service.list_for_project(session, project_id)
        return [doc_to_dict(d) for d in docs]

    @mcp.tool(name="doc_get")
    def doc_get(
        project: str,
        doc_key: str,
        include_sections: bool = False,
    ) -> dict[str, Any] | None:
        """Get document metadata. Set include_sections=true to also return section list."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None:
                return None
            result = doc_to_dict(d)
            if include_sections and d.row_id is not None:
                sections = doc_service.get_sections(session, d.row_id)
                result["sections"] = [
                    {
                        "anchor": s.anchor,
                        "heading": s.heading,
                        "level": s.level,
                        "position": s.position,
                    }
                    for s in sections
                ]
        return result

    @mcp.tool(name="doc_create")
    def doc_create(
        project: str,
        doc_key: str,
        type: str,
        status: str,
        title: str,
        owner: str | None = None,
        sensitivity: str = "internal",
        path: str | None = None,
        preamble: str = "",
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a new document record.

        type: module-spec | module-subdoc | execution-plan | task-section |
              execution-log | standard | architecture | vision | guide |
              user-story | decision | open-question | redirect |
              design | audit | audit-report | journal | plan | analysis |
              research | capability | scenario-set.
        status: draft | review | active | authoritative | deprecated.
        sensitivity: public | internal | confidential | restricted.
        """
        from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools import _idempotency
        from cod_doc.services import doc_service
        from cod_doc.services.validation import ValidationError

        cached = _idempotency.check("doc_create", idempotency_key)
        if cached is not None:
            return dict(cached, idempotent_replay=True)

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = require_project_id(session, project)
                d = doc_service.create(
                    session,
                    project_id=project_id,
                    doc_key=doc_key,
                    type=DocumentType(type),
                    status=DocumentStatus(status),
                    title=title,
                    author=author,
                    path=path,
                    sensitivity=Sensitivity(sensitivity),
                    owner=owner,
                    preamble=preamble,
                    reason=reason,
                )
                from cod_doc.services import activity_service

                activity_service.emit(
                    session,
                    project_id,
                    "doc.created",
                    actor_kind=actor_kind_for_author(author),
                    actor_id=author,
                    scope_kind="document",
                    scope_id=doc_key,
                    payload={"type": type, "status": status},
                    summary=f"Document {doc_key!r} created by {author}",
                )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        out = doc_to_dict(d)
        if dry_run:
            out["dry_run"] = True
        else:
            _idempotency.store("doc_create", idempotency_key, out)
        from cod_doc.services.skill_service import recommend_for_tool

        recs = recommend_for_tool("doc_create")
        if recs:
            out["recommended_skills"] = recs[:3]
        return out

    @mcp.tool(name="doc_rename")
    def doc_rename(
        project: str,
        doc_key: str,
        new_key: str,
        new_path: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
        cascade_links: bool = True,
    ) -> dict[str, Any]:
        """Rename a document (doc_key and optionally path). Cascades link updates by default."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            updated = doc_service.rename(
                session,
                document_id=d.row_id,
                new_doc_key=new_key,
                author=author,
                new_path=new_path,
                reason=reason,
                cascade_links=cascade_links,
            )
            from cod_doc.services import activity_service

            activity_service.emit(
                session,
                project_id,
                "doc.renamed",
                actor_kind=actor_kind_for_author(author),
                actor_id=author,
                scope_kind="document",
                scope_id=new_key,
                payload={"old_key": doc_key, "new_key": new_key},
                summary=f"Document renamed {doc_key!r} → {new_key!r}",
            )
        return doc_to_dict(updated)

    @mcp.tool(name="doc_body")
    def doc_body(project: str, doc_key: str) -> str:
        """Return the full rendered body of a document (preamble + all sections)."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            body = doc_service.render_body(session, d.row_id)
        return body or ""

    @mcp.tool(name="doc_patch_section")
    def doc_patch_section(
        project: str,
        doc_key: str,
        anchor: str,
        body: str,
        expected_parent_revision_id: str | None = None,
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Replace a section's body in the DB — no markdown file required.

        Mirrors the web inline editor's optimistic-concurrency contract
        (``cod_doc/api/web/fragments/sections.py::section_patch``): pass the
        `revision_id` you last observed (e.g. from `doc_get`/`revision_list`) as
        `expected_parent_revision_id` and a concurrent writer landing first
        raises a conflict instead of silently overwriting. Omit it to write
        unconditionally (matches `task_doc_put`'s `base_revision_id=None`).
        No-op (`changed=false`, no revision written) when `body` already
        equals the current content. `dry_run=True` validates and returns the
        would-be unified diff + result without persisting anything.
        """
        from cod_doc.domain.entities import EntityKind
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service
        from cod_doc.services import revision_service as revisions
        from cod_doc.services.revision_service import NO_PARENT_CHECK, RevisionConflictError

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = require_project_id(session, project)
                d = doc_service.get(session, project_id, doc_key)
                if d is None or d.row_id is None:
                    raise ValueError(f"Document '{doc_key}' not found.")

                section = next(
                    (s for s in doc_service.get_sections(session, d.row_id) if s.anchor == anchor),
                    None,
                )
                if section is None:
                    raise ValueError(f"Section '{anchor}' not found in document '{doc_key}'.")
                old_body = section.body

                expected: str | object = (
                    NO_PARENT_CHECK
                    if expected_parent_revision_id is None
                    else expected_parent_revision_id
                )
                updated = doc_service.patch_section(
                    session,
                    document_id=d.row_id,
                    anchor=anchor,
                    new_body=body,
                    author=author,
                    reason=reason,
                    expected_parent_revision_id=expected,
                )
                assert updated.row_id is not None
                revision_id = revisions.head_for_entity(session, EntityKind.SECTION, updated.row_id)
                changed = old_body != body
        except RevisionConflictError as exc:
            raise ValueError(str(exc)) from exc

        out: dict[str, Any] = {
            "doc_key": doc_key,
            "anchor": anchor,
            "revision_id": revision_id,
            "changed": changed,
            "content_hash": updated.content_hash,
        }
        if dry_run:
            out["diff"] = (
                doc_service.section_diff(old_body, body, doc_key=doc_key, anchor=anchor)
                if changed
                else ""
            )
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_add_section")
    def doc_add_section(
        project: str,
        doc_key: str,
        anchor: str,
        heading: str,
        body: str = "",
        level: int = 2,
        position: int | None = None,
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Append a new section to a document in the DB — no markdown file required.

        Completes the file-free authoring cycle `doc_create` → `doc_add_section`
        → `doc_patch_section`: `doc_create` stores only the preamble, so without
        this tool a DB-authored document can never gain a body.

        `position` defaults to the end of the document; pass an explicit index to
        insert elsewhere (existing sections are not renumbered, mirroring
        `doc_service.add_section`). A duplicate `anchor` raises instead of
        overwriting — use `doc_patch_section` to change an existing section.
        `dry_run=True` resolves the document, rejects a taken anchor and returns
        the would-be position and diff without opening a write at all;
        `created=false` and `revision_id=null` say nothing was stored.
        """
        from cod_doc.domain.entities import EntityKind
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service
        from cod_doc.services import revision_service as revisions
        from cod_doc.services.doc_service import SectionAlreadyExistsError

        sf, _ = session_factory(project)
        revision_id: str | None = None
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = require_project_id(session, project)
                d = doc_service.get(session, project_id, doc_key)
                if d is None or d.row_id is None:
                    raise ValueError(f"Document '{doc_key}' not found.")

                existing = doc_service.get_sections(session, d.row_id)
                at = (
                    max((s.position for s in existing), default=-1) + 1
                    if position is None
                    else position
                )

                if dry_run:
                    # Deliberately never call add_section here: it writes through
                    # `session.begin_nested()`, and a SAVEPOINT on pysqlite is not
                    # undone by the enclosing rollback (STO-022) — a "preview"
                    # built on that rollback would silently create the section.
                    if any(s.anchor == anchor for s in existing):
                        raise SectionAlreadyExistsError(anchor)
                    hash_ = doc_service.content_hash(body)
                else:
                    created = doc_service.add_section(
                        session,
                        document_id=d.row_id,
                        anchor=anchor,
                        heading=heading,
                        level=level,
                        position=at,
                        body=body,
                        author=author,
                        reason=reason,
                    )
                    assert created.row_id is not None
                    revision_id = revisions.head_for_entity(
                        session, EntityKind.SECTION, created.row_id
                    )
                    hash_ = created.content_hash
        except SectionAlreadyExistsError as exc:
            raise ValueError(
                f"Section '{anchor}' already exists in document '{doc_key}' — "
                f"use doc_patch_section to change it."
            ) from exc

        out: dict[str, Any] = {
            "doc_key": doc_key,
            "anchor": anchor,
            "heading": heading,
            "level": level,
            "position": at,
            "revision_id": revision_id,
            "content_hash": hash_,
            "created": not dry_run,
        }
        if dry_run:
            out["diff"] = doc_service.section_create_diff(body, doc_key=doc_key, anchor=anchor)
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_delete_section")
    def doc_delete_section(
        project: str,
        doc_key: str,
        anchor: str,
        author: str = "mcp",
        reason: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove one section from a document in the DB.

        The missing third of the section cycle (`doc_add_section` →
        `doc_patch_section` → this). Without it a heading that left the
        markdown file stayed in the DB forever: `doc import` could patch and
        append, never drop, and once `doc_accept` pinned the file hash
        `doc_drift` reported `in_sync` over the divergence (ADO-213).

        Scope is one section, not the document — whole-document deletion stays
        CLI-only (`cod-doc doc delete`) on purpose. Remaining sections are
        renumbered so `position` stays a dense index.

        The removed body is preserved in the DOCUMENT revision this writes
        (JSON diff, ``op="delete_section"``; the returned ``revision_id``) —
        the history hangs on the living document, not on a dead section row
        whose id SQLite may reuse. `revision_revert` refuses it explicitly
        (`RevertNotSupportedError`). Read the body back out of `revision_get`
        and re-create it with `doc_add_section`.

        `dry_run=True` resolves the section and returns the would-be diff
        without deleting anything.
        """
        from cod_doc.domain.entities import EntityKind
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service
        from cod_doc.services import revision_service as revisions

        sf, _ = session_factory(project)
        revision_id: str | None = None
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")

            before = doc_service.get_sections(session, d.row_id)
            section = next((s for s in before if s.anchor == anchor), None)
            if section is None:
                raise ValueError(f"Section '{anchor}' not found in document '{doc_key}'.")
            heading, position, body = section.heading, section.position, section.body
            # Same number either way: a dry run reports what the real call
            # would leave behind, not what is there now.
            remaining = len(before) - 1

            if not dry_run:
                doc_service.delete_section(
                    session,
                    document_id=d.row_id,
                    anchor=anchor,
                    author=author,
                    reason=reason,
                )
                # ADO-213: ревизия удаления на документе. Поиск по SECTION
                # вернул бы прошлую правку уже удалённой секции.
                revision_id = revisions.head_for_entity(session, EntityKind.DOCUMENT, d.row_id)

        out: dict[str, Any] = {
            "doc_key": doc_key,
            "anchor": anchor,
            "heading": heading,
            "position": position,
            "revision_id": revision_id,
            "deleted": not dry_run,
            "remaining_sections": remaining,
        }
        if dry_run:
            out["diff"] = doc_service.section_delete_diff(body, doc_key=doc_key, anchor=anchor)
            out["dry_run"] = True
        return out

    @mcp.tool(name="doc_accept")
    def doc_accept(
        project: str,
        doc_key: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """COD-052: promote a DRAFT/REVIEW document to ACTIVE (the 'accept' step).

        Writes a DOCUMENT revision and emits a ``doc.accepted`` activity event.
        Accepting an already-ACTIVE document is a no-op transition.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            updated = doc_service.accept(
                session, document_id=d.row_id, author=author, reason=reason
            )
            activity_service.emit(
                session,
                project_id,
                "doc.accepted",
                actor_kind=actor_kind_for_author(author),
                actor_id=author,
                scope_kind="document",
                scope_id=doc_key,
                payload={"status": updated.status.value},
                summary=f"Document {doc_key!r} accepted → {updated.status.value}",
            )
        return doc_to_dict(updated)

    @mcp.tool(name="doc_export")
    def doc_export(
        project: str,
        doc_key: str,
        force: bool = False,
        dry_run: bool = False,
        force_write: bool = False,
    ) -> dict[str, Any]:
        """Export a document projection to disk. Returns {path, written, content_hash, diff}.
        Skips if projection_hash already matches current DB content (unless force=true).
        Refuses (ADO-010) to overwrite a file that does not match the last export/import,
        or to write into a repo that is not cod-doc's own checkout; refuses (ADO-022) to
        rewrite a file whose shape the DB does not remember — run
        doc_backfill_projection first, do NOT force past that one; refuses
        (ADO-015) to rewrite the `type:` of a row whose stored type is an older
        build's coercion — apply migration 0026 (`cod-doc project init`) first.
        Preview with dry_run=true (returns a unified diff, writes nothing),
        override with force_write=true.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service, projection_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            result = projection_service.export_document(
                session,
                d.row_id,
                root_path=root,
                force=force,
                dry_run=dry_run,
                force_write=force_write,
                own_checkout_only=True,
            )
        return {
            "path": str(result.path),
            "written": result.written,
            "content_hash": result.content_hash,
            "diff": result.diff,
        }

    @mcp.tool(name="doc_backfill_projection")
    def doc_backfill_projection(project: str, dry_run: bool = False) -> dict[str, Any]:
        """ADO-022: recover projection-fidelity columns from the files on disk.

        Databases created before migration 0025_projection_fidelity do not remember
        how each file was shaped (frontmatter_raw/title_in_body are NULL), so
        doc_export would rewrite frontmatter and invent headings — and refuses until
        this has run. Unlike an import, only the two shape columns are touched, so
        DB-side metadata changes that were never exported survive.
        Returns {scanned, filled, file_missing, skipped, items}.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import projection_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            report = projection_service.backfill_projection_fidelity(
                session, project_id, root_path=root, dry_run=dry_run
            )
        return {
            "project": project,
            "dry_run": dry_run,
            "scanned": report.scanned,
            "filled": report.filled,
            "file_missing": report.file_missing,
            "skipped": report.skipped,
            "items": [
                {"doc_key": item.doc_key, "path": item.path, "action": item.action.value}
                for item in report.items
            ],
        }

    @mcp.tool(name="doc_drift")
    def doc_drift(project: str, doc_key: str) -> dict[str, Any]:
        """Detect drift between DB content, projection_hash, and the on-disk file.
        status: in_sync | stale_export | edited_in_place | missing.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service, projection_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                raise ValueError(f"Document '{doc_key}' not found.")
            report = projection_service.detect_drift(session, d.row_id, root_path=root)
        return {"doc_key": doc_key, **report.as_payload()}

    @mcp.tool(name="doc_drift_all")
    def doc_drift_all(project: str, limit: int | None = None) -> dict[str, Any]:
        """Project-wide DB↔markdown drift summary.

        Returns counts by status plus issue rows for non-in-sync documents.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import projection_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            report = projection_service.detect_project_drift(
                session,
                project_id,
                root_path=root,
                limit=limit,
            )
        return {
            "project": project,
            "total_docs": report.total_docs,
            "problem_count": report.problem_count,
            "counts": report.counts,
            "issues": [item.as_payload() for item in report.issues],
        }

    # ------------------------------------------------------------------- #
    # RFC 22 §3.3 / SYM-006D + RFC 25 §3.2 (CUR-008): ctx.* family —      #
    # thin aliases over doc_list / doc_drift_all / search_service.search, #
    # named per the symbiosis contract (`cod-doc ctx docs|drift|search`). #
    # Read-only. Since CUR-008 ctx_search / ctx_docs / ctx_drift are part #
    # of the default `agent` (doc-curator) allowlist; the CI-gate wrapper #
    # ctx_drift_gate is not — it stays standard/full, like `minimal`,     #
    # which keeps its own explicit allowlist (cod_doc/mcp/profiles.py).   #
    # ------------------------------------------------------------------- #

    @mcp.tool(name="ctx_docs")
    def ctx_docs(project: str) -> list[dict[str, Any]]:
        """RFC 22 (SYM-006D): document listing for external context consumers.

        Thin alias of ``doc_list`` — same shape, same data. The RFC's
        ``--paths``/``--budget-tokens`` filtering is a CLI concern
        (``cod-doc ctx docs``); the MCP surface stays minimal.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import doc_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            docs = doc_service.list_for_project(session, project_id)
        return [doc_to_dict(d) for d in docs]

    @mcp.tool(name="ctx_search")
    def ctx_search(
        project: str,
        query: str,
        scope: str | None = None,
        limit: int = 20,
        projects: list[str] | None = None,
    ) -> dict[str, Any]:
        """RFC 25 §3.2 (CUR-007): FTS5 search for external context consumers.

        Thin wrapper over ``search_service.search`` — same shape, same data.
        Before searching, lazily reindexes an empty project index (fresh DB,
        or one whose ``db_search_idx`` rows were wiped) via
        ``search_service.ensure_index`` so a curator/orchestrator never sees
        a permanently empty result set just because nobody ran a reindex.
        A non-empty index is left untouched — this is not a periodic refresh.

        ``projects`` (CUR-013 / RFC 22 §3.6) adds neighbouring project slugs
        to the same query. It only works in hub mode: every slug must resolve
        to the same ``db_url`` as ``project``, otherwise the call fails with
        ``cross-project search requires a shared db_url (hub mode): …``
        instead of silently searching one index. Ranking stays on a single
        bm25 scale (one index, one corpus — RFC 22 §2.2), the per-kind
        ``limit`` applies to the merged result, and every hit carries the
        owning project's slug in ``project``.

        ``task`` entries in the result are search hits into the task index,
        not an invitation to pick up or check out a task — this tool is a
        documentation/search surface, not the agent task-flow (``agent_pick``
        et al. own that).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import search_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            extra = (
                search_service.resolve_cross_project_ids(
                    session, project=project, projects=projects
                )
                if projects
                else {}
            )
            meta_index = search_service.ensure_index(session, project_id)
            index_by_project = {
                slug: search_service.ensure_index(session, pid) for slug, pid in extra.items()
            }
            result = search_service.search(
                session,
                project_id=project_id,
                query=query,
                scope=scope,
                limit=limit,
                project_ids=list(extra.values()),
            )
        meta: dict[str, Any] = {"index": meta_index}
        if extra:
            meta["projects"] = [project, *extra]
            meta["index_by_project"] = index_by_project
        return result | {"meta": meta}

    @mcp.tool(name="ctx_drift")
    def ctx_drift(project: str, limit: int | None = None) -> dict[str, Any]:
        """RFC 22 (SYM-006D): project-wide DB↔markdown drift for external consumers.

        Calls ``doc_drift_all`` — same shape, same data. Narrowing to the
        files a PR touched and the engine-shaped output (``prescan: true``,
        ``model: "cod-doc/drift"``) live in ``ctx_drift_gate`` (SYM-010).
        """
        report: dict[str, Any] = doc_drift_all(project=project, limit=limit)
        return report

    @mcp.tool(name="ctx_drift_gate")
    def ctx_drift_gate(
        project: str,
        changed_files: list[str] | None = None,
        pr: int | None = None,
        repo: str | None = None,
        post_comment: bool = False,
    ) -> dict[str, Any]:
        """RFC 22 §3.5 (SYM-010): deterministic documentation gate for a PR.

        Same engine as ``cod-doc ctx drift --changed-files``. Collects three
        classes of *verifiable* facts about the documents a pull request
        touches — projection drift (DB↔markdown), links/anchors that do not
        resolve, and frontmatter violations — and returns them in ai-review's
        finding shape (``prescan: true``, ``model: "cod-doc/drift"``).

        Args:
            project: cod-doc project slug.
            changed_files: repo-relative paths to narrow the scan to. When
                omitted and ``pr`` is given, the list is read from the PR via
                ``gh pr view``. When both are omitted the whole project is
                scanned.
            pr: pull-request number (needed for ``post_comment``).
            repo: ``OWNER/NAME`` for ``gh``; defaults to the repository the
                project path points at.
            post_comment: create or update the gate's own PR comment. The
                comment is found by the marker ``cod-doc:drift-gate:<project>``,
                so a repeated run edits it in place instead of adding a new one.

        Read-only with respect to both the database and the target working
        tree: the only write it ever makes is the PR comment.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import drift_gate_service, gh_service

        if post_comment and pr is None:
            raise ValueError("post_comment=True requires pr=<number>")

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()

        files = changed_files
        if files is None and pr is not None:
            files = gh_service.pr_changed_files(pr, repo=repo, cwd=root)

        # commit=False: resolve_section touches the derived `link` table only
        # in memory, the rollback drops it — same contract as `ctx docs`.
        with transactional(sf, commit=False) as session:
            project_id = require_project_id(session, project)
            gate = drift_gate_service.collect(
                session,
                project=project,
                project_id=project_id,
                root_path=root,
                changed_files=files,
            )

        payload = gate.as_dict()
        if post_comment and pr is not None:
            ref = gh_service.upsert_marker_comment(
                pr,
                drift_gate_service.render_comment(gate, pr=pr),
                drift_gate_service.marker(project),
                repo=repo,
                cwd=root,
            )
            payload["comment"] = {
                "action": ref.action,
                "comment_id": ref.comment_id,
                "url": ref.url,
            }
        return payload
