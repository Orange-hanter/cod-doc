"""Project one scenario group into `docs/system/scenarios/<group_key>.md`.

Markdown is the artefact, the tables are the source — the same contract every
other cod-doc projection follows. The file therefore goes through
`doc_service` (Document + Section rows) and then `projection_service`, which
means `doc drift`, `doc_drift_all` and `ctx drift` see scenario files for free
and a hand edit is caught by the existing export guards instead of being
silently overwritten.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.models import SectionModel
from cod_doc.services import activity_service, doc_service, projection_service

from .crud import group_keys, list_for_group
from .links import list_links
from .render import render_sections
from .steps import list_steps

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.services.projection_service import ExportResult

DOC_KEY_PREFIX = "docs/system/scenarios"


def doc_key_for(group_key: str) -> str:
    return f"{DOC_KEY_PREFIX}/{group_key}"


def _sync_sections(
    session: Session,
    *,
    project_id: int,
    document_id: int,
    group_key: str,
    author: str,
) -> int:
    """Make the document's sections equal the rendered ones.

    A group whose scenarios were all retired keeps its Overview section: the
    document stays in place so links and registries do not break.
    """
    scenarios = list_for_group(session, project_id, group_key)
    steps_by_scenario = {
        s.row_id: list_steps(session, s.row_id) for s in scenarios if s.row_id is not None
    }
    links_by_scenario = {
        s.row_id: list_links(session, s.row_id) for s in scenarios if s.row_id is not None
    }
    rendered = render_sections(group_key, scenarios, steps_by_scenario, links_by_scenario)
    wanted = {section.anchor: section for section in rendered}

    existing = {
        m.anchor: m
        for m in session.execute(
            select(SectionModel).where(SectionModel.document_id == document_id)
        ).scalars()
    }

    for position, section in enumerate(rendered):
        current = existing.get(section.anchor)
        if current is None:
            doc_service.add_section(
                session,
                document_id=document_id,
                anchor=section.anchor,
                heading=section.heading,
                level=section.level,
                position=position,
                body=section.body,
                author=author,
                reason="scenario export",
            )
            continue
        # `patch_section` is a no-op when the body is unchanged, so a re-export
        # of an untouched group writes no revisions.
        doc_service.patch_section(
            session,
            document_id=document_id,
            anchor=section.anchor,
            new_body=section.body,
            author=author,
            reason="scenario export",
        )
        current.heading = section.heading
        current.position = position

    for anchor, model in existing.items():
        if anchor not in wanted:
            session.delete(model)
    session.flush()
    return len(rendered) - 1  # minus the Overview section


def export_group(
    session: Session,
    *,
    project_id: int,
    group_key: str,
    root_path: Path,
    author: str,
    force: bool = False,
    dry_run: bool = False,
    own_checkout_only: bool = True,
) -> ExportResult:
    """Render the group, upsert its document, and write the file.

    Raises `projection_service.ExportGuardError` when the target file was
    edited by hand — a hand edit of a generated file is drift, not input —
    or, with `own_checkout_only`, when `root_path` is not cod-doc's own
    checkout. Surfaces leave `own_checkout_only` at its default; dropping it
    lifts that one guard without touching the provenance guard, which
    `force_write` would also disable.
    """
    doc_key = doc_key_for(group_key)
    document = doc_service.get(session, project_id, doc_key)
    if document is None:
        document = doc_service.create(
            session,
            project_id=project_id,
            doc_key=doc_key,
            type=DocumentType.SCENARIO_SET,
            status=DocumentStatus.DRAFT,
            title=f"Test scenarios — {group_key}",
            author=author,
            owner="cod-doc core",
            source_of_truth=True,
            frontmatter={
                "generated": True,
                "generated_from": f"capabilities/{group_key}.md",
            },
            reason="scenario export",
        )
    assert document.row_id is not None

    live_count = _sync_sections(
        session,
        project_id=project_id,
        document_id=document.row_id,
        group_key=group_key,
        author=author,
    )
    result = projection_service.export_document(
        session,
        document.row_id,
        root_path=root_path,
        force=force,
        dry_run=dry_run,
        own_checkout_only=own_checkout_only,
    )
    if not dry_run:
        # No revision of our own: the Document/Section writes above already
        # emitted theirs through `doc_service` (the `repo_index.scanned` shape).
        activity_service.emit_for_write(
            session,
            project_id,
            "scenario.exported",
            author,
            scope_kind="scenario_group",
            scope_id=group_key,
            payload={
                "doc_key": doc_key,
                "scenarios": live_count,
                "written": result.written,
            },
            summary=f"Scenario group {group_key} exported ({live_count} scenario(s))",
        )
    return result


def export_all(
    session: Session,
    *,
    project_id: int,
    root_path: Path,
    author: str,
    force: bool = False,
    dry_run: bool = False,
    own_checkout_only: bool = True,
) -> list[ExportResult]:
    """Export every group that has at least one scenario."""
    return [
        export_group(
            session,
            project_id=project_id,
            group_key=key,
            root_path=root_path,
            author=author,
            force=force,
            dry_run=dry_run,
            own_checkout_only=own_checkout_only,
        )
        for key in group_keys(session, project_id)
    ]
