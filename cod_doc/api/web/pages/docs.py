"""Document list / show / markdown import endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.api.deps import (
    get_config,
    get_project,
    get_project_db,
    try_open_project_db,
)
from cod_doc.api.web.errors import ValidationWebError
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind
from cod_doc.services import doc_service as docs
from cod_doc.services import import_service as imports
from cod_doc.services import revision_service as revisions
from cod_doc.services.import_service import import_or_update_markdown, scan_folder

router = APIRouter()


@router.post("/p/{slug}/docs-accept", response_class=HTMLResponse)
async def doc_accept(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """COD-052: promote a Document's status to ACTIVE (the 'accept' transition).

    Form fields: ``doc_key`` (required), ``new_status`` (optional, defaults to
    'active'; can also send 'review'/'deprecated' to demote).
    """
    proj = get_project(slug)
    session, project_db_id = db
    form = await request.form()
    doc_key = str(form.get("doc_key") or "").strip()
    if not doc_key:
        raise HTTPException(400, "doc_key is required")
    target = str(form.get("new_status") or "active").strip().lower()
    try:
        new_status = DocumentStatus(target)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown status: {target}") from exc

    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")
    docs.update_status(
        session,
        document_id=doc.row_id,
        new_status=new_status,
        author="human:web",
        reason="accept" if new_status == DocumentStatus.ACTIVE else "status",
    )
    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}", status_code=303
    )


def _group_by_folder(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """COD-078: turn a flat doc list into a nested tree keyed by path components.

    Each output node is::

        {"name": <folder name or "(root)">,
         "path": <absolute folder path>,
         "subfolders": [<nodes...>],
         "files": [<doc dict>, ...],
         "total": <docs in this subtree>}

    Folders are sorted alphabetically; files come last. The "(root)" node
    holds top-level documents that have no folder prefix.
    """

    def _new_node(name: str, path: str) -> dict[str, Any]:
        return {"name": name, "path": path, "subfolders": [], "files": [], "total": 0}

    root = _new_node("(root)", "")
    for doc in documents:
        parts = doc["doc_key"].split("/")
        cursor = root
        for folder in parts[:-1]:
            cursor["total"] += 1
            existing = next(
                (s for s in cursor["subfolders"] if s["name"] == folder), None
            )
            if existing is None:
                child_path = f"{cursor['path']}/{folder}" if cursor["path"] else folder
                existing = _new_node(folder, child_path)
                cursor["subfolders"].append(existing)
            cursor = existing
        cursor["total"] += 1
        cursor["files"].append(doc)

    def _sort(node: dict[str, Any]) -> dict[str, Any]:
        node["subfolders"].sort(key=lambda n: n["name"])
        node["files"].sort(key=lambda d: d["doc_key"])
        for sub in node["subfolders"]:
            _sort(sub)
        return node

    _sort(root)
    result: list[dict[str, Any]] = root["subfolders"] + (
        [root] if root["files"] else []
    )
    return result


@router.get("/p/{slug}/docs", response_class=HTMLResponse)
def docs_list(
    request: Request,
    slug: str,
    q: str = "",
    type: str = "",
    status: str = "",
    view: str = "tree",
) -> HTMLResponse:
    proj = get_project(slug)
    documents: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for d in docs.list_for_project(session, project_db_id):
                documents.append(
                    {
                        "doc_key": d.doc_key,
                        "title": d.title,
                        "type": d.type.value,
                        "status": d.status.value,
                        "owner": d.owner or "",
                        "last_updated": d.last_updated,
                    }
                )

    # Filter (server-side) before grouping.
    q_lower = q.strip().lower()
    type_filter = type.strip()
    status_filter = status.strip()
    filtered = [
        d
        for d in documents
        if (
            (not q_lower or q_lower in d["doc_key"].lower() or q_lower in d["title"].lower())
            and (not type_filter or d["type"] == type_filter)
            and (not status_filter or d["status"] == status_filter)
        )
    ]

    counts = {
        "total": len(documents),
        "active": sum(1 for d in documents if d["status"] == "active"),
        "draft": sum(1 for d in documents if d["status"] == "draft"),
        "review": sum(1 for d in documents if d["status"] == "review"),
        "deprecated": sum(1 for d in documents if d["status"] == "deprecated"),
    }

    return templates.TemplateResponse(
        request,
        "project/docs_list.html",
        {
            "project": {"name": proj.entry.name},
            "documents": filtered,
            "tree": _group_by_folder(filtered),
            "db_available": db_available,
            "view": "flat" if view == "flat" else "tree",
            "filters": {"q": q, "type": type_filter, "status": status_filter},
            "counts": counts,
            "doc_status_options": ["draft", "review", "active", "deprecated"],
        },
    )


# ── COD-078: New blank doc ─────────────────────────────────────────────


@router.get("/p/{slug}/docs/new", response_class=HTMLResponse)
def doc_new_form(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    proj = get_project(slug)
    return templates.TemplateResponse(
        request,
        "project/doc_new.html",
        {
            "project": {"name": proj.entry.name},
            "doc_status_options": ["draft", "review", "active", "deprecated"],
            "sensitivity_options": ["public", "internal", "confidential", "restricted"],
        },
    )


@router.post("/p/{slug}/docs/new", response_class=HTMLResponse)
def doc_new_submit(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    doc_key: str = Form(...),
    title: str = Form(...),
    type: str = Form("module-spec"),
    status: str = Form("draft"),
    sensitivity: str = Form("internal"),
    owner: str = Form(""),
    preamble: str = Form(""),
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db

    doc_key = doc_key.strip()
    title = title.strip()
    if not doc_key:
        raise ValidationWebError("doc_key обязателен")
    if not title:
        raise ValidationWebError("title обязателен")

    try:
        doc_type = DocumentType(type)
    except ValueError as exc:
        raise ValidationWebError(f"Неизвестный type: {type}") from exc
    try:
        from cod_doc.domain.entities import DocumentStatus, Sensitivity

        doc_status = DocumentStatus(status)
        sens = Sensitivity(sensitivity)
    except ValueError as exc:
        raise ValidationWebError(f"Неизвестный status/sensitivity: {exc}") from exc

    try:
        docs.create(
            session,
            project_id=project_db_id,
            doc_key=doc_key,
            type=doc_type,
            status=doc_status,
            title=title,
            author="human:web",
            sensitivity=sens,
            owner=owner.strip() or None,
            preamble=preamble,
            reason="web:new",
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValidationWebError(
            f"Документ с таким doc_key уже существует: {doc_key}"
        ) from exc
    except ValueError as exc:
        session.rollback()
        raise ValidationWebError(str(exc)) from exc
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}", status_code=303
    )


# ── COD-078: AI-generate from existing docs ────────────────────────────


@router.get("/p/{slug}/docs/generate", response_class=HTMLResponse)
def doc_generate_form(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    sources: list[dict[str, Any]] = []
    for d in docs.list_for_project(session, project_db_id):
        sources.append(
            {"doc_key": d.doc_key, "title": d.title, "type": d.type.value}
        )
    return templates.TemplateResponse(
        request,
        "project/doc_generate.html",
        {
            "project": {"name": proj.entry.name},
            "sources": sources,
        },
    )


@router.post("/p/{slug}/docs/generate", response_class=HTMLResponse)
async def doc_generate_preview(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Run the AI generator over the selected source docs; return a preview
    fragment with a save form. The DB isn't touched until /generate/save."""
    proj = get_project(slug)
    session, project_db_id = db
    cfg = get_config()
    form = await request.form()
    intent = str(form.get("intent") or "").strip()
    target_type = str(form.get("type") or "module-spec").strip()
    selected_keys = [str(k) for k in form.getlist("source")]

    sources_text: list[tuple[str, str]] = []
    for key in selected_keys:
        doc = docs.get(session, project_db_id, key)
        if doc is None or doc.row_id is None:
            continue
        body = docs.render_body(session, doc.row_id) or doc.preamble or ""
        sources_text.append((doc.doc_key, body))

    notice = ""
    draft = None
    from cod_doc.services import ai_generate
    from cod_doc.services.ai_text import AIBackendError

    try:
        if not sources_text:
            raise AIBackendError("Не выбраны исходные документы.")
        draft, meta = ai_generate.generate_doc_from_sources(
            sources_text, cfg=cfg, intent=intent, target_type=target_type
        )
        notice = f"Draft ready ({meta.input_tokens} in / {meta.output_tokens} out tok)."
        from cod_doc.services import trace_service

        trace_service.record(
            session,
            model=meta.model,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            duration_ms=meta.duration_ms,
            tool_calls=[{"name": "generate_doc_from_sources"}],
        )
        session.commit()
    except AIBackendError as exc:
        notice = f"AI error: {exc}"

    return templates.TemplateResponse(
        request,
        "_frag/doc_draft.html",
        {
            "project": {"name": proj.entry.name},
            "draft": (
                {
                    "doc_key": draft.doc_key,
                    "title": draft.title,
                    "type": draft.type,
                    "preamble": draft.preamble,
                    "sections": draft.sections,
                    "sources": draft.sources,
                }
                if draft
                else None
            ),
            "intent": intent,
            "notice": notice,
        },
    )


