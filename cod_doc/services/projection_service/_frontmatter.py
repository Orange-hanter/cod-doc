"""Frontmatter (YAML) — render from a DocumentModel + parse + apply back."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

import yaml

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity

if TYPE_CHECKING:
    from cod_doc.infra.models import DocumentModel


def _frontmatter_dict(model: DocumentModel) -> dict[str, Any]:
    """Produce a deterministic frontmatter dict from the DB record."""
    fm: dict[str, Any] = {
        "type": model.type,
        "status": model.status,
        "source_of_truth": bool(model.source_of_truth),
        "sensitivity": model.sensitivity,
    }
    if model.owner:
        fm["owner"] = model.owner
    if model.title:
        fm["title"] = model.title
    # Merge stored frontmatter_json for extra fields (tags, schema, …).
    extra = dict(model.frontmatter_json or {})
    # Never overwrite computed fields; also skip reserved fields not for users.
    for key in (
        "type",
        "status",
        "source_of_truth",
        "sensitivity",
        "owner",
        "title",
        "projection_hash",
        "doc_key",
        "revision",
    ):
        extra.pop(key, None)
    fm.update(extra)
    return fm


def _render_frontmatter(fm: dict[str, Any]) -> str:
    dumped = cast("str", yaml.dump(fm, sort_keys=True, allow_unicode=True))
    return "---\n" + dumped + "---\n"


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    yaml_block = content[3:end].strip()
    try:
        loaded = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): v for k, v in loaded.items()}


def _apply_frontmatter_to_model(model: DocumentModel, fm: dict[str, Any]) -> None:
    """Apply recognised frontmatter fields to the ORM model (in-place)."""
    if "type" in fm:
        with contextlib.suppress(ValueError):
            model.type = DocumentType(fm["type"]).value
    if "status" in fm:
        with contextlib.suppress(ValueError):
            model.status = DocumentStatus(fm["status"]).value
    if "owner" in fm:
        model.owner = str(fm["owner"]) if fm["owner"] else None
    if "sensitivity" in fm:
        with contextlib.suppress(ValueError):
            model.sensitivity = Sensitivity(fm["sensitivity"]).value
    if "source_of_truth" in fm and isinstance(fm["source_of_truth"], bool):
        model.source_of_truth = fm["source_of_truth"]
