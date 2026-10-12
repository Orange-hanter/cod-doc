"""Cycle-5 (AGT-003+): agent-centric service composer.

Composes existing CRUD services into self-sufficient payloads for the
6-tool agent surface (``cod_doc.mcp.tools.agent_tools``). The agent
never has to chain task_next_ready + task_checkout + context_get +
skill_get — one call gives it everything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import actor_kind_for_author

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Task


# ----------------------------------------------------------------- #
# Helpers                                                            #
# ----------------------------------------------------------------- #


def _legal_post_checkout_transitions() -> list[str]:
    """Statuses ``in_progress`` may transition to (the post-checkout state)."""
    from cod_doc.services.task_status_machine import ALLOWED_TRANSITIONS

    return sorted(ALLOWED_TRANSITIONS.get("in_progress", set()))


#: Skills that never go into the agent_pick card, even when recommended.
#: ``orchestrator`` is the doc-curator role: its body forbids agent_pick,
#: task_checkout and task_complete — the very calls the card is issued for.
_EXCLUDED_FROM_TASK_CARD = frozenset({"orchestrator"})


def _matching_skills_with_bodies(task_title: str, task_type: str) -> list[dict[str, Any]]:
    """Return applicable skills WITH FULL BODIES (not just descriptions).

    Inlining skill bodies is core to the agent_pick value proposition —
    the agent shouldn't have to call skill_get separately just to read
    the task-standard rules.

    ``orchestrator`` is excluded (RFC 27 F6): its body tells the agent not
    to call agent_pick / task_checkout / task_complete, so a card issued to
    execute a task would forbid executing it.
    """
    from cod_doc.services.skill_service import (
        get_skill_body,
        list_skills,
        recommend_for_tool,
    )

    # task-standard is the base of the coding card: agent_pick lives only
    # on the standard/full profiles, where the agent is a coder (RFC 27 F5/F6).
    matched_names: list[str] = ["task-standard"]

    # Match by tool-name semantics (e.g. task_create → task-standard).
    # We use a synthetic "tool_name" derived from task type to reuse the
    # existing PCA-949 trigger-matching infra without inventing new keywords.
    synthetic = f"task_{task_type}"
    matched_names.extend(recommend_for_tool(synthetic))

    # Deduplicate while preserving order; drop skills barred from the card.
    seen: set[str] = set()
    ordered: list[str] = []
    for name in matched_names:
        if name not in seen and name not in _EXCLUDED_FROM_TASK_CARD:
            seen.add(name)
            ordered.append(name)

    # Limit to top-4 to stay under context budget. Inline bodies.
    by_name = {s["name"]: s for s in list_skills()}
    out: list[dict[str, Any]] = []
    for name in ordered[:4]:
        if name not in by_name:
            continue
        body = get_skill_body(name) or ""
        out.append(
            {
                "name": name,
                "description": by_name[name].get("description", "").strip(),
                "body": body,
            }
        )
    return out


def _parse_acceptance_checklist(acceptance: str | None) -> list[str]:
    """Best-effort: split free-form acceptance text into individual criteria.

    The schema doesn't enforce a checklist shape — agents write whatever.
    We try common separators (✓ markers, bullet dashes, ``; `` joiners,
    or full sentences) and return a flat list. Returns ``[]`` for None.
    """
    if not acceptance:
        return []
    raw = acceptance.strip()
    # Try ✓/✗ markers (skill task-standard recommends this).
    if "✓" in raw:
        items = [chunk.strip(" .") for chunk in raw.split("✓") if chunk.strip(" .")]
        if len(items) >= 2:
            return items
    # Try `; ` joiner.
    if "; " in raw:
        items = [c.strip() for c in raw.split(";") if c.strip()]
        if len(items) >= 2:
            return items
    # Try bullet-style "- ".
    if raw.count("\n- ") >= 1:
        items = [
            line.strip("- ").strip() for line in raw.split("\n") if line.strip().startswith("- ")
        ]
        if items:
            return items
    # Fallback: split by sentence-end "." if multiple sentences exist.
    if raw.count(". ") >= 1:
        items = [
            s.strip() + ("." if not s.endswith(".") else "") for s in raw.split(". ") if s.strip()
        ]
        if len(items) >= 2:
            return items
    return [raw]


# ----------------------------------------------------------------- #
# Core composer: build a task card                                   #
# ----------------------------------------------------------------- #


def _story_full_payload(session: Session, project_id: int, story_id: str) -> dict[str, Any] | None:
    """История проекта с критериями приёмки — для карточки и ``agent_get``.

    Одна форма на оба места; резолв идёт через ``story_service.get``, а не
    ручным запросом. ``status``/``priority`` отдаются строками, как раньше.
    """
    from cod_doc.services import story_service

    story = story_service.get(session, story_id, project_id=project_id)
    if story is None:
        return None
    acceptance = story_service.list_acceptance(session, story_id, project_id=project_id)
    return {
        "story_id": story.story_id,
        "persona": story.persona,
        "narrative": story.narrative,
        "status": story.status.value,
        "priority": story.priority.value,
        "acceptance_criteria": [
            {"position": a.position, "criterion": a.criterion, "met": a.met} for a in acceptance
        ],
    }


def _build_task_card(
    session: Session,
    project_id: int,
    task: Task,
) -> dict[str, Any]:
    """Assemble the agent_pick payload for an already-checked-out task."""
    from cod_doc.infra.models import PlanModel, PlanSectionModel
    from cod_doc.services import activity_service, context_service
    from cod_doc.services.serializers import task_to_dict
    from cod_doc.services.story_lookup import story_id_for_task

    # Build base context via existing L1 context_service.
    ctx = context_service.context_get(
        session=session,
        project_id=project_id,
        target_kind="task",
        target_id=task.task_id,
        depth="L1",
        token_budget=4000,
    )

    # Resolve plan + section names (context_service returns ids only).
    plan_row = (
        session.execute(
            select(PlanModel.scope).where(PlanModel.row_id == task.plan_id)
        ).scalar_one_or_none()
        if task.plan_id
        else None
    )
    section_row = (
        session.execute(
            select(
                PlanSectionModel.letter,
                PlanSectionModel.title,
                PlanSectionModel.position,
            ).where(PlanSectionModel.row_id == task.section_id)
        ).first()
        if task.section_id
        else None
    )

    # Linked story (full): if any story_link → task, fetch story.
    story_id = story_id_for_task(session, project_id=project_id, task_id=task.task_id)
    story_full = (
        _story_full_payload(session, project_id, story_id) if story_id is not None else None
    )

    # Recent activity events for this task scope (last 5).
    history_page = activity_service.list_events(
        session,
        project_id=project_id,
        scope_kind="task",
        scope_id=task.task_id,
        limit=5,
    )
    recent_history_dicts = history_page.get("items", [])

    skills = _matching_skills_with_bodies(task.title, task.type.value)

    # task_to_dict с сессией делает четыре запроса (план и секция, blocked_by,
    # affects_files, story_id); карточке он нужен дважды — для "task" и для
    # "affected_files", поэтому сериализуем один раз.
    task_dict = task_to_dict(task, session=session)
    return {
        "task": task_dict,
        "context": {
            "plan": {
                "scope": plan_row,
                "section_letter": section_row.letter if section_row else None,
                "section_title": section_row.title if section_row else None,
                "section_position": section_row.position if section_row else None,
            },
            "story": story_full,
            "related_docs": ctx.get("related", {}).get("documents", []),
            "siblings": ctx.get("related", {}).get("tasks", []),
            "affected_files": task_dict.get("affects_files", []),
            "recent_history": recent_history_dicts,
        },
        "navigation": {
            "applicable_skills": skills,
            "next_actions": [
                "Read all skill bodies in navigation.applicable_skills first.",
                "Work on the task — make changes, run tests.",
                "On completion: agent_complete(task_id=..., agent_id=..., commit_sha=...).",
                "If stuck: agent_report(task_id=..., kind='blocker', message=...).",
                "If need more context: agent_get(task_id=..., what='full_doc_body', ref=<doc_key>).",
            ],
            "success_criteria": _parse_acceptance_checklist(task.acceptance),
            "legal_status_transitions": _legal_post_checkout_transitions(),
        },
    }


# ----------------------------------------------------------------- #
# Public: pick                                                       #
# ----------------------------------------------------------------- #


def get(
    session: Session,
    *,
    project_id: int,
    task_id: str,
    what: str,
    ref: str | None = None,
) -> dict[str, Any]:
    """AGT-004: opt-in deep fetch when the pick-card didn't include something.

    ``what`` values:
    - ``full_doc_body`` — ``ref`` is doc_key; returns full document body.
    - ``related_task`` — ``ref`` is task_id; returns full task dict.
    - ``story_full`` — ``ref`` is story_id; returns story with all
      acceptance criteria and links.
    - ``plan_export`` — ``ref`` is plan scope; returns markdown export.
    - ``file_content`` — ``ref`` is relative file path; returns text body.

    Returns ``{found: bool, what, ref, payload}`` on hit or
    ``{found: false, what, ref, hint, legal_what: [...]}`` on miss.
    """
    LEGAL = ["full_doc_body", "related_task", "story_full", "plan_export", "file_content"]
    if what not in LEGAL:
        return {
            "found": False,
            "what": what,
            "ref": ref,
            "hint": f"unknown 'what'. Legal values: {LEGAL}",
            "legal_what": LEGAL,
        }

    from cod_doc.infra.repositories import PlanRepository
    from cod_doc.services import doc_service
    from cod_doc.services.serializers import task_to_dict

    if what == "full_doc_body":
        if not ref:
            return {"found": False, "what": what, "ref": ref, "hint": "ref=doc_key required"}
        doc = doc_service.get(session, project_id, ref)
        if doc is None or doc.row_id is None:
            return {"found": False, "what": what, "ref": ref, "hint": f"doc_key not found: {ref}"}
        body = doc_service.render_body(session, doc.row_id) or ""
        return {"found": True, "what": what, "ref": ref, "payload": {"doc_key": ref, "body": body}}

    if what == "related_task":
        from cod_doc.services import task_service

        if not ref:
            return {"found": False, "what": what, "ref": ref, "hint": "ref=task_id required"}
        t = task_service.get(session, ref, project_id=project_id)
        if t is None:
            return {"found": False, "what": what, "ref": ref, "hint": f"task_id not found: {ref}"}
        return {
            "found": True,
            "what": what,
            "ref": ref,
            "payload": task_to_dict(t, session=session),
        }

    if what == "story_full":
        if not ref:
            return {"found": False, "what": what, "ref": ref, "hint": "ref=story_id required"}
        payload = _story_full_payload(session, project_id, ref)
        if payload is None:
            return {"found": False, "what": what, "ref": ref, "hint": f"story_id not found: {ref}"}
        return {"found": True, "what": what, "ref": ref, "payload": payload}

    if what == "plan_export":
        if not ref:
            return {"found": False, "what": what, "ref": ref, "hint": "ref=plan_scope required"}
        plan = PlanRepository(session).get_by_scope(ref, project_id=project_id)
        if plan is None or plan.row_id is None:
            return {
                "found": False,
                "what": what,
                "ref": ref,
                "hint": f"plan_scope not found: {ref}",
            }
        from cod_doc.services.plan_service import reads as plan_reads

        progress = plan_reads.recalc(session, plan.row_id)
        return {
            "found": True,
            "what": what,
            "ref": ref,
            "payload": {
                "scope": progress.scope,
                "total": progress.total,
                "done": progress.done,
                # ADO-078: отменённые вынесены из `remaining` — но не в `done`,
                # а в собственное поле: агент видит, что число уехало, и знает
                # почему.
                "cancelled": progress.cancelled,
                "remaining": progress.remaining,
                "sections": [
                    {
                        "letter": s.letter,
                        "title": s.title,
                        "total": s.total,
                        "done": s.done,
                        "cancelled": s.cancelled,
                    }
                    for s in progress.sections
                ],
            },
        }

    if what == "file_content":
        # File path resolution requires the project entry — not available
        # in the service layer (no FS coupling). MCP wrapper resolves and
        # passes content via different code path.
        return {
            "found": False,
            "what": what,
            "ref": ref,
            "hint": "file_content requires MCP-layer resolution (project root). "
            "Use the standard-profile read_file tool instead, or open via your editor.",
        }
    # unreachable due to LEGAL check
    return {"found": False, "what": what, "ref": ref}


def report(
    session: Session,
    *,
    project_id: int,
    task_id: str,
    kind: str,
    message: str,
    payload: dict[str, Any] | None = None,
    agent_id: str = "agent",
) -> dict[str, Any]:
    """AGT-005: unified dispatcher for non-done outcomes.

    ``kind`` values:
    - ``progress`` — records a progress note (revision + last_updated).
    - ``blocker`` — sets blocked_reason (transitions to blocked).
    - ``approval_request`` — creates an approval row tied to this task.
    - ``needs_context`` — informational; logs an activity event but
      doesn't mutate task state. Useful for "I'm exploring, hold off
      on auto-release".
    """
    LEGAL = ["progress", "blocker", "approval_request", "needs_context"]
    if kind not in LEGAL:
        return {
            "ok": False,
            "kind": kind,
            "hint": f"unknown kind. Legal values: {LEGAL}",
            "legal_kinds": LEGAL,
        }

    from cod_doc.services import activity_service, task_service
    from cod_doc.services.task_service import TaskNotFoundError

    if kind == "progress":
        try:
            task_service.log_progress(
                session,
                project_id=project_id,
                task_id=task_id,
                message=message,
                author=agent_id,
            )
        except TaskNotFoundError:
            return {"ok": False, "kind": kind, "hint": f"task not found: {task_id}"}
        return {
            "ok": True,
            "kind": kind,
            "task_id": task_id,
            "next_actions": ["agent_complete or further agent_report"],
        }

    if kind == "blocker":
        from cod_doc.domain.entities import TaskStatus
        from cod_doc.services.task_status_machine import StatusTransitionError

        # Причина блокировки и переход в blocked — одна операция: если переход
        # не разрешён (из backlog / in_review / done / cancelled), откатываются
        # оба шага, а агент получает подсказку, а не голое исключение.
        try:
            with session.begin_nested():
                task_service.set_blocker(
                    session,
                    task_id=task_id,
                    reason=message,
                    author=agent_id,
                    project_id=project_id,
                )
                t = task_service.update_status(
                    session,
                    task_id=task_id,
                    new_status=TaskStatus("blocked"),
                    author=agent_id,
                    reason=message,
                    project_id=project_id,
                )
        except TaskNotFoundError:
            return {"ok": False, "kind": kind, "hint": f"task not found: {task_id}"}
        except StatusTransitionError as exc:
            return {"ok": False, "kind": kind, "hint": str(exc)}
        activity_service.emit(
            session,
            project_id,
            "task.blocked",
            actor_kind=actor_kind_for_author(agent_id),
            actor_id=agent_id,
            scope_kind="task",
            scope_id=task_id,
            payload={"reason": message},
            summary=f"Task {task_id} blocked: {message[:120]}",
        )
        return {
            "ok": True,
            "kind": kind,
            "task_id": task_id,
            "status": t.status.value,
            "next_actions": ["agent_complete after blocker resolved, or agent_release to give up"],
        }

    if kind == "approval_request":
        from cod_doc.services import approval_service

        # Тип приходит из payload агента: чужое значение — структурный отказ,
        # как у неизвестного kind, а не исключение из approval_service.
        approval_type = (payload or {}).get("approval_type", "manual")
        if not isinstance(approval_type, str) or approval_type not in approval_service.VALID_TYPES:
            legal_types = sorted(approval_service.VALID_TYPES)
            return {
                "ok": False,
                "kind": kind,
                "hint": f"unknown approval_type {approval_type!r}. Legal values: {legal_types}",
                "legal_approval_types": legal_types,
            }
        approval = approval_service.request(
            session,
            project_id,
            approval_type=approval_type,
            requested_by=agent_id,
            payload={"message": message, **(payload or {})},
            linked_task_refs=[task_id],
            linked_doc_revision_ids=None,
            expires_in_hours=48,
        )
        activity_service.emit(
            session,
            project_id,
            "approval.requested",
            actor_kind=actor_kind_for_author(agent_id),
            actor_id=agent_id,
            scope_kind="approval",
            scope_id=approval.approval_id,
            payload={"task_id": task_id},
            summary=f"Approval requested for {task_id}",
        )
        return {
            "ok": True,
            "kind": kind,
            "approval_id": approval.approval_id,
            "next_actions": ["wait — orchestration will resume after approval resolution"],
        }

    if kind == "needs_context":
        activity_service.emit(
            session,
            project_id,
            "agent.needs_context",
            actor_kind=actor_kind_for_author(agent_id),
            actor_id=agent_id,
            scope_kind="task",
            scope_id=task_id,
            payload={"message": message},
            summary=f"Needs context: {message[:120]}",
        )
        return {
            "ok": True,
            "kind": kind,
            "task_id": task_id,
            "next_actions": ["agent_get(what=...) for specific lookup"],
        }
    return {"ok": False, "kind": kind}


def complete(
    session: Session,
    *,
    project_id: int,
    task_id: str,
    agent_id: str,
    commit_sha: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """AGT-006: validate blockers, mark done, release lock in one transaction."""
    from cod_doc.services import activity_service, checkout_service, task_service
    from cod_doc.services.task_service import (
        TaskAlreadyDoneError,
        TaskBlockedError,
        TaskNotFoundError,
    )

    try:
        t = task_service.complete(
            session,
            project_id=project_id,
            task_id=task_id,
            author=agent_id,
            commit_sha=commit_sha,
            reason=summary,
        )
    except TaskNotFoundError:
        return {"ok": False, "hint": f"task not found: {task_id}"}
    except TaskAlreadyDoneError:
        return {"ok": False, "hint": f"task {task_id} is already done"}
    except TaskBlockedError as exc:
        return {"ok": False, "hint": str(exc), "code": "blocked"}

    # Release lock if held by this agent (idempotent if not).
    # F3 (2026-05-15 audit): log if release fails — silent pass made it
    # hard to spot leaking locks (e.g. when admin closes someone else's
    # checked-out task with a different agent_id).
    try:
        checkout_service.release(
            session, task_id, project_id=project_id, agent=agent_id, force=False
        )
    except Exception as exc:
        import logging

        logging.getLogger("cod_doc.agent").warning(
            "agent_complete: lock release failed for %s (agent=%s): %s — "
            "task is done but checkout may have leaked; caller may need "
            "force=True via task_release",
            task_id,
            agent_id,
            exc,
        )

    activity_service.emit(
        session,
        project_id,
        "task.completed",
        actor_kind=actor_kind_for_author(agent_id),
        actor_id=agent_id,
        scope_kind="task",
        scope_id=task_id,
        payload={"commit_sha": commit_sha, "summary": summary},
        summary=f"Task {task_id} completed by {agent_id}",
    )
    return {
        "ok": True,
        "task_id": task_id,
        "status": t.status.value,
        "commit_sha": commit_sha,
        "next_actions": ["agent_pick for the next ready task"],
    }


def release(
    session: Session,
    *,
    project_id: int,
    task_id: str,
    agent_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """AGT-007: release checkout lock without transitioning to done.

    ``in_progress`` откатывается в ``todo`` через ``task_service.update_status``
    — со сверкой по статус-машине, ревизией и событием перехода, как любая
    смена статуса. Отказы — структурным ответом, как у ``pick``.
    """
    from cod_doc.domain.entities import TaskStatus
    from cod_doc.services import activity_service, checkout_service, task_service
    from cod_doc.services.checkout_service import CheckoutConflictError
    from cod_doc.services.task_service import TaskNotFoundError
    from cod_doc.services.task_status_machine import StatusTransitionError, normalise

    try:
        result = checkout_service.release(
            session, task_id, project_id=project_id, agent=agent_id, force=False
        )
        if normalise(result.new_status) == TaskStatus.IN_PROGRESS_NEW.value:
            updated = task_service.update_status(
                session,
                project_id=project_id,
                task_id=task_id,
                new_status=TaskStatus.TODO,
                author=agent_id,
                reason=reason or "agent_release",
            )
            result.new_status = updated.status.value
    except TaskNotFoundError:
        return {"ok": False, "hint": f"task not found: {task_id}"}
    except CheckoutConflictError as exc:
        return {"ok": False, "hint": str(exc), "code": "not_held_by_this_agent"}
    except StatusTransitionError as exc:
        return {"ok": False, "hint": str(exc), "code": "status_transition"}

    activity_service.emit(
        session,
        project_id,
        "task.released",
        actor_kind=actor_kind_for_author(agent_id),
        actor_id=agent_id,
        scope_kind="task",
        scope_id=task_id,
        payload={"reason": reason},
        summary=f"Task {task_id} released by {agent_id}: {reason or 'no reason'}",
    )
    return {
        "ok": True,
        "task_id": task_id,
        "status": result.new_status,
        "next_actions": ["agent_pick for the next ready task"],
    }


def pick(
    session: Session,
    *,
    project_id: int,
    agent_id: str,
    plan_scope: str | None = None,
    local_only: bool = True,
) -> dict[str, Any]:
    """AGT-003: atomic pick + checkout + task-card assembly.

    Behaviours:
    - If ``agent_id`` already holds a checkout (any task in this project),
      return that task's card (idempotent — safe under retries). This
      branch never scans the ready set, so its card carries no
      ``skipped_foreign`` key.
    - Else select highest-priority ready task (optionally filtered by
      ``plan_scope``), atomically checkout under ``agent_id``, assemble
      and return card.
    - ``local_only`` (default True, RFC 27 F13): a task whose
      ``affects_files`` are ALL absolute paths outside the project's
      ``root_path`` is *foreign* and is skipped. Every answer that
      scanned the ready set — ``no_ready_tasks``, ``checkout_failed``
      and the success card — carries ``skipped_foreign``: the count of
      foreign tasks filtered project-wide (before plan_scope/lock
      filters). Pass ``local_only=False`` to make foreign tasks
      pickable.
    - When the ready set is empty:
      ``{"task": None, "reason": "no_ready_tasks", "skipped_foreign": N}``.
    """
    from cod_doc.infra.models import TaskModel
    from cod_doc.infra.repositories import TaskRepository
    from cod_doc.services import checkout_service
    from cod_doc.services.checkout_service import CheckoutConflictError, CheckoutStatusError
    from cod_doc.services.plan_service import reads as plan_reads
    from cod_doc.services.task_service import TaskNotFoundError

    repo = TaskRepository(session)

    # 1. Idempotency: already-held lock on an OPEN task?
    # F2 (2026-05-15 audit): exclude done/cancelled so a stale lock on a
    # closed task doesn't replay as the next pick. Lock survives raw-SQL
    # status flips (which the audit-cycle migration scripts do); without
    # this filter the next agent_pick returns the dead task.
    existing_lock = session.execute(
        select(TaskModel)
        .where(
            TaskModel.project_id == project_id,
            TaskModel.checked_out_by == agent_id,
            TaskModel.status.notin_(("done", "cancelled")),
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing_lock is not None and existing_lock.row_id is not None:
        held = repo.get(existing_lock.row_id)
        if held is not None:
            card = _build_task_card(session, project_id, held)
            card["idempotent_replay"] = True
            return card

    # 2. Find ready set, filter by plan + locks.
    batch = plan_reads.ready_batch_for_project(session, project_id, local_only=local_only)
    ready = batch.tasks
    if plan_scope is not None:
        from cod_doc.infra.repositories import PlanRepository

        plan = PlanRepository(session).get_by_scope(plan_scope, project_id=project_id)
        plan_id = plan.row_id if plan is not None else -1
        ready = [t for t in ready if getattr(t, "plan_id", None) == plan_id]
    if ready:
        ready_ids = [t.row_id for t in ready if t.row_id is not None]
        locked = set(
            session.execute(
                select(TaskModel.row_id).where(
                    TaskModel.row_id.in_(ready_ids),
                    TaskModel.checked_out_by.isnot(None),
                )
            ).scalars()
        )
        ready = [t for t in ready if t.row_id not in locked]

    if not ready:
        return {
            "task": None,
            "reason": "no_ready_tasks",
            "skipped_foreign": batch.skipped_foreign,
        }

    target = ready[0]
    assert target.task_id is not None

    # 3. Atomic checkout under agent_id.
    try:
        checkout_service.checkout(
            session,
            target.task_id,
            project_id=project_id,
            agent=agent_id,
        )
    except (CheckoutConflictError, CheckoutStatusError, TaskNotFoundError) as exc:
        # Гонка за замок, дрейф статуса, задачу удалили между выборкой и
        # чекаутом — тексты доменные. Ошибки драйвера не перехватываются:
        # str() от них несёт SQL и параметры, наружу им незачем.
        return {
            "task": None,
            "reason": "checkout_failed",
            "detail": str(exc),
            "skipped_foreign": batch.skipped_foreign,
        }

    # Refresh task after checkout (status updated to in_progress).
    refreshed = repo.get(target.row_id) if target.row_id is not None else target
    if refreshed is None:
        # Row vanished between checkout and refresh (concurrent delete) — the
        # type fix in STB-022 surfaced this previously-unhandled edge.
        return {
            "task": None,
            "reason": "task_disappeared",
            "detail": target.task_id,
        }

    # 4. Assemble card.
    card = _build_task_card(session, project_id, refreshed)
    card["skipped_foreign"] = batch.skipped_foreign
    return card