@router.post("/p/{slug}/docs/generate/save", response_class=HTMLResponse)
async def doc_generate_save(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    form = await request.form()
    doc_key = str(form.get("doc_key") or "").strip()
    title = str(form.get("title") or "").strip()
    target_type = str(form.get("type") or "module-spec").strip()
    preamble = str(form.get("preamble") or "")
    section_headings = form.getlist("section_heading")
    section_bodies = form.getlist("section_body")
    sources = form.getlist("source")  # readonly metadata, persisted as frontmatter

    if not doc_key or not title:
        raise ValidationWebError("doc_key и title обязательны")
    try:
        doc_type = DocumentType(target_type)
    except ValueError as exc:
        raise ValidationWebError(f"Неизвестный type: {target_type}") from exc

    from cod_doc.domain.entities import DocumentStatus, Sensitivity

    try:
        doc = docs.create(
            session,
            project_id=project_db_id,
            doc_key=doc_key,
            type=doc_type,
            status=DocumentStatus.DRAFT,
            title=title,
            author="human:web",
            sensitivity=Sensitivity.INTERNAL,
            preamble=preamble,
            frontmatter={"generated_from": list(sources)},
            reason="web:ai-generate",
        )
        assert doc.row_id is not None  # docs.create always assigns a row_id
        for i, (heading, body) in enumerate(
            zip(section_headings, section_bodies, strict=False)
        ):
            heading = str(heading).strip()
            if not heading:
                continue
            anchor = (
                heading.lower()
                .replace(" ", "-")
                .replace("/", "-")[:40]
                or f"section-{i}"
            )
            docs.add_section(
                session,
                document_id=doc.row_id,
                anchor=anchor,
                heading=heading,
                level=2,
                position=i,
                body=str(body),
                author="human:web",
                reason="ai-generate",
            )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValidationWebError(
            f"Документ с таким doc_key уже существует: {doc_key}"
        ) from exc
    except ValueError as exc:
        session.rollback()
        raise ValidationWebError(str(exc)) from exc
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}", status_code=303
    )


