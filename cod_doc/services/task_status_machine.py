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

from cod_doc.domain.entities import TaskStatus


class StatusTransitionError(ValueError):
    """Raised when an attempted transition is not allowed."""

    def __init__(self, *, from_status: str, to_status: str, reason: str) -> None:
        super().__init__(
            f"Invalid TaskStatus transition {from_status!r} → {to_status!r}: {reason}"
        )
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason


# --------------------------------------------------------------------------- #
# Normalisation                                                                #
# --------------------------------------------------------------------------- #

# Map raw status strings (legacy + new) to canonical buckets.
_LEGACY_ALIASES: dict[str, str] = {
    "pending": "todo",
    "in-progress": "in_progress",
    # "done" and the rest of the new names already match canonical form.
}


def normalise(status: str | TaskStatus) -> str:
    """Collapse legacy or canonical status to the canonical bucket name."""
    raw = status.value if isinstance(status, TaskStatus) else str(status)
    return _LEGACY_ALIASES.get(raw, raw)


# --------------------------------------------------------------------------- #
# Transition table                                                             #
# --------------------------------------------------------------------------- #

# Per proposal 08 §51. Keys + values are *canonical* (post-normalise) buckets.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "backlog":     frozenset({"todo", "cancelled"}),
    "todo":        frozenset({"in_progress", "blocked", "backlog", "cancelled"}),
    # `in_progress → todo` is a slight deviation from proposal 08 §51 to
    # support "rollback without completing" — useful when an exploratory
    # checkout turns out to be wrong and the agent wants to put the task
    # back without marking it cancelled. Releasing the lock alone (via
    # `task_release`) does not change status, so this stays orthogonal.
    "in_progress": frozenset({"todo", "in_review", "blocked", "done", "cancelled"}),
    "in_review":   frozenset({"in_progress", "done", "cancelled"}),
    "blocked":     frozenset({"todo", "in_progress", "cancelled"}),
    "done":        frozenset({"todo", "in_progress"}),  # reopen
    "cancelled":   frozenset({"todo"}),                  # reopen
}

# Transitions that, per proposal 08 §51 + proposal 06, REQUIRE a checkout.
# Currently: only `todo → in_progress` (must go through `task_checkout`).
TRANSITIONS_REQUIRING_CHECKOUT: frozenset[tuple[str, str]] = frozenset({
    ("todo", "in_progress"),
})


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

    if (
        enforce_checkout
        and (f, t) in TRANSITIONS_REQUIRING_CHECKOUT
        and not via_checkout
    ):
        raise StatusTransitionError(
            from_status=str(from_status),
            to_status=str(to_status),
            reason="must go through task_checkout (proposal 06)",
        )


def is_terminal(status: str | TaskStatus) -> bool:
    """``done`` and ``cancelled`` are terminal — the task is no longer active."""
    return normalise(status) in ("done", "cancelled")


def is_active(status: str | TaskStatus) -> bool:
    """Active = task is being or about to be worked on (todo, in_progress, in_review)."""
    return normalise(status) in ("todo", "in_progress", "in_review")
