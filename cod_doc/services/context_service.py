"""ContextService — assemble minimal-sufficient context for agents (COD-041..043).

Public API:
- context_get(session, project_id, target_kind, target_id, depth, token_budget, master_content)

Target kinds: document | task | plan | module
Depth levels:  L0 (metadata only)
               L1 (L0 + body + direct relations)
               L2 (L1 + dependency chains + cross-document links)   — COD-042
               L3 (L2 + semantic hits from ChromaDB, graceful skip) — COD-043

``token_budget`` — обещание, а не пожелание (CUR-014). Весь текст пакета
идёт через ``_Budget``, поэтому ``meta.tokens_used <= token_budget``.
Исключение одно: минимальное ядро (``target_summary``) включается всегда,
и если оно само больше бюджета — цифра вылезает, а ``truncated`` встаёт.
Материал L2/L3, который не влезает целиком, не добавляется вовсе, а
``meta.effective_depth`` опускается до фактического уровня (``L1``/``L2``):
деградация видна вызывающему, а не молчит.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import TaskStatus, equivalent_task_statuses
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
    from collections.abc import Iterable, Sequence

    from sqlalchemy.orm import Session

    from cod_doc.core.reindex import SearchHit

_CHARS_PER_TOKEN = 4  # rough approximation
_MASTER_EXCERPT_CHARS = 1200
_SECTION_EXCERPT_CHARS = 600
_MAX_RELATED_TASKS = 10

#: «Открытая задача» — весь класс эквивалентности обоих бакетов.
#:
#: Раньше здесь стояли ровно две строки, `pending` и `in-progress`, то есть
#: половина класса: задача в каноническом `todo` или `in_progress` не
#: попадала в L1-пакет вовсе. После бэкфилла ADO-156 (миграция 0036) это
#: была бы уже не половина, а весь ответ — `context_get` отдавал бы ноль
#: связанных задач.
_OPEN_TASK_STATUSES: frozenset[str] = equivalent_task_statuses(
    TaskStatus.TODO
) | equivalent_task_statuses(TaskStatus.IN_PROGRESS_NEW)
_MAX_STORIES = 3
_MAX_CHAIN_DEPTH_TASKS = 5
_MAX_CROSS_DOC_LINKS = 5
_MAX_SEMANTIC_HITS = 5
_VALID_DEPTHS = ("L0", "L1", "L2", "L3")


def _excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _entries_cost(entries: Iterable[Any]) -> int:
    """Цена списка записей в символах — по значениям, как они уедут в JSON."""
    return sum(len(str(value)) for entry in entries for value in entry.values())


# ---------------------------------------------------------------------------
# Бюджет пакета (CUR-014)
# ---------------------------------------------------------------------------


class _Budget:
    """Символьный счётчик пакета контекста.

    Три операции, по способу траты:

    - ``take(text, cap)`` — обрезать текст под остаток (и под ``cap`` —
      политику выдержки вызывающего) и списать; ``None`` — места нет вовсе;
    - ``afford(chars)`` — списать неделимую порцию, только если влезает;
    - ``charge(chars)`` — списать безусловно; так проходит минимальное ядро
      пакета, которое включается даже когда одно больше бюджета.

    ``truncated`` поднимается, когда материал урезан или отброшен **из-за
    бюджета**. Политика выдержки (``_SECTION_EXCERPT_CHARS`` и подобные)
    флаг не трогает: агенту незачем просить бюджет побольше там, где
    больше и не дадут.
    """

    def __init__(self, limit_chars: int) -> None:
        self.limit = max(0, limit_chars)
        self.used = 0
        self.truncated = False

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def charge(self, chars: int) -> None:
        self.used += chars
        if self.used > self.limit:
            self.truncated = True

    def afford(self, chars: int) -> bool:
        if chars > self.remaining:
            self.truncated = True
            return False
        self.used += chars
        return True

    def take(self, text: str, cap: int | None = None) -> str | None:
        room = self.remaining if cap is None else min(cap, self.remaining)
        if room <= 0:
            self.truncated = True
            return None
        if len(text) > self.remaining:
            self.truncated = True
        excerpt = _excerpt(text, room)
        self.used += len(excerpt)
        return excerpt


def _affordable(entries: list[dict[str, Any]], budget: _Budget) -> list[dict[str, Any]]:
    """Оставить префикс списка, который влезает в остаток бюджета."""
    kept: list[dict[str, Any]] = []
    for entry in entries:
        if not budget.afford(_entries_cost((entry,))):
            break
        kept.append(entry)
    return kept


def _take_bodies(budget: _Budget, bodies: Sequence[tuple[str, str | None]]) -> dict[str, str]:
    """Разложить длинные тела (description/acceptance) по остатку бюджета.

    Равная доля каждому, неиспользованный остаток переходит следующему;
    короткие идут первыми. Пока весь материал влезает, никто не урезан
    (короткое тело не длиннее половины суммы), а при переполнении
    ``description`` не съедает бюджет целиком, оставив ``acceptance`` пустым.
    Порядок ключей в ответе — объявленный, не отсортированный.
    """
    pending = [(key, text) for key, text in bodies if text]
    taken: dict[str, str] = {}
    for index, (key, text) in enumerate(sorted(pending, key=lambda item: len(item[1]))):
        share = budget.remaining // (len(pending) - index)
        excerpt = budget.take(text, share)
        if excerpt is None:
            break
        taken[key] = excerpt
    return {key: taken[key] for key, _ in bodies if key in taken}


def _section_excerpts(
    sections: Sequence[SectionModel],
    budget: _Budget,
) -> list[dict[str, str]]:
    """Выдержки из секций документа под бюджет; обрыв — как только место кончилось."""
    excerpts: list[dict[str, str]] = []
    for sec in sections:
        body_excerpt = budget.take(sec.body, _SECTION_EXCERPT_CHARS)
        if body_excerpt is None:
            break
        excerpts.append({"anchor": sec.anchor, "heading": sec.heading, "excerpt": body_excerpt})
    return excerpts


def _hint(hints: dict[str, Any], line: str, budget: _Budget) -> None:
    """Добавить подсказку, если она влезает в бюджет."""
    if budget.afford(len(line)):
        hints["next_best_reads"].append(line)


# ---------------------------------------------------------------------------
# Собранный пакет
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Packet:
    """L0/L1-пакет плюс отложенный до проверки бюджета L2-материал."""

    target_summary: dict[str, Any]
    core: dict[str, Any]
    related: dict[str, Any]
    hints: dict[str, Any]
    semantic_query: str = ""
    l2_slot: str = ""
    l2_entries: list[dict[str, Any]] | None = None

    @classmethod
    def start(
        cls,
        budget: _Budget,
        target_summary: dict[str, Any],
        semantic_query: str,
    ) -> _Packet:
        """Открыть пакет: ``target_summary`` — минимальное ядро, списывается всегда."""
        budget.charge(_entries_cost((target_summary,)))
        return cls(
            target_summary=target_summary,
            core={},
            related={"documents": [], "tasks": [], "stories": [], "dependencies": []},
            hints={"next_best_reads": [], "open_questions": []},
            semantic_query=semantic_query,
        )


def _stories_for_target(
    session: Session, project_id: int, to_kind: str, to_ref: str, budget: _Budget
) -> list[dict[str, Any]]:
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
    entries: list[dict[str, Any]] = [
        {"story_id": r.story_id, "persona": r.persona, "why": "linked"} for r in rows
    ]
    return _affordable(entries, budget)


def _open_tasks_for_plan(session: Session, plan_id: int, budget: _Budget) -> list[dict[str, Any]]:
    """Return pending + in_progress tasks for a plan (priority-ordered, capped)."""
    from cod_doc.infra.sql_helpers import priority_sql_order

    stmt = (
        select(TaskModel)
        .where(
            TaskModel.plan_id == plan_id,
            TaskModel.status.in_(_OPEN_TASK_STATUSES),
        )
        .order_by(priority_sql_order(TaskModel.priority), TaskModel.task_id)
        .limit(_MAX_RELATED_TASKS)
    )
    rows = session.execute(stmt).scalars().all()
    entries: list[dict[str, Any]] = [
        {"task_id": r.task_id, "title": r.title, "status": r.status, "why": "open"} for r in rows
    ]
    return _affordable(entries, budget)


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
    budget: _Budget,
) -> _Packet:
    packet = _Packet.start(
        budget,
        {
            "title": doc.title,
            "type": doc.type,
            "status": doc.status,
            "owner": doc.owner,
            "sensitivity": doc.sensitivity,
            "path": doc.path,
        },
        doc.title,
    )

    if depth == "L0":
        return packet

    # L1: pull sections
    sections_stmt = (
        select(SectionModel)
        .where(SectionModel.document_id == doc.row_id)
        .order_by(SectionModel.position)
    )
    sections_rows = session.execute(sections_stmt).scalars().all()
    packet.core["sections"] = _section_excerpts(sections_rows, budget)

    # L1: related tasks via plan that owns this doc (via parent_doc_id)
    plan_stmt = select(PlanModel).where(
        PlanModel.project_id == project_id,
        PlanModel.parent_doc_id == doc.row_id,
    )
    plan = session.execute(plan_stmt).scalar_one_or_none()
    if plan:
        packet.related["tasks"] = _open_tasks_for_plan(session, plan.row_id, budget)
        progress = _plan_progress(session, plan.row_id)
        packet.hints["plan_progress"] = progress
        if progress["total"] > 0:
            pct = int(100 * progress["done"] / progress["total"])
            _hint(
                packet.hints,
                f"Plan '{plan.scope}': {progress['done']}/{progress['total']} done ({pct}%)",
                budget,
            )

    # L1: linked stories
    packet.related["stories"] = _stories_for_target(
        session, project_id, "document", doc.doc_key, budget
    )

    return packet


def _build_task_context(
    session: Session,
    project_id: int,
    task: TaskModel,
    depth: str,
    budget: _Budget,
) -> _Packet:
    packet = _Packet.start(
        budget,
        {
            "title": task.title,
            "status": task.status,
            "type": task.type,
            "priority": task.priority,
            "task_id": task.task_id,
        },
        task.title,
    )

    if depth == "L0":
        return packet

    # L1: task description + acceptance — под бюджет, а не поверх него.
    packet.core.update(
        _take_bodies(
            budget,
            (("description", task.description), ("acceptance", task.acceptance)),
        )
    )

    # L1: sibling tasks in the same plan section (context)
    if task.plan_id and budget.remaining:
        sibling_stmt = (
            select(TaskModel)
            .where(
                TaskModel.plan_id == task.plan_id,
                TaskModel.section_id == task.section_id,
                TaskModel.task_id != task.task_id,
                TaskModel.status.in_(_OPEN_TASK_STATUSES),
            )
            .limit(5)
        )
        siblings = session.execute(sibling_stmt).scalars().all()
        packet.related["tasks"] = _affordable(
            [
                {"task_id": s.task_id, "title": s.title, "status": s.status, "why": "sibling"}
                for s in siblings
            ],
            budget,
        )

    # L1: stories linked to this task
    packet.related["stories"] = _stories_for_target(
        session, project_id, "task", task.task_id, budget
    )

    return packet


def _build_plan_context(
    session: Session,
    project_id: int,
    plan: PlanModel,
    depth: str,
    budget: _Budget,
) -> _Packet:
    progress = _plan_progress(session, plan.row_id)
    packet = _Packet.start(
        budget,
        {
            "scope": plan.scope,
            "module_id": plan.module_id,
            "done": progress["done"],
            "total": progress["total"],
        },
        plan.scope,
    )
    packet.core["plan_progress"] = progress

    if depth == "L0":
        return packet

    # L1: open tasks
    packet.related["tasks"] = _open_tasks_for_plan(session, plan.row_id, budget)

    if progress["total"] > 0:
        pct = int(100 * progress["done"] / progress["total"])
        _hint(
            packet.hints,
            f"{progress['done']}/{progress['total']} tasks done ({pct}%)",
            budget,
        )

    return packet


def _build_module_context(
    session: Session,
    project_id: int,
    module: ModuleModel,
    depth: str,
    budget: _Budget,
) -> _Packet:
    packet = _Packet.start(
        budget,
        {
            "module_id": module.module_id,
            "name": module.name,
            "status": module.status,
        },
        module.name,
    )

    if depth == "L0":
        return packet

    # L1: plan progress
    if module.plan_id:
        packet.core["plan_progress"] = _plan_progress(session, module.plan_id)
        packet.related["tasks"] = _open_tasks_for_plan(session, module.plan_id, budget)

    # L1: spec doc sections
    if module.spec_doc_id:
        spec_stmt = (
            select(SectionModel)
            .where(SectionModel.document_id == module.spec_doc_id)
            .order_by(SectionModel.position)
            .limit(5)
        )
        spec_sections = session.execute(spec_stmt).scalars().all()
        packet.core["spec_sections"] = _section_excerpts(spec_sections, budget)

    # L1: stories
    packet.related["stories"] = _stories_for_target(
        session, project_id, "module", module.module_id, budget
    )

    return packet


# ---------------------------------------------------------------------------
# L2 enrichment — dependency chains + cross-doc links
# ---------------------------------------------------------------------------


def _l2_task_chains(session: Session, task: TaskModel) -> list[dict[str, Any]]:
    """Return depends_on / dependents chains for ``related["dependencies"]``."""
    from cod_doc.services import plan_service

    try:
        forward = plan_service.forward_chain(session, task.task_id)
        reverse = plan_service.reverse_chain(session, task.task_id)
    except plan_service.TaskNotFoundInPlanError:
        return []

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
    return deps


def _l2_document_links(session: Session, doc: DocumentModel) -> list[dict[str, Any]]:
    """Return cross-document outgoing links for ``related["documents"]``."""
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
    return docs


# ---------------------------------------------------------------------------
# L3 enrichment — semantic search via ChromaDB (graceful fallback)
# ---------------------------------------------------------------------------


def _semantic_hits(query: str) -> list[SearchHit]:
    """Run a semantic search over the project's ChromaDB index, if configured.

    Returns an empty list when the embedding backend is not available —
    keeps L3 callers safe in offline / placeholder-key setups.
    """
    if not query.strip():
        return []

    try:
        from cod_doc.config import Config
        from cod_doc.core.embeddings import settings_from_config
        from cod_doc.core.reindex import search_documents

        cfg = Config.load()
        # ADO-071: ключ эмбеддера отдельный, поэтому и проверка отдельная —
        # офлайн-бэкендам ключ не нужен, сетевым обязателен.
        settings = settings_from_config(cfg)
        if not settings.is_usable:
            return []  # no embedding backend → graceful skip

        return search_documents(
            query=query,
            chroma_path=cfg.chroma_path,
            settings=settings,
            n_results=_MAX_SEMANTIC_HITS,
        )
    except Exception:
        # Any backend hiccup — log? Not here; ContextService stays read-only & quiet.
        return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _assemble(
    session: Session,
    project_id: int,
    target_kind: str,
    target_id: str,
    depth: str,
    budget: _Budget,
) -> _Packet:
    """Собрать L0/L1-пакет под ``target_kind`` и приложить отложенный L2-материал."""
    # L2 and L3 build on top of L1's body+relations work.
    base_depth = "L0" if depth == "L0" else "L1"
    wants_l2 = depth in {"L2", "L3"}

    if target_kind == "document":
        doc = _resolve_document(session, project_id, target_id)
        if doc is None:
            raise ValueError(f"Document not found: {target_id!r}")
        packet = _build_document_context(session, project_id, doc, base_depth, budget)
        if wants_l2:
            packet.l2_slot = "documents"
            packet.l2_entries = _l2_document_links(session, doc)
        return packet

    if target_kind == "task":
        task = _resolve_task(session, project_id, target_id)
        if task is None:
            raise ValueError(f"Task not found: {target_id!r}")
        packet = _build_task_context(session, project_id, task, base_depth, budget)
        if wants_l2:
            packet.l2_slot = "dependencies"
            packet.l2_entries = _l2_task_chains(session, task)
        return packet

    if target_kind == "plan":
        plan = _resolve_plan(session, project_id, target_id)
        if plan is None:
            raise ValueError(f"Plan not found: {target_id!r}")
        return _build_plan_context(session, project_id, plan, base_depth, budget)

    if target_kind == "module":
        module = _resolve_module(session, project_id, target_id)
        if module is None:
            raise ValueError(f"Module not found: {target_id!r}")
        return _build_module_context(session, project_id, module, base_depth, budget)

    raise ValueError(
        f"Unknown target_kind {target_kind!r}. Expected one of: document, task, plan, module"
    )


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
    token_budget:   Token ceiling, который соблюдается (CUR-014):
                    ``meta.tokens_used <= token_budget`` для всего пакета,
                    кроме ``target_summary`` — тот включается всегда. Текст
                    режется, а L2/L3-материал, не влезший целиком, не
                    добавляется; тогда ``meta.effective_depth`` ниже
                    ``depth``, а ``meta.truncated`` — ``True``.
    master_content: Optional pre-loaded MASTER.md text for master_excerpt.
    """
    if depth not in _VALID_DEPTHS:
        raise ValueError(f"Invalid depth {depth!r}. Expected one of {sorted(_VALID_DEPTHS)}")

    budget = _Budget(token_budget * _CHARS_PER_TOKEN)

    # Master excerpt
    master_excerpt = budget.take(master_content, _MASTER_EXCERPT_CHARS) if master_content else None

    packet = _assemble(session, project_id, target_kind, target_id, depth, budget)

    # L2: цепочки и кросс-ссылки идут в пакет только целиком. Не влезли —
    # пакет честно деградирует до L1, вместо тихого перебора бюджета.
    effective_depth = depth
    if packet.l2_entries is not None:
        if budget.afford(_entries_cost(packet.l2_entries)):
            packet.related[packet.l2_slot] = packet.l2_entries
        else:
            effective_depth = "L1"

    # L3: semantic search on top of L2 — и только если L2 состоялся.
    if depth == "L3":
        packet.related["semantic"] = []
        if effective_depth == depth:
            hits = _semantic_hits(packet.semantic_query)
            if budget.afford(_entries_cost(hits)):
                packet.related["semantic"] = hits
            else:
                effective_depth = "L2"

    if master_excerpt:
        packet.core["master_excerpt"] = master_excerpt

    return {
        "target_summary": packet.target_summary,
        "core": packet.core,
        "related": packet.related,
        "hints": packet.hints,
        "meta": {
            "depth": depth,
            "effective_depth": effective_depth,
            "tokens_used": max(1, budget.used // _CHARS_PER_TOKEN),
            "truncated": budget.truncated,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    }