@router.post("/p/{slug}/docs/import", response_class=HTMLResponse)
def docs_import(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    doc_key: str = Form(""),
    type: str = Form("module-spec"),
    file: UploadFile = File(...),  # noqa: B008 — standard FastAPI form-upload pattern
) -> Response:
    """Upload a markdown file and create a Document + Sections.

    Frontmatter (if present) supplies title / type / status / owner /
    sensitivity; everything else falls back to defensible defaults.
    Sections are split on `## ` headings; preamble is everything before
    the first H2.
    """
    proj = get_project(slug)
    session, project_db_id = db

    if not doc_key.strip():
        raise ValidationWebError("doc_key обязателен")

    try:
        doc_type = DocumentType(type)
    except ValueError as exc:
        raise ValidationWebError(f"Неизвестный type: {type}") from exc

    raw_bytes = file.file.read()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationWebError(
            "Файл не в UTF-8 — ожидается markdown в кодировке UTF-8."
        ) from exc

    fallback_title = (file.filename or doc_key).rsplit("/", 1)[-1]
    if fallback_title.endswith(".md"):
        fallback_title = fallback_title[:-3]

    try:
        doc = imports.import_markdown(
            session,
            project_id=project_db_id,
            doc_key=doc_key.strip(),
            raw_markdown=raw,
            fallback_title=fallback_title,
            fallback_type=doc_type,
            author="human:web",
            reason="web import",
        )
        session.commit()
    except (ValueError, IntegrityError) as exc:
        session.rollback()
        raise ValidationWebError(f"Импорт отклонён: {exc}") from exc

    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc.doc_key}", status_code=303
    )


