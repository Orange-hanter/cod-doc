"""Frontmatter (YAML) — render from a DocumentModel + parse + apply back."""

from __future__ import annotations

import contextlib
import re
from datetime import date
from typing import TYPE_CHECKING, Any, TypeAlias, cast

import yaml

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity

if TYPE_CHECKING:
    from cod_doc.infra.models import DocumentModel

# Reserved keys never projected into frontmatter from `frontmatter_json`.
_RESERVED_KEYS = ("projection_hash", "doc_key", "revision")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# What YAML round-trips through `frontmatter_json` can hold.
YamlValue: TypeAlias = (
    "str | int | float | bool | None | date | list[YamlValue] | dict[str, YamlValue]"
)


def _frontmatter_dict(model: DocumentModel) -> dict[str, Any]:
    """Produce a deterministic frontmatter dict from the DB record.

    Key order follows the imported source (`frontmatter_json` preserves it);
    DB-authoritative values win over the stored copies. Keys the source never
    had are not invented — except for a DB-authored document with no stored
    frontmatter at all, which gets the canonical minimum. `title` is only
    projected when the source carried it: otherwise the H1 in the body is the
    document's title (ADO-010).
    """
    computed: dict[str, Any] = {
        "type": model.type,
        "status": model.status,
        "source_of_truth": bool(model.source_of_truth),
        "sensitivity": model.sensitivity,
    }
    if model.owner:
        computed["owner"] = model.owner

    stored = dict(model.frontmatter_json or {})
    for key in _RESERVED_KEYS:
        stored.pop(key, None)

    if not stored:
        fm = dict(computed)
        if model.title:
            fm["title"] = model.title
        return fm

    fm = {}
    for key, value in stored.items():
        if key == "title":
            fm[key] = model.title or value
        else:
            fm[key] = computed.get(key, value)
    for key, value in computed.items():
        if key not in fm:
            fm[key] = value
    return fm


def _yaml_scalar(value: YamlValue) -> YamlValue:
    """Re-hydrate ISO-date strings so YAML emits them unquoted, as authored.

    `_jsonable_frontmatter` stores dates as ISO strings (JSON has no date type);
    dumping them back as strings would quote them — `created: '2026-07-29'` —
    which is a diff on every export of every dated document.
    """
    if isinstance(value, str) and _ISO_DATE.match(value):
        with contextlib.suppress(ValueError):
            return date.fromisoformat(value)
    if isinstance(value, dict):
        return {k: _yaml_scalar(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_yaml_scalar(v) for v in value]
    return value


def _render_frontmatter(fm: dict[str, Any]) -> str:
    dumped = cast(
        "str",
        yaml.dump(
            {k: _yaml_scalar(v) for k, v in fm.items()},
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ),
    )
    return "---\n" + dumped + "---\n"


def _raw_matches_db(model: DocumentModel) -> bool:
    """True when the stored raw YAML block still agrees with the DB record.

    Unknown enum values count as agreeing: the import coerced them to a
    fallback in the DB, and rewriting the file to that fallback would be
    exactly the corruption ADO-010 exists to stop.

    ADO-015 shrank the set this covers — `capability`, `audit-report` and the
    other six corpus types are now real enum members, stored as authored, and
    reach the ordinary comparison below. What is left are values no version of
    cod-doc can store (`kickoff-brief`, `roadmap-index` …); the import reports
    those in `ImportReport.warnings`, and the file still keeps what it said.
    """
    raw = model.frontmatter_raw
    if not raw:
        return False
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        # ADO-064: a block we cannot parse cannot *prove* it disagrees with
        # the DB (real corpus: Orakul's Russian frontmatter with unquoted
        # markdown links breaks safe_load). Rebuilding it from metadata would
        # destroy the author's block — the same corruption the unknown-enum
        # escape hatch below exists to stop. Keep it verbatim; the trade-off
        # is that DB-side metadata edits never reach such a file's frontmatter.
        return True
    if not isinstance(parsed, dict):
        return False

    enums: dict[str, tuple[type[DocumentType | DocumentStatus | Sensitivity], str]] = {
        "type": (DocumentType, model.type),
        "status": (DocumentStatus, model.status),
        "sensitivity": (Sensitivity, model.sensitivity),
    }
    for key, (enum_cls, db_value) in enums.items():
        if key not in parsed:
            continue
        try:
            if enum_cls(str(parsed[key])).value != db_value:
                return False
        except ValueError:
            continue  # unknown value the DB could not store — keep as authored
    if "owner" in parsed and str(parsed["owner"] or "") != (model.owner or ""):
        return False
    if "title" in parsed and str(parsed["title"]) != model.title:
        return False
    return not (
        isinstance(parsed.get("source_of_truth"), bool)
        and parsed["source_of_truth"] != bool(model.source_of_truth)
    )


def _render_frontmatter_block(model: DocumentModel) -> str:
    """Frontmatter for the projection: verbatim when still accurate, else rebuilt.

    A document imported from a file that carried no frontmatter (`""`, as
    opposed to `NULL` for a DB-authored document) gets none back: writing an
    invented `type`/`owner`/`status` into someone's markdown is the same class
    of damage as the glued preamble this task exists to fix.
    """
    if model.frontmatter_raw == "":
        return ""
    if _raw_matches_db(model):
        return "---\n" + (model.frontmatter_raw or "") + "\n---\n"
    return _render_frontmatter(_frontmatter_dict(model))


def _leading_shape(text: str) -> tuple[str | None, bool]:
    """The two things `frontmatter_raw` / `title_in_body` remember about a file.

    Returns `(raw YAML block without the fences or None, has a leading '# H1')`.
    Parsed with the importer's own `parse_markdown`, so the answer is exactly
    what an import (or `backfill_projection_fidelity`) would store — the guard
    in `export.py` must not be a second, drifting copy of that regex.
    """
    from cod_doc.services.import_service import parse_markdown

    parsed = parse_markdown(text)
    return parsed.frontmatter_raw, parsed.title_h1 is not None


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
