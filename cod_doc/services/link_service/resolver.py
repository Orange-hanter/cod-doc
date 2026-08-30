"""Sync / resolve / verify pipeline (DB-bound).

Replaces stored `link` rows from a section's current parse output, then
looks each link's target up in the DB and stamps `to_*` / `resolved` /
`broken_reason` accordingly.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import Link, LinkKind
from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    LinkModel,
    ProjectModel,
    SectionModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.repositories import LinkRepository
from cod_doc.services import activity_service

from ._section_helpers import _link_or_raise, _project_id_for_section, _section_or_raise
from ._types import IncomingLink, ParsedLink, VerifyReport
from .parser import parse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_MD_LABEL_RE = re.compile(r"\[([^\]]+)\]")
_LINE_FRAGMENT_RE = re.compile(r"^L(?P<start>\d+)(?:-L?(?P<end>\d+))?$")


def _extract_label(raw: str) -> str | None:
    """Extract `[label](...)` text from a parsed link's raw string.

    Returns the first bracketed-label group, or None if the raw form has
    no brackets (e.g. bare URL or `[[doc:KEY]]` canonical refs — those
    shouldn't reach IncomingLink rendering anyway).
    """
    m = _MD_LABEL_RE.match(raw)
    return m.group(1) if m else None


def _target_candidates(parsed_key: str, source_doc_key: str | None) -> list[str]:
    """Generate doc_key candidates to try, mirroring importer normalization.

    The parser is purely lexical and cannot know:
    1. that the importer strips a leading ``docs/`` segment from doc_keys
       (see ``import_service._derive_doc_key``), so links written as
       ``docs/foo/bar.md`` from a root-level doc must also match ``foo/bar``;
    2. that bare-filename refs like ``[x](concept.md)`` are relative to the
       source doc's directory, so they should resolve against that prefix.

    Return ordered candidates: the parsed key first, then the docs/-stripped
    variant, then source-dir-prefixed variants. Resolver tries each in order.
    """
    if not parsed_key:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def _add(k: str) -> None:
        if k and k not in seen:
            seen.add(k)
            out.append(k)

    _add(parsed_key)
    if parsed_key.startswith("docs/"):
        _add(parsed_key[5:])

    # Walk up the source-doc directory tree. The parser eats ``../`` segments
    # without tracking how many were consumed, so a bare ``polyglot`` from
    # ``architecture/v2/cloud_connectivity`` may target any of:
    #   architecture/v2/polyglot, architecture/polyglot, polyglot.
    # Trying each level is cheap and bounded by directory depth.
    if source_doc_key and "/" in source_doc_key:
        parts = source_doc_key.split("/")[:-1]  # drop filename component
        while parts:
            prefixed = "/".join(parts) + "/" + parsed_key
            _add(prefixed)
            if prefixed.startswith("docs/"):
                _add(prefixed[5:])
            parts.pop()
    return out


def _lookup_doc_id(session: Session, project_id: int, doc_key: str) -> int | None:
    return session.execute(
        select(DocumentModel.row_id).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()


def _resolve_canonical(
    session: Session,
    project_id: int,
    doc_key: str | None,
    source_doc_key: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Return (resolved, to_doc_key_to_stamp, broken_reason)."""
    if not doc_key:
        return False, None, "missing doc_key"
    for cand in _target_candidates(doc_key, source_doc_key):
        if _lookup_doc_id(session, project_id, cand) is not None:
            return True, cand, None
    return False, None, f"document not found: {doc_key}"


def _project_root(session: Session, project_id: int) -> Path | None:
    proj = session.execute(
        select(ProjectModel).where(ProjectModel.row_id == project_id)
    ).scalar_one_or_none()
    if proj is None or not proj.root_path:
        return None
    return Path(proj.root_path)


def _candidate_paths(parsed_key: str, source_doc_key: str | None) -> list[str]:
    """Return relative filesystem candidates for markdown file/dir refs."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        cleaned = path.strip("/")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)

    for cand in _target_candidates(parsed_key, source_doc_key):
        _add(cand)
        if not cand.endswith("/") and Path(cand).suffix == "":
            _add(f"{cand}.md")
    return out


def _resolve_markdown(
    session: Session,
    project_id: int,
    doc_key: str | None,
    source_doc_key: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Resolve markdown refs to DB docs, or to existing files/directories."""
    ok, to_key, reason = _resolve_canonical(session, project_id, doc_key, source_doc_key)
    if ok or not doc_key:
        return ok, to_key, reason

    root = _project_root(session, project_id)
    if root is None:
        return False, None, f"project root unknown for project_id={project_id}"
    for rel in _candidate_paths(doc_key, source_doc_key):
        target = root / rel
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.exists():
            return True, None, None
    return False, None, reason


def _resolve_section_anchor(
    session: Session,
    project_id: int,
    doc_key: str | None,
    anchor: str | None,
    source_doc_key: str | None = None,
) -> tuple[bool, str | None, str | None]:
    # Intra-doc anchor refs `[X](#anchor)` arrive with no doc_key — the parser
    # has no source context. Resolve them against the source doc.
    if not doc_key and source_doc_key:
        doc_key = source_doc_key
    if not doc_key:
        return False, None, "missing doc_key"
    matched_key: str | None = None
    doc_row: int | None = None
    for cand in _target_candidates(doc_key, source_doc_key):
        row_id = _lookup_doc_id(session, project_id, cand)
        if row_id is not None:
            matched_key = cand
            doc_row = row_id
            break
    if doc_row is None or matched_key is None:
        return False, None, f"document not found: {doc_key}"
    if not anchor:
        return False, matched_key, "missing anchor"
    sec = session.execute(
        select(SectionModel.row_id).where(
            SectionModel.document_id == doc_row,
            SectionModel.anchor == anchor,
        )
    ).scalar_one_or_none()
    if sec is None:
        return False, matched_key, f"anchor not found: {anchor}"
    return True, matched_key, None


def _resolve_task(
    session: Session, project_id: int, task_id: str | None
) -> tuple[bool, str | None, str | None]:
    if not task_id:
        return False, None, "missing task_id"
    stmt = select(TaskModel.row_id).where(
        TaskModel.project_id == project_id,
        TaskModel.task_id == task_id,
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        return False, None, f"task not found: {task_id}"
    return True, task_id, None


def _resolve_story(
    session: Session, project_id: int, story_id: str | None
) -> tuple[bool, str | None, str | None]:
    if not story_id:
        return False, None, "missing story_id"
    stmt = select(UserStoryModel.row_id).where(
        UserStoryModel.project_id == project_id,
        UserStoryModel.story_id == story_id,
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        return False, None, f"story not found: {story_id}"
    return True, story_id, None


def _resolve_adr(
    session: Session, project_id: int, adr_id: str | None
) -> tuple[bool, str | None, str | None]:
    if not adr_id:
        return False, None, "missing adr_id"
    stmt = select(ADRModel.row_id).where(
        ADRModel.project_id == project_id,
        ADRModel.adr_id == adr_id,
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        return False, None, f"adr not found: {adr_id}"
    return True, adr_id, None


def _resolve_wiki(
    session: Session, project_id: int, label: str | None
) -> tuple[bool, str | None, str | None]:
    """Exact match by `title` or `doc_key`. Fuzzy fallback is deferred."""
    if not label:
        return False, None, "missing label"
    stmt = select(DocumentModel.doc_key).where(
        DocumentModel.project_id == project_id,
        (DocumentModel.title == label) | (DocumentModel.doc_key == label),
    )
    doc_key = session.execute(stmt).scalars().first()
    if doc_key is None:
        return False, None, f"wiki target not found: {label}"
    return True, doc_key, None


def _resolve_code_ref(
    session: Session,
    project_id: int,
    file_path: str | None,
    symbol: str | None,
    source_doc_key: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """OBI-020: resolve a code-ref by checking the file exists on disk.

    Returns (ok, file_path_back, reason). The parser normalizes ``./`` and
    leading ``../`` segments without retaining source context, so resolution
    tries root-relative candidates and source-doc-relative candidates.
    Resolution is fail-fast: if the file is missing under the project root,
    ``broken_reason`` carries an actionable message.

    Symbol-level verification (does the file contain ``#symbol``?) is
    a future enhancement — for now we only verify file existence.
    """
    if not file_path:
        return (False, None, "missing file_path")

    root = _project_root(session, project_id)
    if root is None:
        return (False, file_path, f"project root unknown for project_id={project_id}")

    target: Path | None = None
    matched_path: str | None = None
    for candidate in _candidate_paths(file_path, source_doc_key):
        candidate_target = root / candidate
        try:
            candidate_target.relative_to(root)
        except ValueError:
            continue
        if not candidate_target.exists() and candidate_target.suffix == ".py":
            package_init = candidate_target.with_suffix("") / "__init__.py"
            if package_init.exists():
                candidate_target = package_init
        if candidate_target.exists():
            target = candidate_target
            matched_path = candidate_target.relative_to(root).as_posix()
            break
    if target is None:
        return (
            False,
            file_path,
            f"file not found under project root: {file_path!r}",
        )
    if matched_path is None:
        matched_path = file_path
    if not target.is_file():
        return (False, matched_path, f"target is not a regular file: {matched_path!r}")
    # Optionally also check that ``symbol`` appears in the file. We don't
    # parse AST (different per language) — just substring match, which
    # catches typos without false-positive on similar prefixes.
    if symbol:
        line_match = _LINE_FRAGMENT_RE.fullmatch(symbol)
        if line_match:
            total_lines = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            start = int(line_match.group("start"))
            end = int(line_match.group("end") or start)
            if 1 <= start <= end <= total_lines:
                return (True, matched_path, None)
            return (
                False,
                matched_path,
                f"line fragment {symbol!r} outside {matched_path!r}",
            )
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return (False, matched_path, f"unreadable: {exc}")
        if symbol not in text:
            return (
                False,
                matched_path,
                f"symbol {symbol!r} not found in {matched_path!r}",
            )
    return (True, matched_path, None)


def _reparse_link(model: LinkModel) -> ParsedLink:
    """Re-derive a ParsedLink from a stored row's `raw` field.

    We don't persist the parsed pieces (anchor, label, etc.) on the row, so
    when verify/resolve runs we re-parse the `raw` to recover them. The raw
    is always exactly one link form, so `parse()` returns a single entry.
    """
    items = parse(model.raw)
    if not items:
        # Defensive fallback: synthesize a placeholder so downstream code can
        # mark the row broken instead of crashing.
        return ParsedLink(raw=model.raw, kind=LinkKind(model.kind))
    return items[0]


def _source_doc_key_for_section(session: Session, section_id: int) -> str | None:
    return session.execute(
        select(DocumentModel.doc_key)
        .join(SectionModel, SectionModel.document_id == DocumentModel.row_id)
        .where(SectionModel.row_id == section_id)
    ).scalar_one_or_none()


def _source_doc_for_section(session: Session, section_id: int) -> DocumentModel | None:
    return session.execute(
        select(DocumentModel)
        .join(SectionModel, SectionModel.document_id == DocumentModel.row_id)
        .where(SectionModel.row_id == section_id)
    ).scalar_one_or_none()


def _task_exists(session: Session, project_id: int, task_id: str | None) -> bool:
    if not task_id:
        return False
    return (
        session.execute(
            select(TaskModel.row_id).where(
                TaskModel.project_id == project_id,
                TaskModel.task_id == task_id,
            )
        ).scalar_one_or_none()
        is not None
    )


def _coerce_execution_plan_bare_adr_task(
    session: Session,
    *,
    project_id: int,
    source_doc: DocumentModel | None,
    parsed: ParsedLink,
) -> tuple[LinkKind, str | None]:
    """Treat bare ADR-NNN in execution plans as task refs when a task exists.

    The ADR-system roadmap used task IDs such as ADR-006 before ADR refs
    became first-class. Explicit forms like ``[[adr:ADR-006]]`` still mean
    Architecture Decision Record; only the bare token in execution-plan docs
    is contextually a task.
    """
    if (
        source_doc is not None
        and source_doc.type == "execution-plan"
        and parsed.kind is LinkKind.ADR
        and parsed.raw == (parsed.target_adr_id or "")
        and _task_exists(session, project_id, parsed.target_adr_id)
    ):
        return LinkKind.TASK, parsed.target_adr_id
    return parsed.kind, None


def _apply_resolution(
    session: Session,
    *,
    model: LinkModel,
    project_id: int,
    parsed: ParsedLink,
    mark_checked: bool,
) -> tuple[bool, bool]:
    """Compute resolution for one link and write it back.

    Returns (is_url_skipped, is_resolved_after).
    """
    kind = LinkKind(model.kind)
    if kind is LinkKind.URL:
        # No network. resolve() marks URLs as resolved=True; verify() skips.
        if not mark_checked:
            model.resolved = True
            model.broken_reason = None
        return (mark_checked, model.resolved)

    source_doc_key = _source_doc_key_for_section(session, model.from_section_id)

    if kind is LinkKind.CANONICAL:
        ok, to_key, reason = _resolve_canonical(
            session, project_id, parsed.target_doc_key, source_doc_key
        )
        model.to_doc_key = to_key
    elif kind is LinkKind.SECTION:
        ok, to_key, reason = _resolve_section_anchor(
            session, project_id, parsed.target_doc_key, parsed.anchor, source_doc_key
        )
        model.to_doc_key = to_key
    elif kind is LinkKind.MARKDOWN:
        ok, to_key, reason = _resolve_markdown(
            session, project_id, parsed.target_doc_key, source_doc_key
        )
        model.to_doc_key = to_key
    elif kind is LinkKind.WIKI:
        ok, to_key, reason = _resolve_wiki(session, project_id, parsed.target_label)
        model.to_doc_key = to_key
    elif kind is LinkKind.TASK:
        ok, to_id, reason = _resolve_task(
            session, project_id, parsed.target_task_id or model.to_task_id
        )
        model.to_task_id = to_id
    elif kind is LinkKind.STORY:
        ok, to_id, reason = _resolve_story(session, project_id, parsed.target_story_id)
        model.to_story_id = to_id
    elif kind is LinkKind.ADR:
        ok, to_id, reason = _resolve_adr(session, project_id, parsed.target_adr_id)
        model.to_adr_id = to_id
    elif kind is LinkKind.CODE:
        # OBI-020: code refs resolve to a file path on disk relative to the
        # project root. ``parsed.target_file_path`` is what the parser
        # produced; ``parsed.target_symbol`` is the optional ``#fragment``.
        ok, file_path, reason = _resolve_code_ref(
            session,
            project_id,
            parsed.target_file_path,
            parsed.target_symbol,
            source_doc_key,
        )
        model.to_file_path = file_path
        model.to_symbol = parsed.target_symbol
    else:
        # Форвард-совместимость: ветка мертва, пока if-цепочка исчерпывает
        # LinkKind, и оживает при добавлении нового вида ссылки — линк
        # помечается broken вместо NameError. mypy прав «сейчас», мы — «потом».
        ok, reason = False, f"unsupported kind: {kind.value}"  # type: ignore[unreachable]

    model.resolved = ok
    model.broken_reason = None if ok else reason
    if mark_checked:
        model.last_checked = datetime.now(UTC)
    return (False, ok)


def sync_section(session: Session, section_id: int) -> list[Link]:
    """Replace stored `link` rows for the section from its current body parse.

    Newly inserted rows are unresolved (`to_*` left as None). Call
    `resolve_section` to populate them.
    """
    sec = _section_or_raise(session, section_id)
    project_id = _project_id_for_section(session, section_id)
    source_doc = _source_doc_for_section(session, section_id)
    parsed = parse(sec.body)

    repo = LinkRepository(session)
    repo.delete_for_section(section_id)

    inserted: list[LinkModel] = []
    for p in parsed:
        kind, coerced_task_id = _coerce_execution_plan_bare_adr_task(
            session,
            project_id=project_id,
            source_doc=source_doc,
            parsed=p,
        )
        m = LinkModel(
            project_id=project_id,
            from_section_id=section_id,
            raw=p.raw,
            kind=kind.value,
            to_doc_key=None,
            to_task_id=coerced_task_id,
            to_story_id=None,
            # OBI-020: code-ref payload preserved through sync so resolver
            # has access without re-parsing.
            to_file_path=p.target_file_path,
            to_symbol=p.target_symbol,
            resolved=False,
            last_checked=None,
            broken_reason=None,
        )
        session.add(m)
        inserted.append(m)
    session.flush()

    activity_service.emit_for_write(
        session,
        project_id,
        "link.synced",
        "system",
        scope_kind="section",
        scope_id=str(section_id),
        payload={"link_count": len(inserted)},
        summary=f"Section {section_id}: links synced ({len(inserted)} parsed)",
    )

    return [repo._to_domain(m) for m in inserted]


def resolve(session: Session, link_row_id: int) -> Link:
    """Resolve a single link. Idempotent."""
    model = _link_or_raise(session, link_row_id)
    parsed = _reparse_link(model)
    _apply_resolution(
        session,
        model=model,
        project_id=model.project_id,
        parsed=parsed,
        mark_checked=False,
    )
    session.flush()

    activity_service.emit_for_write(
        session,
        model.project_id,
        "link.resolved",
        "system",
        scope_kind="link",
        scope_id=str(link_row_id),
        payload={"resolved": model.resolved, "broken_reason": model.broken_reason},
        summary=f"Link {link_row_id} resolved",
    )

    repo = LinkRepository(session)
    return repo._to_domain(model)


def resolve_section(session: Session, section_id: int) -> list[Link]:
    """Sync (if needed) and resolve all link rows for the section."""
    repo = LinkRepository(session)
    rows = repo.list_for_section(section_id)
    if not rows:
        sync_section(session, section_id)
    project_id = _project_id_for_section(session, section_id)

    stmt = select(LinkModel).where(LinkModel.from_section_id == section_id)
    out: list[Link] = []
    for model in session.execute(stmt).scalars():
        parsed = _reparse_link(model)
        _apply_resolution(
            session,
            model=model,
            project_id=project_id,
            parsed=parsed,
            mark_checked=False,
        )
        out.append(repo._to_domain(model))
    session.flush()

    activity_service.emit_for_write(
        session,
        project_id,
        "link.resolved",
        "system",
        scope_kind="section",
        scope_id=str(section_id),
        payload={
            "link_count": len(out),
            "resolved": sum(1 for link in out if link.resolved),
        },
        summary=f"Section {section_id}: {len(out)} link(s) resolved",
    )

    return out


def verify_section(session: Session, section_id: int) -> VerifyReport:
    """Re-check all links for a section against current DB state."""
    project_id = _project_id_for_section(session, section_id)

    stmt = select(LinkModel).where(LinkModel.from_section_id == section_id)
    ok = broken = skipped = 0
    for model in session.execute(stmt).scalars():
        if LinkKind(model.kind) is LinkKind.URL:
            skipped += 1
            continue
        parsed = _reparse_link(model)
        _, resolved = _apply_resolution(
            session,
            model=model,
            project_id=project_id,
            parsed=parsed,
            mark_checked=True,
        )
        if resolved:
            ok += 1
        else:
            broken += 1
    session.flush()

    activity_service.emit_for_write(
        session,
        project_id,
        "link.verified",
        "system",
        scope_kind="section",
        scope_id=str(section_id),
        payload={"ok": ok, "broken": broken, "skipped": skipped},
        summary=(
            f"Section {section_id}: links verified ({ok} ok, {broken} broken, {skipped} skipped)"
        ),
    )

    return VerifyReport(section_id=section_id, ok=ok, broken=broken, skipped=skipped)


def list_for_section(session: Session, section_id: int) -> list[Link]:
    return LinkRepository(session).list_for_section(section_id)


def list_code_refs(
    session: Session,
    project_id: int,
) -> list[dict[str, object]]:
    """OBI-021 (web): all ``kind='code'`` links in the project, plain dicts.

    Returns dicts (not LinkModel objects) so the web layer doesn't have to
    import infra models — closes F1 of the 2026-05-15 audit.
    """
    rows = list(
        session.execute(
            select(LinkModel)
            .where(LinkModel.project_id == project_id, LinkModel.kind == "code")
            .order_by(LinkModel.to_file_path, LinkModel.to_symbol)
        ).scalars()
    )
    return [
        {
            "file_path": r.to_file_path or "",
            "symbol": r.to_symbol or None,
            "resolved": r.resolved,
            "broken_reason": r.broken_reason,
            "raw": r.raw,
            "from_section_id": r.from_section_id,
        }
        for r in rows
    ]


def list_incoming_for_doc(session: Session, project_id: int, doc_key: str) -> list[IncomingLink]:
    """COD-078: enumerate links pointing at ``doc_key`` with enough source
    context for UI rendering.

    The web layer used to reach into LinkRepository + DocumentModel + SectionModel
    directly for this; that violated the layering test. Service-side helper
    keeps the SQLAlchemy imports in infra-aware code.

    Self-links (source doc == target doc) are excluded.
    """
    rows = LinkRepository(session).list_for_doc_key(project_id, doc_key)
    out: list[IncomingLink] = []
    for link in rows:
        section = session.get(SectionModel, link.from_section_id)
        if section is None:
            continue
        source_doc = session.get(DocumentModel, section.document_id)
        if source_doc is None or source_doc.doc_key == doc_key:
            continue
        out.append(
            IncomingLink(
                source_doc_key=source_doc.doc_key,
                source_doc_title=source_doc.title,
                section_heading=section.heading,
                section_anchor=section.anchor,
                label=_extract_label(link.raw),
            )
        )
    return out
