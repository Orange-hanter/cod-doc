"""PCA-221: TaskStatus state machine — transitions and normalisation."""

from __future__ import annotations

import pytest

from cod_doc.services.task_status_machine import (
    ALLOWED_TRANSITIONS,
    StatusTransitionError,
    is_active,
    is_terminal,
    normalise,
    validate_transition,
)


class TestNormalise:
    def test_legacy_pending_maps_to_todo(self) -> None:
        assert normalise("pending") == "todo"

    def test_legacy_hyphen_maps_to_underscore(self) -> None:
        assert normalise("in-progress") == "in_progress"

    def test_canonical_passthrough(self) -> None:
        for s in ("backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"):
            assert normalise(s) == s

    def test_accepts_enum(self) -> None:
        from cod_doc.domain.entities import TaskStatus
        assert normalise(TaskStatus.PENDING) == "todo"
        assert normalise(TaskStatus.IN_PROGRESS) == "in_progress"


class TestValidateTransition:
    def test_no_op_self_transition_allowed(self) -> None:
        validate_transition("done", "done")
        validate_transition("pending", "todo")  # both normalise to "todo"

    def test_legacy_pending_to_in_progress_via_checkout_ok(self) -> None:
        validate_transition("pending", "in-progress", via_checkout=True)

    def test_canonical_todo_to_in_progress_warn_mode_ok(self) -> None:
        # Default: enforce_checkout=False — checkout-required transition allowed.
        validate_transition("todo", "in_progress")

    def test_canonical_todo_to_in_progress_strict_requires_checkout(self) -> None:
        with pytest.raises(StatusTransitionError, match="task_checkout"):
            validate_transition("todo", "in_progress", enforce_checkout=True)

    def test_in_progress_to_done_allowed(self) -> None:
        validate_transition("in-progress", "done")

    def test_done_to_in_progress_reopen_allowed(self) -> None:
        validate_transition("done", "in_progress")

    def test_cancelled_to_done_rejected(self) -> None:
        with pytest.raises(StatusTransitionError, match="only these are allowed"):
            validate_transition("cancelled", "done")

    def test_unknown_source_rejected(self) -> None:
        with pytest.raises(StatusTransitionError, match="unknown source status"):
            validate_transition("garbage", "todo")

    def test_in_progress_to_todo_rollback_allowed(self) -> None:
        validate_transition("in-progress", "pending")  # in_progress → todo per PCA-221


class TestTransitionTableShape:
    def test_table_covers_all_canonical_statuses(self) -> None:
        canonical = {"backlog", "todo", "in_progress", "in_review",
                     "blocked", "done", "cancelled"}
        assert set(ALLOWED_TRANSITIONS.keys()) == canonical

    def test_no_transition_to_unknown_target(self) -> None:
        canonical = {"backlog", "todo", "in_progress", "in_review",
                     "blocked", "done", "cancelled"}
        for sources, targets in ALLOWED_TRANSITIONS.items():
            assert targets <= canonical


class TestPredicates:
    def test_terminal_states(self) -> None:
        assert is_terminal("done") is True
        assert is_terminal("cancelled") is True
        assert is_terminal("pending") is False  # legacy maps to todo

    def test_active_states(self) -> None:
        for s in ("todo", "in_progress", "in_review"):
            assert is_active(s) is True
        for s in ("backlog", "blocked", "done", "cancelled"):
            assert is_active(s) is False
