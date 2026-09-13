"""Export documented obligations and draft properties from cod-doc records.

This is the obligations side of the structure protocol. It never rewrites
source-of-truth documents. Unstructured prose becomes ``draft`` claims/obligations
until a human confirms them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import (
    DocumentModel,
    LinkModel,
    SectionModel,
    StoryAcceptanceModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.models.structure import DocCodeClaimModel
from cod_doc.services.structure_protocol import (
    EDGE_OBLIGATION_KINDS,
    OBLIGATIONS_SCHEMA,
    PROTOCOL_VERSION,
    as_object,
    sha256_text,
    stable_id,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_MUST_RE = re.compile(r"\b(MUST|SHOULD|MAY)\b")
_ERROR_RE = re.compile(r"\b(error|invalid|reject|fail|forbidden)\b", re.I)
_BOUNDARY_RE = re.compile(r"\b(range|boundary|minimum|maximum|at least|at most)\b", re.I)
_INVARIANT_RE = re.compile(r"\b(invariant|always|remain|never)\b", re.I)


def _priority(token: str) -> str:
    return {"MUST": "must", "SHOULD": "should", "MAY": "may"}[token]


def _kind_from_statement(statement: str) -> str:
    if _ERROR_RE.search(statement):
        return "error_path"
    if _BOUNDARY_RE.search(statement):
        return "boundary_value"
    if _INVARIANT_RE.search(statement):
        return "invariant"
    return "happy_path"


def _code_refs_for_section(session: Session, section_id: int) -> list[str]:
    rows = session.execute(
        select(LinkModel).where(
            LinkModel.from_section_id == section_id,
            LinkModel.kind == "code",
        )
    ).scalars()
    refs: list[str] = []
    for link in rows:
        if link.to_symbol:
            path = link.to_file_path or ""
            refs.append(f"{path}#{link.to_symbol}" if path else str(link.to_symbol))
        elif link.to_file_path:
            refs.append(link.to_file_path)
    return sorted(set(refs))


def export_obligations(
    session: Session,
    project_id: int,
    *,
    project_slug: str,
    head_sha: str = "unknown",
) -> dict[str, object]:
    """Build ``obligations_export.v1`` from specs, stories, tasks and claims."""
    obligations: list[dict[str, object]] = []

    sections = session.execute(
        select(SectionModel, DocumentModel)
        .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
        .where(DocumentModel.project_id == project_id)
        .order_by(DocumentModel.doc_key, SectionModel.position)
    ).all()
    for section, document in sections:
        for line in section.body.splitlines():
            match = _MUST_RE.search(line)
            if not match:
                continue
            statement = line.strip()
            content_hash = sha256_text(f"{document.doc_key}#{section.anchor}:{statement}")
            token = match.group(1)
            obligations.append(
                {
                    "id": stable_id("obl", document.doc_key, section.anchor, content_hash),
                    "source": "cod-doc-section",
                    "docRef": document.doc_key,
                    "sectionAnchor": section.anchor,
                    "storyRef": None,
                    "taskRef": None,
                    "moduleRef": None,
                    "contentHash": content_hash,
                    "priority": _priority(token),
                    "kind": _kind_from_statement(statement),
                    "statement": statement,
                    "status": "draft",
                    "contractRefs": _code_refs_for_section(session, section.row_id),
                    "expected": {},
                    "provenance": "import",
                }
            )

    stories = session.execute(
        select(UserStoryModel)
        .where(UserStoryModel.project_id == project_id)
        .order_by(UserStoryModel.story_id)
    ).scalars()
    for story in stories:
        criteria = session.execute(
            select(StoryAcceptanceModel)
            .where(StoryAcceptanceModel.story_id == story.row_id)
            .order_by(StoryAcceptanceModel.position)
        ).scalars()
        for criterion in criteria:
            statement = criterion.criterion.strip()
            if not statement:
                continue
            content_hash = sha256_text(f"{story.story_id}:{statement}")
            obligations.append(
                {
                    "id": stable_id("obl", story.story_id, str(criterion.position), content_hash),
                    "source": "cod-doc-story",
                    "docRef": None,
                    "sectionAnchor": None,
                    "storyRef": story.story_id,
                    "taskRef": None,
                    "moduleRef": None,
                    "contentHash": content_hash,
                    "priority": "must",
                    "kind": _kind_from_statement(statement),
                    "statement": statement,
                    "status": "confirmed" if criterion.met else "draft",
                    "contractRefs": [],
                    "expected": {},
                    "provenance": "story",
                }
            )

    tasks = session.execute(
        select(TaskModel).where(TaskModel.project_id == project_id).order_by(TaskModel.task_id)
    ).scalars()
    for task in tasks:
        statement = (task.acceptance or "").strip()
        if not statement:
            continue
        content_hash = sha256_text(f"{task.task_id}:{statement}")
        obligations.append(
            {
                "id": stable_id("obl", task.task_id, content_hash),
                "source": "cod-doc-task",
                "docRef": None,
                "sectionAnchor": None,
                "storyRef": None,
                "taskRef": task.task_id,
                "moduleRef": None,
                "contentHash": content_hash,
                "priority": "must",
                "kind": _kind_from_statement(statement),
                "statement": statement,
                "status": "draft",
                "contractRefs": [],
                "expected": {},
                "provenance": "task",
            }
        )

    claims = session.execute(
        select(DocCodeClaimModel)
        .where(DocCodeClaimModel.project_id == project_id, DocCodeClaimModel.status == "confirmed")
        .order_by(DocCodeClaimModel.claim_id)
    ).scalars()
    obligations.extend(_obligation_from_claim(claim) for claim in claims)

    obligations.sort(key=lambda item: str(item["id"]))
    hashes = [str(item["contentHash"]) for item in obligations]
    revision = sha256_text("|".join(hashes)) if hashes else sha256_text("empty")
    return {
        "schemaRef": OBLIGATIONS_SCHEMA,
        "version": PROTOCOL_VERSION,
        "kind": "obligations_export",
        "project": project_slug,
        "revision": revision,
        "headSha": head_sha,
        "exportedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "obligations": obligations,
    }


def _obligation_from_claim(claim: DocCodeClaimModel) -> dict[str, object]:
    return {
        "id": claim.claim_id,
        "source": "cod-doc-claim",
        "docRef": claim.doc_key,
        "sectionAnchor": claim.section_anchor,
        "storyRef": None,
        "taskRef": None,
        "moduleRef": None,
        "contentHash": claim.content_hash,
        "priority": "must",
        "kind": "invariant" if claim.kind != "scenario" else "happy_path",
        "statement": f"{claim.kind} {claim.subject_ref}",
        "status": "confirmed",
        "contractRefs": [claim.subject_ref],
        "expected": dict(claim.expected_json or {}),
        "provenance": claim.provenance,
    }


def generate_property_drafts(obligations: list[object]) -> list[dict[str, object]]:
    """Draft property specs for confirmed edge obligations. Execution is opt-in."""
    drafts: list[dict[str, object]] = []
    for raw in obligations:
        obligation = as_object(raw, label="obligation")
        if obligation.get("status") != "confirmed":
            continue
        kind = str(obligation.get("kind") or "")
        if kind not in EDGE_OBLIGATION_KINDS:
            continue
        statement = str(obligation.get("statement") or "")
        generators = ["counterexample"]
        if kind == "boundary_value":
            generators = ["below_minimum", "at_minimum", "at_maximum", "above_maximum"]
        elif kind == "error_path":
            generators = ["invalid_input", "forbidden_state"]
        drafts.append(
            {
                "obligationRef": obligation.get("id"),
                "kind": kind,
                "statement": statement,
                "semanticHash": obligation.get("contentHash"),
                "generators": generators,
                "requiresConfirmation": True,
                "oracle": "obligation-statement",
            }
        )
    drafts.sort(key=lambda item: str(item["obligationRef"]))
    return drafts
