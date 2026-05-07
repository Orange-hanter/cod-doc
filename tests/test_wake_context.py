"""PCA-020: WakeContext dataclass + WakeReason enum + size-cap validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cod_doc.agent.wake_context import (
    PAYLOAD_BUDGET_BYTES,
    WakeContext,
    WakePayloadTooLargeError,
    WakeReason,
)

# --------------------------------------------------------------------------- #
# Constructor & shape                                                          #
# --------------------------------------------------------------------------- #


def test_cold_start_minimal_constructor() -> None:
    wc = WakeContext(reason=WakeReason.COLD_START)
    assert wc.reason is WakeReason.COLD_START
    assert wc.task_id is None
    assert wc.payload == {}
    assert wc.skills_to_preload == []
    assert isinstance(wc.assembled_at, datetime)
    assert wc.assembled_at.tzinfo is UTC


def test_task_assigned_requires_task_id() -> None:
    with pytest.raises(ValueError, match="requires non-empty task_id"):
        WakeContext(reason=WakeReason.TASK_ASSIGNED)


def test_approval_resolved_requires_task_id() -> None:
    with pytest.raises(ValueError, match="requires non-empty task_id"):
        WakeContext(reason=WakeReason.APPROVAL_RESOLVED)


def test_doc_drift_requires_triggering_doc_ref() -> None:
    with pytest.raises(ValueError, match="triggering_doc_ref"):
        WakeContext(reason=WakeReason.DOC_DRIFT)


def test_manual_does_not_require_task_or_doc() -> None:
    wc = WakeContext(reason=WakeReason.MANUAL)
    assert wc.reason is WakeReason.MANUAL


def test_reason_must_be_wake_reason_enum() -> None:
    with pytest.raises(TypeError, match="reason must be WakeReason"):
        WakeContext(reason="task_assigned")  # type: ignore[arg-type]


def test_payload_must_be_dict() -> None:
    with pytest.raises(TypeError, match="payload must be a dict"):
        WakeContext(reason=WakeReason.COLD_START, payload=["not a dict"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Size cap                                                                     #
# --------------------------------------------------------------------------- #


def test_small_payload_passes() -> None:
    wc = WakeContext(reason=WakeReason.COLD_START, payload={"k": "v" * 100})
    assert wc.payload_size_bytes() < PAYLOAD_BUDGET_BYTES


def test_payload_at_budget_passes() -> None:
    """Граница ≤ budget — допустима."""
    # `{"k": "..."}` — 9 chars overhead with default separators (', ', ': ').
    wc = WakeContext(
        reason=WakeReason.COLD_START,
        payload={"k": "x" * (PAYLOAD_BUDGET_BYTES - 9)},
    )
    assert wc.payload_size_bytes() == PAYLOAD_BUDGET_BYTES


def test_payload_exceeding_budget_raises() -> None:
    with pytest.raises(WakePayloadTooLargeError) as exc:
        WakeContext(
            reason=WakeReason.COLD_START,
            payload={"k": "x" * (PAYLOAD_BUDGET_BYTES + 100)},
        )
    assert exc.value.budget_bytes == PAYLOAD_BUDGET_BYTES
    assert exc.value.size_bytes > PAYLOAD_BUDGET_BYTES


# --------------------------------------------------------------------------- #
# is_scoped + to_message_block                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reason,expected_scoped",
    [
        (WakeReason.COLD_START, False),
        (WakeReason.TASK_ASSIGNED, True),
        (WakeReason.DOC_DRIFT, True),
        (WakeReason.APPROVAL_RESOLVED, True),
        (WakeReason.MANUAL, False),
    ],
)
def test_is_scoped_per_reason(reason: WakeReason, expected_scoped: bool) -> None:
    if reason is WakeReason.TASK_ASSIGNED or reason is WakeReason.APPROVAL_RESOLVED:
        wc = WakeContext(reason=reason, task_id="HB-001")
    elif reason is WakeReason.DOC_DRIFT:
        wc = WakeContext(reason=reason, triggering_doc_ref="doc:foo")
    else:
        wc = WakeContext(reason=reason)
    assert wc.is_scoped is expected_scoped


def test_message_block_includes_reason_and_no_master_hint_for_scoped() -> None:
    wc = WakeContext(
        reason=WakeReason.TASK_ASSIGNED,
        task_id="HB-001",
        triggering_revision_id="01J0000",
        skills_to_preload=["validation"],
    )
    msg = wc.to_message_block()
    assert msg.startswith("WAKE PAYLOAD")
    assert "reason: task_assigned" in msg
    assert "task_id: HB-001" in msg
    assert "since_revision_id: 01J0000" in msg
    assert "skills: validation" in msg
    assert "do NOT call get_master" in msg


def test_message_block_no_scope_hint_for_cold_start() -> None:
    wc = WakeContext(reason=WakeReason.COLD_START)
    msg = wc.to_message_block()
    assert "do NOT call get_master" not in msg
