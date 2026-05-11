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
    result: list[dict[str, Any]] = list(root["subfolders"])
    if root["files"]:
        result.append({
            "name": "(root)",
            "path": "",
            "subfolders": [],
            "files": root["files"],
            "total": len(root["files"]),
        })
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
    # status="" (default) hides deprecated docs — most projects accumulate stale
    # deprecated entries that just create noise. Specific values isolate to a
    # single status; "all" disables the filter entirely.
    q_lower = q.strip().lower()
    type_filter = type.strip()
    status_filter = status.strip()

    def _status_visible(d_status: str) -> bool:
        if status_filter == "" or status_filter == "live":
            return d_status != "deprecated"
        if status_filter == "all":
            return True
        return d_status == status_filter

    filtered = [
        d
        for d in documents
        if (
            (not q_lower or q_lower in d["doc_key"].lower() or q_lower in d["title"].lower())
            and (not type_filter or d["type"] == type_filter)
            and _status_visible(d["status"])
        )
    ]

    counts = {
        "total": len(documents),
        "live": sum(1 for d in documents if d["status"] != "deprecated"),
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

_DOCUMENT_TYPES = [
    "module-spec", "guide", "architecture", "vision", "standard",
    "execution-plan", "decision", "open-question",
    "module-subdoc", "task-section", "execution-log", "user-story", "redirect",
]


@router.get("/p/{slug}/docs/new", response_class=HTMLResponse)
def doc_new_form(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    description: str = Query(""),
    type: str = Query(""),
) -> HTMLResponse:
    proj = get_project(slug)
    return templates.TemplateResponse(
        request,
        "project/doc_new.html",
        {
            "project": {"name": proj.entry.name},
            "document_types": _DOCUMENT_TYPES,
            "doc_status_options": ["draft", "review", "active", "deprecated"],
            "sensitivity_options": ["public", "internal", "confidential", "restricted"],
            "prefill_description": description,
            "prefill_type": type,
        },
    )


@router.post("/p/{slug}/docs/suggest", response_class=JSONResponse)
def doc_suggest(
    request: Request,
    slug: str,
    description: str = Form(...),
) -> JSONResponse:
    """AI-powered meta suggestion: infer title/doc_key/type/preamble from a description."""
    from cod_doc.api.deps import get_config
    from cod_doc.services.ai_text import AIBackendError, suggest_doc_meta

    cfg = get_config()
    try:
        suggestion = suggest_doc_meta(description, cfg=cfg)
    except AIBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse({
        "title": suggestion.title,
        "doc_key": suggestion.doc_key,
        "type": suggestion.type,
        "preamble": suggestion.preamble,
    })


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
    intent: str = Query(""),
    type: str = Query(""),
    source: list[str] = Query(default=[]),
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    sources: list[dict[str, Any]] = []
    for d in docs.list_for_project(session, project_db_id):
        sources.append(
            {
                "doc_key": d.doc_key,
                "title": d.title,
                "type": d.type.value,
                "preselected": d.doc_key in source,
            }
        )
    return templates.TemplateResponse(
        request,
        "project/doc_generate.html",
        {
            "project": {"name": proj.entry.name},
            "sources": sources,
            "document_types": _DOCUMENT_TYPES,
            "prefill_intent": intent,
            "prefill_type": type,
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
            "document_types": _DOCUMENT_TYPES,
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
        section_bodies_str = [str(b) for b in section_bodies]
        for i, (heading, body) in enumerate(
            zip(section_headings, section_bodies_str, strict=False)
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
                body=body,
                author="human:web",
                reason="ai-generate",
            )

        # Auto-register every source doc as an outgoing link. The AI is asked to
        # use [label](key) markdown, but it often forgets — sources were explicitly
        # chosen as context, so we guarantee the link exists by appending a
        # "Источники" section listing any source that wasn't already linked in
        # the generated bodies. The link parser picks markdown links up on insert.
        all_body_text = "\n".join(section_bodies_str) + "\n" + preamble
        missing_sources = [
            s for s in sources
            if s and f"]({s})" not in all_body_text and f"]({s}#" not in all_body_text
        ]
        if missing_sources:
            source_lines = []
            for src_key in missing_sources:
                src_doc = docs.get(session, project_db_id, str(src_key))
                label = src_doc.title if src_doc else str(src_key)
                source_lines.append(f"- [{label}]({src_key})")
            sources_body = (
                "Документ построен на основе следующих контекстных материалов:\n\n"
                + "\n".join(source_lines)
            )
            docs.add_section(
                session,
                document_id=doc.row_id,
                anchor="sources",
                heading="Источники",
                level=2,
                position=len(section_headings),
                body=sources_body,
                author="human:web",
                reason="ai-generate:auto-sources",
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
        # PCA-928: capture sha256 head for change detection on next scan.
        source_sha = imports._head_sha256(fpath)
        try:
            _, created = import_or_update_markdown(
                session,
                project_id=project_db_id,
                doc_key=doc_key,
                raw_markdown=raw,
                fallback_title=fallback_title,
                author="human:web",
                reason="bulk import",
                source_sha256=source_sha,
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


@router.post("/p/{slug}/suggestions/run")
def suggestions_run(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    doc_key: str = Form(...),
) -> JSONResponse:
    """PCA-422 UI: Generate semantic suggestions for one document's sections.

    Form-posted from the doc page «Run suggestions» button.  Best-effort:
    ChromaDB index must already be populated; otherwise returns an empty
    summary with a note.
    """
    from cod_doc.config import Config
    from cod_doc.services import doc_service
    from cod_doc.services.link_service import semantic

    session, project_db_id = db
    doc = doc_service.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    sections = doc_service.get_sections(session, doc.row_id)
    sec_ids = [s.row_id for s in sections if s.row_id is not None]

    cfg = Config.load()
    total = 0
    for sid in sec_ids:
        try:
            suggs = semantic.suggest_for_section(session, int(sid), cfg)
            total += len(suggs)
        except Exception:
            pass
    session.commit()
    return JSONResponse({"sections_processed": len(sec_ids), "suggestions_created": total})


@router.post("/p/{slug}/suggestions/{row_id}/accept", response_class=HTMLResponse)
def suggestion_accept(
    request: Request,
    slug: str,
    row_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> JSONResponse:
    """PCA-422 UI: Accept a link suggestion.

    Appends ``[title](doc-key)`` to a managed «See also» footer of the
    source section's body via ``patch_section`` (which triggers link
    sync), then sets the suggestion's state to 'accepted'.
    """
    from cod_doc.services import doc_service
    from cod_doc.services.link_service import semantic

    session, project_db_id = db
    sugg = semantic.get_suggestion(session, row_id)
    if sugg is None or sugg["state"] != "pending":
        raise HTTPException(404, "Suggestion not found or already resolved")

    sec = doc_service.get_section_by_id(session, sugg["from_section_id"])
    if sec is None:
        raise HTTPException(404, "Section not found")
    parent_doc = doc_service.get_doc_by_id(session, sec.document_id)
    if parent_doc is None or parent_doc.project_id != project_db_id:
        raise HTTPException(404, "Document not found in project")

    target_doc = doc_service.get(session, project_db_id, sugg["to_doc_key"])
    if target_doc is None:
        raise HTTPException(404, f"Target doc not found: {sugg['to_doc_key']}")

    label = target_doc.title or target_doc.doc_key
    new_link_md = f"[{label}](/{target_doc.doc_key})"
    marker_open = "<!-- cod-doc:see-also -->"
    marker_close = "<!-- /cod-doc:see-also -->"

    body = sec.body or ""
    if marker_open in body and marker_close in body:
        # Append to existing managed block (idempotent — skip if already present)
        if new_link_md not in body:
            body = body.replace(
                marker_close,
                f"- {new_link_md}\n{marker_close}",
            )
    else:
        block = f"\n\n{marker_open}\n**See also:**\n- {new_link_md}\n{marker_close}\n"
        body = body.rstrip() + block

    doc_service.patch_section(
        session,
        document_id=parent_doc.row_id,
        anchor=sec.anchor,
        new_body=body,
        author="human:web",
        reason=f"accept suggestion #{row_id}",
    )
    semantic.update_suggestion_state(session, row_id, "accepted")
    session.commit()
    return JSONResponse({"accepted": True, "row_id": row_id})


@router.post("/p/{slug}/suggestions/{row_id}/reject", response_class=HTMLResponse)
def suggestion_reject(
    request: Request,
    slug: str,
    row_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> JSONResponse:
    """PCA-422 UI: Mark a suggestion as rejected (no body change)."""
    from cod_doc.services.link_service import semantic

    session, _ = db
    if not semantic.update_suggestion_state(session, row_id, "rejected"):
        raise HTTPException(404, "Suggestion not found")
    session.commit()
    return JSONResponse({"rejected": True, "row_id": row_id})


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

    # PCA-422 follow-up: Suggested links — pending semantic-similarity hits
    # for sections of this document, with Accept/Reject controls.
    suggestions_by_section: list[dict[str, Any]] = []
    try:
        from cod_doc.services.link_service import semantic as _semantic
        sec_id_to_heading = {s.row_id: s.heading for s in sections_db if s.row_id is not None}
        rows = _semantic.list_suggestions_for_document(
            session, project_id=project_db_id, document_id=doc.row_id,
        )
        # Group by from_section
        grouped: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            grouped.setdefault(r["from_section_id"], []).append({
                "row_id": r["row_id"],
                "to_doc_key": r["to_doc_key"],
                "score": round(r["score"], 3),
            })
        for sec_id, items in grouped.items():
            suggestions_by_section.append({
                "section_id": sec_id,
                "section_heading": sec_id_to_heading.get(sec_id, "?"),
                "items": items,
            })
    except Exception:
        suggestions_by_section = []

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
                "sensitivity": doc.sensitivity.value if doc.sensitivity else "internal",
                "owner": doc.owner or "",
                "preamble": doc.preamble or "",
                "last_updated": doc.last_updated,
            },
            "sections": sections_nav,
            "is_raw": is_raw,
            "raw_body": raw_body,
            "preamble_html": preamble_html,
            "sections_html": sections_html,
            "outgoing_links": outgoing,
            "incoming_links": incoming,
            "suggestions_by_section": suggestions_by_section,
            "has_sections": bool(sections_db),
        },
    )


@router.post("/p/{slug}/docs/{doc_key:path}/expand", response_class=JSONResponse)
def doc_expand(
    request: Request,
    slug: str,
    doc_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    intent: str = Form(""),
) -> JSONResponse:
    """AI-expand: generate sections for an empty or sparse document."""
    from cod_doc.api.deps import get_config
    from cod_doc.services.ai_text import AIBackendError, expand_doc_sections

    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    cfg = get_config()
    try:
        section_drafts = expand_doc_sections(
            doc.preamble or "",
            intent=intent,
            cfg=cfg,
            doc_type=doc.type.value,
        )
    except AIBackendError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    import re as _re

    def _make_anchor(heading: str, idx: int) -> str:
        slug = _re.sub(r"[^\w\s-]", "", heading.lower()).strip()
        slug = _re.sub(r"[\s_]+", "-", slug)
        return slug or f"section-{idx}"

    created = 0
    for idx, draft in enumerate(section_drafts):
        if draft.heading and draft.body:
            try:
                docs.add_section(
                    session,
                    document_id=doc.row_id,
                    anchor=_make_anchor(draft.heading, idx),
                    heading=draft.heading,
                    level=2,
                    position=idx,
                    body=draft.body,
                    author="ai:expand",
                    reason="ai-expand",
                )
                created += 1
            except Exception:
                pass
    session.commit()
    return JSONResponse({"created": created, "sections": [{"heading": s.heading} for s in section_drafts]})
