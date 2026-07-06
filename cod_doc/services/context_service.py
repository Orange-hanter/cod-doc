"""ContextService L0/L1 — assemble minimal-sufficient context for agents (COD-041).

Public API:
- context_get(session, project_id, target_kind, target_id, depth, token_budget, master_content)

Target kinds: document | task | plan | module
Depth levels:  L0 (metadata only)
               L1 (L0 + body + direct relations)
               L2/L3 deferred to COD-042.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import TaskStatus
from cod_doc.infra.models import (
    DocumentModel,
    LinkModel,
    ModuleModel,
    PlanModel,
    SectionModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_CHARS_PER_TOKEN = 4  # rough approximation
_MASTER_EXCERPT_CHARS = 1200
_SECTION_EXCERPT_CHARS = 600
_MAX_RELATED_TASKS = 10
_MAX_STORIES = 3
_MAX_CHAIN_DEPTH_TASKS = 5
_MAX_CROSS_DOC_LINKS = 5
_MAX_SEMANTIC_HITS = 5


def _excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _stories_for_target(
    session: Session, project_id: int, to_kind: str, to_ref: str
) -> list[dict[str, str]]:
    """Return up to _MAX_STORIES user stories linked to a target."""
    stmt = (
        select(UserStoryModel)
        .join(StoryLinkModel, StoryLinkModel.story_id == UserStoryModel.row_id)
        .where(
            UserStoryModel.project_id == project_id,
            StoryLinkModel.to_kind == to_kind,
            StoryLinkModel.to_ref == to_ref,
        )
        .limit(_MAX_STORIES)
    )
    rows = session.execute(stmt).scalars().all()
    return [{"story_id": r.story_id, "persona": r.persona, "why": "linked"} for r in rows]


def _open_tasks_for_plan(session: Session, plan_id: int) -> list[dict[str, Any]]:
    """Return pending + in_progress tasks for a plan (priority-ordered, capped)."""
    from cod_doc.infra.sql_helpers import priority_sql_order

    stmt = (
        select(TaskModel)
        .where(
            TaskModel.plan_id == plan_id,
            TaskModel.status.in_([TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value]),
        )
        .order_by(priority_sql_order(TaskModel.priority), TaskModel.task_id)
        .limit(_MAX_RELATED_TASKS)
    )
    rows = session.execute(stmt).scalars().all()
    return [
        {"task_id": r.task_id, "title": r.title, "status": r.status, "why": "open"} for r in rows
    ]


def _plan_progress(session: Session, plan_id: int) -> dict[str, int]:
    """Return {done, total} counts for a plan."""
    all_tasks = (
        session.execute(select(TaskModel.status).where(TaskModel.plan_id == plan_id))
        .scalars()
        .all()
    )
    done = sum(1 for s in all_tasks if s == TaskStatus.DONE.value)
    return {"done": done, "total": len(all_tasks)}


# ---------------------------------------------------------------------------
# Target resolvers
# ---------------------------------------------------------------------------


def _resolve_document(session: Session, project_id: int, doc_key: str) -> DocumentModel | None:
    stmt = select(DocumentModel).where(
        DocumentModel.project_id == project_id,
        DocumentModel.doc_key == doc_key,
    )
    return session.execute(stmt).scalar_one_or_none()


def _resolve_task(session: Session, project_id: int, task_id: str) -> TaskModel | None:
    stmt = select(TaskModel).where(
        TaskModel.project_id == project_id,
        TaskModel.task_id == task_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def _resolve_plan(session: Session, project_id: int, scope: str) -> PlanModel | None:
    stmt = select(PlanModel).where(
        PlanModel.project_id == project_id,
        PlanModel.scope == scope,
    )
    return session.execute(stmt).scalar_one_or_none()


def _resolve_module(session: Session, project_id: int, module_id: str) -> ModuleModel | None:
    stmt = select(ModuleModel).where(
        ModuleModel.project_id == project_id,
        ModuleModel.module_id == module_id,
    )
    return session.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Assemblers per target kind
# ---------------------------------------------------------------------------


def _build_document_context(
    session: Session,
    project_id: int,
    doc: DocumentModel,
    depth: str,
    budget_chars: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int]:
    """Return (target_summary, core, related, hints, chars_used)."""
    used = 0

    target_summary: dict[str, Any] = {
        "title": doc.title,
        "type": doc.type,
        "status": doc.status,
        "owner": doc.owner,
        "sensitivity": doc.sensitivity,
        "path": doc.path,
    }

    core: dict[str, Any] = {}
    related: dict[str, Any] = {"documents": [], "tasks": [], "stories": [], "dependencies": []}
    hints: dict[str, Any] = {"next_best_reads": [], "open_questions": []}

    if depth == "L0":
        return target_summary, core, related, hints, used

    # L1: pull sections
    sections_stmt = (
        select(SectionModel)
        .where(SectionModel.document_id == doc.row_id)
        .order_by(SectionModel.position)
    )
    sections_rows = session.execute(sections_stmt).scalars().all()

    section_excerpts = []
    for sec in sections_rows:
        body_excerpt = _excerpt(sec.body, _SECTION_EXCERPT_CHARS)
        used += len(body_excerpt)
        if used > budget_chars:
            break
        section_excerpts.append(
            {"anchor": sec.anchor, "heading": sec.heading, "excerpt": body_excerpt}
        )
    core["sections"] = section_excerpts

    # L1: related tasks via plan that owns this doc (via parent_doc_id)
    plan_stmt = select(PlanModel).where(
        PlanModel.project_id == project_id,
        PlanModel.parent_doc_id == doc.row_id,
    )
    plan = session.execute(plan_stmt).scalar_one_or_none()
    if plan:
        related["tasks"] = _open_tasks_for_plan(session, plan.row_id)
        progress = _plan_progress(session, plan.row_id)
        hints["plan_progress"] = progress
        if progress["total"] > 0:
            pct = int(100 * progress["done"] / progress["total"])
            hints["next_best_reads"].append(
                f"Plan '{plan.scope}': {progress['done']}/{progress['total']} done ({pct}%)"
            )

    # L1: linked stories
    related["stories"] = _stories_for_target(session, project_id, "document", doc.doc_key)

    return target_summary, core, related, hints, used


def _build_task_context(
    session: Session,
    project_id: int,
    task: TaskModel,
    depth: str,
    budget_chars: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int]:
    used = 0

    target_summary: dict[str, Any] = {
        "title": task.title,
        "status": task.status,
        "type": task.type,
        "priority": task.priority,
        "task_id": task.task_id,
    }

    core: dict[str, Any] = {}
    related: dict[str, Any] = {"documents": [], "tasks": [], "stories": [], "dependencies": []}
    hints: dict[str, Any] = {"next_best_reads": [], "open_questions": []}

    if depth == "L0":
        return target_summary, core, related, hints, used

    # L1: task description + acceptance
    if task.description:
        core["description"] = task.description
        used += len(task.description)
    if task.acceptance:
        core["acceptance"] = task.acceptance
        used += len(task.acceptance)

    # L1: sibling tasks in the same plan section (context)
    if task.plan_id and used < budget_chars:
        sibling_stmt = (
            select(TaskModel)
            .where(
                TaskModel.plan_id == task.plan_id,
                TaskModel.section_id == task.section_id,
                TaskModel.task_id != task.task_id,
                TaskModel.status.in_([TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value]),
            )
            .limit(5)
        )
        siblings = session.execute(sibling_stmt).scalars().all()
        related["tasks"] = [
            {"task_id": s.task_id, "title": s.title, "status": s.status, "why": "sibling"}
            for s in siblings
        ]

    # L1: stories linked to this task
    related["stories"] = _stories_for_target(session, project_id, "task", task.task_id)

    return target_summary, core, related, hints, used


def _build_plan_context(
    session: Session,
    project_id: int,
    plan: PlanModel,
    depth: str,
    budget_chars: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int]:
    used = 0

    progress = _plan_progress(session, plan.row_id)
    target_summary: dict[str, Any] = {
        "scope": plan.scope,
        "module_id": plan.module_id,
        "done": progress["done"],
        "total": progress["total"],
    }

    core: dict[str, Any] = {"plan_progress": progress}
    related: dict[str, Any] = {"documents": [], "tasks": [], "stories": [], "dependencies": []}
    hints: dict[str, Any] = {"next_best_reads": [], "open_questions": []}

    if depth == "L0":
        return target_summary, core, related, hints, used

    # L1: open tasks
    related["tasks"] = _open_tasks_for_plan(session, plan.row_id)
    for t in related["tasks"]:
        used += len(t["title"])

    if progress["total"] > 0:
        pct = int(100 * progress["done"] / progress["total"])
        hints["next_best_reads"].append(
            f"{progress['done']}/{progress['total']} tasks done ({pct}%)"
        )

    return target_summary, core, related, hints, used


def _build_module_context(
    session: Session,
    project_id: int,
    module: ModuleModel,
    depth: str,
    budget_chars: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int]:
    used = 0

    target_summary: dict[str, Any] = {
        "module_id": module.module_id,
        "name": module.name,
        "status": module.status,
    }

    core: dict[str, Any] = {}
    related: dict[str, Any] = {"documents": [], "tasks": [], "stories": [], "dependencies": []}
    hints: dict[str, Any] = {"next_best_reads": [], "open_questions": []}

    if depth == "L0":
        return target_summary, core, related, hints, used

    # L1: plan progress
    if module.plan_id:
        progress = _plan_progress(session, module.plan_id)
        core["plan_progress"] = progress
        related["tasks"] = _open_tasks_for_plan(session, module.plan_id)
        for t in related["tasks"]:
            used += len(t["title"])

    # L1: spec doc sections
    if module.spec_doc_id:
        spec_stmt = (
            select(SectionModel)
            .where(SectionModel.document_id == module.spec_doc_id)
            .order_by(SectionModel.position)
            .limit(5)
        )
        spec_sections = session.execute(spec_stmt).scalars().all()
        section_excerpts = []
        for sec in spec_sections:
            body_excerpt = _excerpt(sec.body, _SECTION_EXCERPT_CHARS)
            used += len(body_excerpt)
            if used > budget_chars:
                break
            section_excerpts.append(
                {"anchor": sec.anchor, "heading": sec.heading, "excerpt": body_excerpt}
            )
        core["spec_sections"] = section_excerpts

    # L1: stories
    related["stories"] = _stories_for_target(session, project_id, "module", module.module_id)

    return target_summary, core, related, hints, used


# ---------------------------------------------------------------------------
# L2 enrichment — dependency chains + cross-doc links
# ---------------------------------------------------------------------------


def _enrich_l2_task(session: Session, task: TaskModel, related: dict[str, Any]) -> None:
    """Add depends_on / dependents chains to ``related["dependencies"]``."""
    from cod_doc.services import plan_service

    try:
        forward = plan_service.forward_chain(session, task.task_id)
        reverse = plan_service.reverse_chain(session, task.task_id)
    except plan_service.TaskNotFoundInPlanError:
        return

    deps: list[dict[str, Any]] = []
    for entry in forward[:_MAX_CHAIN_DEPTH_TASKS]:
        deps.append(
            {
                "task_id": entry.task_id,
                "title": entry.title,
                "status": entry.status.value,
                "depth": entry.depth,
                "direction": "blocks",  # this task BLOCKS the target
            }
        )
    for entry in reverse[:_MAX_CHAIN_DEPTH_TASKS]:
        deps.append(
            {
                "task_id": entry.task_id,
                "title": entry.title,
                "status": entry.status.value,
                "depth": entry.depth,
                "direction": "blocked_by",  # target BLOCKS this task
            }
        )
    related["dependencies"] = deps


def _enrich_l2_document(session: Session, doc: DocumentModel, related: dict[str, Any]) -> None:
    """Add cross-document outgoing links to ``related["documents"]``."""
    stmt = (
        select(LinkModel, DocumentModel)
        .join(SectionModel, SectionModel.row_id == LinkModel.from_section_id)
        .join(DocumentModel, DocumentModel.doc_key == LinkModel.to_doc_key)
        .where(
            SectionModel.document_id == doc.row_id,
            LinkModel.to_doc_key.is_not(None),
            LinkModel.to_doc_key != doc.doc_key,
            DocumentModel.project_id == doc.project_id,
        )
        .limit(_MAX_CROSS_DOC_LINKS)
    )
    seen: set[str] = set()
    docs: list[dict[str, Any]] = []
    for _link, target_doc in session.execute(stmt).all():
        if target_doc.doc_key in seen:
            continue
        seen.add(target_doc.doc_key)
        docs.append(
            {
                "doc_key": target_doc.doc_key,
                "title": target_doc.title,
                "type": target_doc.type,
                "why": "outgoing_link",
            }
        )
    if docs:
        related["documents"] = docs


# ---------------------------------------------------------------------------
# L3 enrichment — semantic search via ChromaDB (graceful fallback)
# ---------------------------------------------------------------------------


def _enrich_l3_semantic(
    related: dict[str, Any],
    query: str,
) -> None:
    """Run a semantic search over the project's ChromaDB index, if configured.

    Silent no-op (`related["semantic"] = []`) if the embedding backend is not
    available — keeps L3 callers safe in offline / placeholder-key setups.
    """
    related["semantic"] = []
    if not query.strip():
        return

    try:
        from cod_doc.config import Config
        from cod_doc.core.reindex import search_documents

        cfg = Config.load()
        # Local backend doesn't need an api_key; the openai backend does.
        if cfg.embedding_backend == "openai" and not cfg.api_key:
            return  # no embedding backend → graceful skip

        hits = search_documents(
            query=query,
            chroma_path=cfg.chroma_path,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            embedding_model=cfg.embedding_model,
            embedding_backend=cfg.embedding_backend,
            n_results=_MAX_SEMANTIC_HITS,
        )
        related["semantic"] = hits
    except Exception:
        # Any backend hiccup — log? Not here; ContextService stays read-only & quiet.
        related["semantic"] = []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def context_get(
    session: Session,
    project_id: int,
    target_kind: str,
    target_id: str,
    depth: str = "L1",
    token_budget: int = 8000,
    master_content: str | None = None,
) -> dict[str, Any]:
    """Assemble context for the given target at the requested depth.

    Parameters
    ----------
    session:        Active SQLAlchemy session (read-only queries).
    project_id:     DB row_id of the owning project.
    target_kind:    One of ``document``, ``task``, ``plan``, ``module``.
    target_id:      doc_key / task_id / plan scope / module_id.
    depth:          ``L0`` (metadata) | ``L1`` (body + direct relations) |
                    ``L2`` (L1 + dependency chains + cross-document links) |
                    ``L3`` (L2 + semantic search hits when embeddings are configured).
    token_budget:   Approximate token ceiling; content is truncated when exceeded.
    master_content: Optional pre-loaded MASTER.md text for master_excerpt.
    """
    _VALID_DEPTHS = {"L0", "L1", "L2", "L3"}
    if depth not in _VALID_DEPTHS:
        raise ValueError(f"Invalid depth {depth!r}. Expected one of {sorted(_VALID_DEPTHS)}")

    # L2 and L3 build on top of L1's body+relations work.
    base_depth = "L0" if depth == "L0" else "L1"

    budget_chars = token_budget * _CHARS_PER_TOKEN
    used_chars = 0

    # Master excerpt
    master_excerpt: str | None = None
    if master_content:
        master_excerpt = _excerpt(master_content, _MASTER_EXCERPT_CHARS)
        used_chars += len(master_excerpt)

    # Dispatch by target kind
    target_summary: dict[str, Any]
    core: dict[str, Any]
    related: dict[str, Any]
    hints: dict[str, Any]

    semantic_query = ""

    if target_kind == "document":
        doc = _resolve_document(session, project_id, target_id)
        if doc is None:
            raise ValueError(f"Document not found: {target_id!r}")
        target_summary, core, related, hints, body_used = _build_document_context(
            session, project_id, doc, base_depth, budget_chars - used_chars
        )
        used_chars += body_used
        if depth in {"L2", "L3"}:
            _enrich_l2_document(session, doc, related)
        semantic_query = doc.title

    elif target_kind == "task":
        task = _resolve_task(session, project_id, target_id)
        if task is None:
            raise ValueError(f"Task not found: {target_id!r}")
        target_summary, core, related, hints, body_used = _build_task_context(
            session, project_id, task, base_depth, budget_chars - used_chars
        )
        used_chars += body_used
        if depth in {"L2", "L3"}:
            _enrich_l2_task(session, task, related)
        semantic_query = task.title

    elif target_kind == "plan":
        plan = _resolve_plan(session, project_id, target_id)
        if plan is None:
            raise ValueError(f"Plan not found: {target_id!r}")
        target_summary, core, related, hints, body_used = _build_plan_context(
            session, project_id, plan, base_depth, budget_chars - used_chars
        )
        used_chars += body_used
        semantic_query = plan.scope

    elif target_kind == "module":
        module = _resolve_module(session, project_id, target_id)
        if module is None:
            raise ValueError(f"Module not found: {target_id!r}")
        target_summary, core, related, hints, body_used = _build_module_context(
            session, project_id, module, base_depth, budget_chars - used_chars
        )
        used_chars += body_used
        semantic_query = module.name

    else:
        raise ValueError(
            f"Unknown target_kind {target_kind!r}. Expected one of: document, task, plan, module"
        )

    # L3: semantic search on top of L2.
    if depth == "L3":
        _enrich_l3_semantic(related, semantic_query)

    if master_excerpt:
        core["master_excerpt"] = master_excerpt

    return {
        "target_summary": target_summary,
        "core": core,
        "related": related,
        "hints": hints,
        "meta": {
            "depth": depth,
            "effective_depth": depth,
            "tokens_used": max(1, used_chars // _CHARS_PER_TOKEN),
            "truncated": used_chars > budget_chars,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    }
