"""TaskStatus state machine — transitions and normalisation (PCA-221).

Closes proposal 08. The 7-state taxonomy is a superset of the legacy
3-state one: existing PENDING/IN_PROGRESS/DONE rows continue to work
because :func:`normalise` collapses each pair to a canonical bucket
before the transition table is consulted.

Canonical buckets
-----------------
- ``backlog``     — parked, not on the active heartbeat
- ``todo``        ≡ legacy ``pending`` — ready to work, not picked up
- ``in_progress`` ≡ legacy ``in-progress`` — owned by a worker via checkout
- ``in_review``   — explicit waiting posture (approval / human review)
- ``blocked``     — cannot move until another task / external state changes
- ``done``        — closed
- ``cancelled``   — intentionally abandoned, will not resume

Transitions are defined in :data:`ALLOWED_TRANSITIONS`. Calls into the
state machine pass the *raw* status string (legacy or new); :func:`normalise`
maps it to the canonical bucket so callers don't need to care.
"""

from __future__ import annotations

from cod_doc.domain.entities import (
    TASK_STATUS_ALIASES,
    TaskStatus,
    canonical_task_status,
)


class StatusTransitionError(ValueError):
    """Raised when an attempted transition is not allowed."""

    def __init__(self, *, from_status: str, to_status: str, reason: str) -> None:
        super().__init__(f"Invalid TaskStatus transition {from_status!r} → {to_status!r}: {reason}")
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason


# --------------------------------------------------------------------------- #
# Normalisation                                                                #
# --------------------------------------------------------------------------- #

# ADO-182: сама карта переехала в `domain.entities` — её читает и `infra`
# (репозиторий фильтрует по статусу), а импорт `services` из `infra` запрещён
# слоями. Здесь — ре-экспорт под прежним именем: `mcp/tools/agent_tools.py` и
# `mcp/tools/context_tools.py` кладут её в payload как
# `task_status_legacy_aliases`, и этот контракт меняться не должен.
_LEGACY_ALIASES: dict[str, str] = TASK_STATUS_ALIASES


def normalise(status: str | TaskStatus) -> str:
    """Collapse legacy or canonical status to the canonical bucket name."""
    return canonical_task_status(status)


# --------------------------------------------------------------------------- #
# Transition table                                                             #
# --------------------------------------------------------------------------- #

# Per proposal 08 §51. Keys + values are *canonical* (post-normalise) buckets.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "backlog": frozenset({"todo", "cancelled"}),
    "todo": frozenset({"in_progress", "blocked", "backlog", "cancelled"}),
    # `in_progress → todo` is a slight deviation from proposal 08 §51 to
    # support "rollback without completing" — useful when an exploratory
    # checkout turns out to be wrong and the agent wants to put the task
    # back without marking it cancelled. Releasing the lock alone (via
    # `task_release`) does not change status, so this stays orthogonal.
    "in_progress": frozenset({"todo", "in_review", "blocked", "done", "cancelled"}),
    "in_review": frozenset({"in_progress", "done", "cancelled"}),
    "blocked": frozenset({"todo", "in_progress", "cancelled"}),
    "done": frozenset({"todo", "in_progress"}),  # reopen
    "cancelled": frozenset({"todo"}),  # reopen
}

# Transitions that, per proposal 08 §51 + proposal 06, REQUIRE a checkout.
# Currently: only `todo → in_progress` (must go through `task_checkout`).
TRANSITIONS_REQUIRING_CHECKOUT: frozenset[tuple[str, str]] = frozenset(
    {
        ("todo", "in_progress"),
    }
)


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def validate_transition(
    from_status: str | TaskStatus,
    to_status: str | TaskStatus,
    *,
    via_checkout: bool = False,
    enforce_checkout: bool = False,
) -> None:
    """Raise :class:`StatusTransitionError` when the transition isn't allowed.

    ``via_checkout`` should be True when the transition is being driven by
    ``task_checkout`` — required for ``todo → in_progress`` per proposal 06,
    *iff* ``enforce_checkout=True``. Defaults to False (warn-mode per
    proposal 06 §89: Phase 1 logs but does not block; Phase 2 enforces).
    """
    f = normalise(from_status)
    t = normalise(to_status)

    if f == t:
        # Re-asserting the same status is always a no-op, never an error.
        return

    allowed = ALLOWED_TRANSITIONS.get(f)
    if allowed is None:
        raise StatusTransitionError(
            from_status=str(from_status),
            to_status=str(to_status),
            reason=f"unknown source status {f!r}",
        )
    if t not in allowed:
        raise StatusTransitionError(
            from_status=str(from_status),
            to_status=str(to_status),
            reason=f"only these are allowed from {f!r}: {sorted(allowed)}",
        )

    if enforce_checkout and (f, t) in TRANSITIONS_REQUIRING_CHECKOUT and not via_checkout:
        raise StatusTransitionError(
            from_status=str(from_status),
            to_status=str(to_status),
            reason="must go through task_checkout (proposal 06)",
        )


#: Канонические статусы, означающие «задача закрыта».
#:
#: ADO-078: до этого «закрыто» было определено в дереве дважды и по-разному —
#: :func:`is_terminal` считал закрытыми ``done`` и ``cancelled``, а
#: ``ready_tasks``, :func:`task_service.complete` и
#: :func:`task_service.list_blocked` — только ``done``. Расхождение стоило
#: дорого: зависимость, упирающаяся в отменённый блокер, не разблокировалась
#: никогда, и закрыть такую задачу было нельзя вовсе. Здесь единственная точка
#: вывода для Python-слоя; SQL-вьюхи повторяют тот же набор литералами
#: (миграция — застывший снимок), синхронность стережёт
#: ``tests/infra/test_ready_tasks_cancelled.py``.
TERMINAL_STATUSES: frozenset[str] = frozenset({TaskStatus.DONE.value, TaskStatus.CANCELLED.value})


def is_terminal(status: str | TaskStatus) -> bool:
    """``done`` and ``cancelled`` are terminal — the task is no longer active."""
    return normalise(status) in TERMINAL_STATUSES


def is_active(status: str | TaskStatus) -> bool:
    """Active = task is being or about to be worked on (todo, in_progress, in_review)."""
    return normalise(status) in ("todo", "in_progress", "in_review")