@router.get("/p/{slug}/docs/import/scan")
def docs_import_scan(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    path: str = Query(default="."),
) -> JSONResponse:
    """PCA-400: Scan project folder and return manifest diff vs DB.

    Returns a JSON array of manifest entries:
    ``[{path, doc_key, title, doc_type, sha256_head, status, reason}]``
    where ``status`` ∈ ``new | changed | unchanged | missing``.
    """
    proj = get_project(slug)
    session, project_db_id = db
    try:
        entries = scan_folder(
            session,
            project_id=project_db_id,
            root=proj.entry.root,
            sub_dir=path,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return JSONResponse([
        {
            "path": e.path,
            "doc_key": e.doc_key,
            "title": e.title,
            "doc_type": e.doc_type,
            "sha256_head": e.sha256_head,
            "status": e.status,
            "reason": e.reason,
        }
        for e in entries
    ])


@router.get("/p/{slug}/docs/import", response_class=HTMLResponse)
def docs_import_page(
    request: Request,
    slug: str,
    path: str = Query(default="."),
) -> HTMLResponse:
    """PCA-401: Bulk import UI — folder manifest with checkboxes."""
    proj = get_project(slug)
    return templates.TemplateResponse(
        "project/docs_import.html",
        {
            "request": request,
            "project": proj.entry,
            "scan_path": path,
            "slug": slug,
        },
    )


@router.post("/p/{slug}/docs/import/apply", response_class=HTMLResponse)
async def docs_import_apply(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """PCA-401: Apply selected files from the bulk-import manifest.

    Expects a JSON body ``{"paths": ["rel/path/file.md", ...]}``.
    Each file is parsed and imported (idempotent: reimport = new revision).
    Returns a JSON summary ``{"imported": N, "errors": [...]}``.
    """
    proj = get_project(slug)
    session, project_db_id = db

    body = await request.json()
    paths: list[str] = body.get("paths", [])
    if not paths:
        raise HTTPException(400, "paths must be a non-empty list")

    imported = 0
    errors: list[str] = []

    for rel_path in paths:
        fpath = proj.entry.root / rel_path
        if not fpath.is_file():
            errors.append(f"{rel_path}: file not found")
            continue
        try:
            raw = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"{rel_path}: {exc}")
            continue

        doc_key = imports._derive_doc_key(str(fpath.relative_to(proj.entry.root)))
        fallback_title = fpath.stem
        try:
            _, created = import_or_update_markdown(
                session,
                project_id=project_db_id,
                doc_key=doc_key,
                raw_markdown=raw,
                fallback_title=fallback_title,
                author="human:web",
                reason="bulk import (new)" if True else "bulk import (update)",
            )
            imported += 1
        except (ValueError, Exception) as exc:
            session.rollback()
            errors.append(f"{rel_path}: {exc}")
            continue

    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(500, f"Commit failed: {exc}") from exc

    return JSONResponse({"imported": imported, "errors": errors})


@router.get("/p/{slug}/docs/{doc_key:path}", response_class=HTMLResponse)
def doc_show(
    request: Request,
    slug: str,
    doc_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    raw: int = 0,
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")
    sections_db = docs.get_sections(session, doc.row_id)

    # Sidebar nav uses the section anchors regardless of mode — they match
    # the `<section id>` we render below (or the in-page hash, harmless in
    # raw mode since browsers tolerate non-existent fragments).
    sections_nav = [
        {"anchor": s.anchor, "heading": s.heading, "level": s.level} for s in sections_db
    ]

    is_raw = bool(raw)
    raw_body: str | None = None
    preamble_html: str | None = None
    sections_html: list[dict[str, Any]] = []

    if is_raw:
        raw_body = docs.render_body(session, doc.row_id) or doc.preamble or ""
    else:
        preamble_html = render_markdown(doc.preamble or "") or None
        # WEB-012: expose head revision_id per section so the edit form can
        # send it back as `expected_parent_revision_id` for optimistic
        # concurrency. None when the section has no revisions yet (rare).
        sections_html = [
            {
                "anchor": s.anchor,
                "heading": s.heading,
                "level": s.level,
                "row_id": s.row_id,
                "body": s.body or "",
                "html": render_markdown(s.body or ""),
                "head_rev": revisions.head_for_entity(
                    session, EntityKind.SECTION, s.row_id
                )
                if s.row_id is not None
                else None,
            }
            for s in sections_db
        ]

    # COD-078: links panel — outgoing links parsed from sections + incoming
    # links from anywhere in the project pointing at this doc_key. All infra
    # touchdowns happen inside link_service so the web layer stays decoupled.
    from cod_doc.services import link_service as links

    outgoing: list[dict[str, Any]] = []
    for s in sections_db:
        if s.row_id is None:
            continue
        for link in links.list_for_section(session, s.row_id):
            # COD-079: anchor + label aren't persisted on Link; the schema
            # only carries to_doc_key / to_task_id / to_story_id. The raw
            # parsed string is enough for the panel since the user can
            # follow the doc link.
            outgoing.append(
                {
                    "to_doc_key": link.to_doc_key,
                    "raw": link.raw,
                    "from_section": s.heading,
                    "resolved": link.resolved,
                    "broken_reason": link.broken_reason,
                }
            )

    incoming_groups: dict[str, dict[str, Any]] = {}
    for il in links.list_incoming_for_doc(session, project_db_id, doc.doc_key):
        bucket = incoming_groups.setdefault(
            il.source_doc_key,
            {
                "doc_key": il.source_doc_key,
                "title": il.source_doc_title,
                "sections": [],
            },
        )
        bucket["sections"].append(
            {"heading": il.section_heading, "anchor": il.section_anchor}
        )
    incoming = sorted(incoming_groups.values(), key=lambda x: x["doc_key"])

    return templates.TemplateResponse(
        request,
        "project/doc_show.html",
        {
            "project": {"name": proj.entry.name},
            "doc": {
                "doc_key": doc.doc_key,
                "path": doc.path,
                "doc_id": doc.row_id,
                "title": doc.title,
                "type": doc.type.value,
                "status": doc.status.value,
                "owner": doc.owner or "",
                "last_updated": doc.last_updated,
            },
            "sections": sections_nav,
            "is_raw": is_raw,
            "raw_body": raw_body,
            "preamble_html": preamble_html,
            "sections_html": sections_html,
            "outgoing_links": outgoing,
            "incoming_links": incoming,
        },
    )
