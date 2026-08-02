"""rename_cascade — fix downstream links + section bodies after `doc_key` rename.

Two rewrite passes per section:
1. Canonical refs `[[doc:OLD]]` / `[[doc:OLD#anchor]]` → `[[doc:NEW…]]`.
2. Markdown-relative refs `[label](../old/path.md)` → `[label](../new/path.md)`
   when a `path_map` is supplied (COD-014a).

Each modified section gets a SECTION revision via DocService.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import LinkKind
from cod_doc.infra.models import DocumentModel, LinkModel, SectionModel
from cod_doc.services import doc_service as docs

from ._section_helpers import _section_or_raise
from ._types import RenameCascadeReport
from .parser import _MD_LINK_RE

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _rewrite_canonical_refs(body: str, old_doc_key: str, new_doc_key: str) -> str:
    """Rewrite `[[doc:OLD]]` / `[[doc:OLD#anchor]]` → `[[doc:NEW…]]`."""
    pattern = re.compile(r"\[\[doc:" + re.escape(old_doc_key) + r"((?:#[^\]\n]+)?)\]\]")
    return pattern.sub(lambda m: f"[[doc:{new_doc_key}{m.group(1)}]]", body)


def _resolve_md_href(href: str, source_dir: str) -> tuple[str, str] | None:
    """Resolve a markdown href against source_dir. Returns (normalized_target, anchor).

    Returns None for URLs / mailto / empty / fragment-only refs.
    """
    if not href or href.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return None
    anchor = ""
    target = href
    if "#" in target:
        target, _, anchor = target.partition("#")
    if not target:
        return None
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        joined = posixpath.join(source_dir, target) if source_dir else target
        normalized = posixpath.normpath(joined)
    return (normalized, anchor)


def _make_relative_href(target_path: str, source_dir: str, anchor: str) -> str:
    """Compute new href = relative path from source_dir to target_path (+anchor)."""
    rel = posixpath.relpath(target_path, source_dir) if source_dir else target_path
    if anchor:
        rel += "#" + anchor
    return rel


def _rewrite_markdown_relative_refs(
    body: str,
    *,
    source_doc_path: str,
    path_map: dict[str, str],
) -> str:
    """Rewrite `[label](rel.md…)` whose target resolves to a key of `path_map`.

    `source_doc_path` is the path of the document hosting `body`; relative
    hrefs resolve against its parent directory. Refs to URLs, anchors-only,
    or paths not in `path_map` are left untouched.
    """
    if not path_map:
        return body
    source_dir = posixpath.dirname(source_doc_path)

    def _replace(m: re.Match[str]) -> str:
        label = m.group(1)
        href = m.group(2).strip()
        resolved = _resolve_md_href(href, source_dir)
        if resolved is None:
            return m.group(0)
        target, anchor = resolved
        new_target = path_map.get(target)
        if new_target is None:
            return m.group(0)
        return f"[{label}]({_make_relative_href(new_target, source_dir, anchor)})"

    return _MD_LINK_RE.sub(_replace, body)


def rename_cascade(
    session: Session,
    *,
    project_id: int,
    old_doc_key: str,
    new_doc_key: str,
    author: str,
    reason: str | None = None,
    path_map: dict[str, str] | None = None,
) -> RenameCascadeReport:
    """Cascade-update link rows + section bodies after `doc_key` rename.

    Pre-condition: caller already updated `document.doc_key` (and `path` if
    different) — typically via `DocService.rename`. This service fixes
    downstream `link` rows and rewrites both canonical refs (`[[doc:OLD]]`)
    and — when `path_map` is provided — markdown-relative refs
    (`[label](../old/path.md)`) in section bodies, writing one SECTION
    revision per modified section.

    `path_map` is `{old_path: new_path}`. When the document's `path` did not
    change pass `None` (or omit) — only canonical refs are rewritten. The
    dict shape is forward-looking for bulk renames; a single-doc rename
    passes a single-entry dict.
    """
    no_key_change = old_doc_key == new_doc_key
    no_path_change = not path_map or all(o == n for o, n in path_map.items())
    if no_key_change and no_path_change:
        return RenameCascadeReport(
            old_doc_key=old_doc_key,
            new_doc_key=new_doc_key,
            updated_links=0,
            rewritten_sections=0,
        )

    effective_path_map = {o: n for o, n in path_map.items() if o != n} if path_map else {}

    # COD-079: rename_cascade pre-updates link rows in step 1 and patches
    # section bodies in step 3. doc_service.patch_section's auto-sync would
    # wipe and re-resolve rows from raw body — but cascade has already
    # staged the new resolved state, so the auto-sync is redundant and
    # destructive. Toggle the opt-out flag for the duration of cascade.
    from cod_doc.services.doc_service import SKIP_AUTO_LINK_SYNC

    session.info[SKIP_AUTO_LINK_SYNC] = True
    try:
        return _rename_cascade_locked(
            session,
            project_id=project_id,
            old_doc_key=old_doc_key,
            new_doc_key=new_doc_key,
            author=author,
            reason=reason,
            no_key_change=no_key_change,
            effective_path_map=effective_path_map,
        )
    finally:
        session.info.pop(SKIP_AUTO_LINK_SYNC, None)


def _rename_cascade_locked(
    session: Session,
    *,
    project_id: int,
    old_doc_key: str,
    new_doc_key: str,
    author: str,
    reason: str | None,
    no_key_change: bool,
    effective_path_map: dict[str, str],
) -> RenameCascadeReport:
    # 1. Update link rows whose target was the old doc_key.
    affected_links: list[LinkModel] = []
    affected_section_ids: set[int] = set()
    if not no_key_change:
        link_stmt = select(LinkModel).where(
            LinkModel.project_id == project_id,
            LinkModel.to_doc_key == old_doc_key,
        )
        affected_links = list(session.execute(link_stmt).scalars())
        for link in affected_links:
            link.to_doc_key = new_doc_key
            link.raw = _rewrite_canonical_refs(link.raw, old_doc_key, new_doc_key)
            affected_section_ids.add(link.from_section_id)
        if affected_links:
            session.flush()

    # 2. Sections potentially containing markdown-relative refs to a renamed
    #    path. Markdown hrefs only carry the literal path (e.g.
    #    `../M1-auth/overview.md`), and `parse()` strips `../` chains, so
    #    `to_doc_key` does NOT reliably equal the canonical doc_key for
    #    nested-folder layouts. To stay correct we pull in every section
    #    that hosts at least one MARKDOWN/SECTION link — the per-section
    #    body equality check below filters out sections whose bodies don't
    #    actually contain a path in `path_map`.
    if effective_path_map:
        md_section_stmt = select(LinkModel.from_section_id).where(
            LinkModel.project_id == project_id,
            LinkModel.kind.in_((LinkKind.MARKDOWN.value, LinkKind.SECTION.value)),
        )
        for sid in session.execute(md_section_stmt).scalars():
            affected_section_ids.add(sid)

    # 3. Rewrite section bodies (canonical + markdown-relative) and write
    #    SECTION revisions for changed bodies.
    rewritten = 0
    for section_id in sorted(affected_section_ids):
        sec = session.get(SectionModel, section_id)
        if sec is None:
            continue
        new_body = sec.body
        if not no_key_change:
            new_body = _rewrite_canonical_refs(new_body, old_doc_key, new_doc_key)
        if effective_path_map:
            host_doc = session.get(DocumentModel, sec.document_id)
            if host_doc is not None:
                new_body = _rewrite_markdown_relative_refs(
                    new_body,
                    source_doc_path=host_doc.path,
                    path_map=effective_path_map,
                )
        if new_body == sec.body:
            continue
        docs.patch_section(
            session,
            document_id=sec.document_id,
            anchor=sec.anchor,
            new_body=new_body,
            author=author,
            reason=reason or f"rename_cascade {old_doc_key} → {new_doc_key}",
        )
        rewritten += 1

    # 4. Also rewrite link.raw for markdown-relative links pointing at a
    #    renamed path, so that re-resolution stays consistent and a future
    #    audit doesn't see `[label](../OLD.md)` in stored rows.
    if effective_path_map and affected_section_ids:
        md_link_stmt = select(LinkModel).where(
            LinkModel.from_section_id.in_(affected_section_ids),
            LinkModel.kind.in_((LinkKind.MARKDOWN.value, LinkKind.SECTION.value)),
        )
        for link in session.execute(md_link_stmt).scalars():
            host_doc = session.get(
                DocumentModel, _section_or_raise(session, link.from_section_id).document_id
            )
            if host_doc is None:
                continue
            new_raw = _rewrite_markdown_relative_refs(
                link.raw,
                source_doc_path=host_doc.path,
                path_map=effective_path_map,
            )
            if new_raw != link.raw:
                link.raw = new_raw
        session.flush()

    return RenameCascadeReport(
        old_doc_key=old_doc_key,
        new_doc_key=new_doc_key,
        updated_links=len(affected_links),
        rewritten_sections=rewritten,
    )
