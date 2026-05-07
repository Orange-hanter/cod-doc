"""WakeContext — структурированный wake-payload для оркестратора (PCA-020).

Closes proposal 03 dataclass piece: ``WakeContext`` собирается до первого
LLM-вызова и инжектится как первое user-message блоком ``WAKE PAYLOAD``.

PCA-020 покрывает только модель данных + size-cap валидацию. Builder
``build_wake_context()`` (PCA-021) и интеграция в ``Orchestrator.run``
(PCA-022) — отдельные задачи.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

PAYLOAD_BUDGET_BYTES = 4096
"""Hard limit on the JSON-encoded ``WakeContext.payload``.

Keeps the wake-message bounded so the agent always has headroom in its
context window. Payload >4 KB → :class:`WakePayloadTooLargeError` —
caller should pull data on demand instead.
"""


class WakeReason(StrEnum):
    """Why the orchestrator was woken up.

    Ordering reflects scoped-vs-cold-start spectrum: ``cold_start`` is the
    most expensive (full ``get_master`` path), the others are scoped and
    skip ``get_master`` per orchestrator/SKILL.md.
    """

    COLD_START = "cold_start"
    TASK_ASSIGNED = "task_assigned"
    DOC_DRIFT = "doc_drift"
    APPROVAL_RESOLVED = "approval_resolved"
    MANUAL = "manual"


class WakePayloadTooLargeError(ValueError):
    """Raised when ``WakeContext.payload`` exceeds :data:`PAYLOAD_BUDGET_BYTES`."""

    def __init__(self, *, size_bytes: int, budget_bytes: int) -> None:
        super().__init__(
            f"WakeContext.payload size {size_bytes} bytes exceeds "
            f"budget {budget_bytes}; pull data on demand instead of inlining"
        )
        self.size_bytes = size_bytes
        self.budget_bytes = budget_bytes


@dataclass(slots=True)
class WakeContext:
    """Структурированный wake-payload, собирается до первого LLM-вызова.

    Поля
    -----
    reason
        Что разбудило оркестратор. Драйверит scoped-vs-cold-start решение
        в ``Orchestrator.run`` (PCA-022).
    task_id
        Если wake привязан к конкретной задаче — её ID. ``None`` для
        cold_start / doc_drift без зависящих задач.
    triggering_doc_ref
        Если триггер — drift документа: его hybrid-ref или doc_key.
    triggering_revision_id
        Cursor для инкрементального чтения через
        ``task.heartbeat_context(since_revision_id=...)``.
    payload
        Готовый компактный JSON (типично — результат
        ``heartbeat_context``). Размер ≤ :data:`PAYLOAD_BUDGET_BYTES`.
    skills_to_preload
        Список имён скиллов из триггер-матчера (PCA-002). Передаётся в
        select_skills() как hint.
    assembled_at
        Когда payload собран — для проверки stale-ности на стороне агента.
    """

    reason: WakeReason
    task_id: str | None = None
    triggering_doc_ref: str | None = None
    triggering_revision_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    skills_to_preload: list[str] = field(default_factory=list)
    assembled_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.validate()

    # ---------------------------------------------------------------- #

    def validate(self) -> None:
        """Enforce shape + payload size invariants. Called from __post_init__."""
        if not isinstance(self.reason, WakeReason):
            raise TypeError(
                f"reason must be WakeReason, got {type(self.reason).__name__}"
            )
        if self.task_id is not None and not isinstance(self.task_id, str):
            raise TypeError("task_id must be str | None")
        if self.triggering_doc_ref is not None and not isinstance(
            self.triggering_doc_ref, str
        ):
            raise TypeError("triggering_doc_ref must be str | None")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dict")
        if not isinstance(self.skills_to_preload, list):
            raise TypeError("skills_to_preload must be a list")

        size = self.payload_size_bytes()
        if size > PAYLOAD_BUDGET_BYTES:
            raise WakePayloadTooLargeError(
                size_bytes=size, budget_bytes=PAYLOAD_BUDGET_BYTES
            )

        if (
            self.reason in (WakeReason.TASK_ASSIGNED, WakeReason.APPROVAL_RESOLVED)
            and not self.task_id
        ):
            raise ValueError(
                f"reason={self.reason.value} requires non-empty task_id"
            )
        if self.reason is WakeReason.DOC_DRIFT and not self.triggering_doc_ref:
            raise ValueError("reason=doc_drift requires triggering_doc_ref")

    def payload_size_bytes(self) -> int:
        """Return the JSON-encoded UTF-8 byte size of ``payload``."""
        return len(json.dumps(self.payload, ensure_ascii=False).encode("utf-8"))

    @property
    def is_scoped(self) -> bool:
        """True for reasons that allow skipping ``get_master`` on first round-trip."""
        return self.reason in (
            WakeReason.TASK_ASSIGNED,
            WakeReason.DOC_DRIFT,
            WakeReason.APPROVAL_RESOLVED,
        )

    def to_message_block(self) -> str:
        """Render a human-readable WAKE PAYLOAD block for LLM injection (PCA-022 use)."""
        lines = [
            "WAKE PAYLOAD",
            f"reason: {self.reason.value}",
        ]
        if self.task_id:
            lines.append(f"task_id: {self.task_id}")
        if self.triggering_doc_ref:
            lines.append(f"triggering_doc: {self.triggering_doc_ref}")
        if self.triggering_revision_id:
            lines.append(f"since_revision_id: {self.triggering_revision_id}")
        if self.skills_to_preload:
            lines.append(f"skills: {', '.join(self.skills_to_preload)}")
        lines.append(f"assembled_at: {self.assembled_at.isoformat()}")
        if self.payload:
            lines.append("")
            lines.append("payload:")
            lines.append(json.dumps(self.payload, ensure_ascii=False, indent=2))
        if self.is_scoped:
            lines.append("")
            lines.append("Acknowledge this wake first; do NOT call get_master.")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Builder (PCA-021)                                                            #
# --------------------------------------------------------------------------- #


def _trim_payload_to_budget(payload: dict[str, Any]) -> dict[str, Any]:
    """If payload exceeds budget, drop the most variable fields until it fits.

    Strategy: heartbeat_context payload has fixed-shape skeleton + variable
    ``recent_changes``. Drop ``recent_changes`` first, then ``ancestry``
    if still over. Keeps the essential ``task`` block.
    """
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= PAYLOAD_BUDGET_BYTES:
        return payload

    trimmed = dict(payload)
    if "recent_changes" in trimmed:
        trimmed["recent_changes"] = []
    encoded = json.dumps(trimmed, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= PAYLOAD_BUDGET_BYTES:
        return trimmed

    if "linked_docs_summary" in trimmed:
        trimmed["linked_docs_summary"] = []
    encoded = json.dumps(trimmed, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= PAYLOAD_BUDGET_BYTES:
        return trimmed

    return {"task": trimmed.get("task", {}), "_trimmed": True}


def build_wake_context(
    session: Session,
    *,
    reason: WakeReason,
    task_id: str | None = None,
    triggering_doc_ref: str | None = None,
    triggering_revision_id: str | None = None,
    skills_to_preload: list[str] | None = None,
) -> WakeContext:
    """Assemble a :class:`WakeContext` for the orchestrator.

    Composition rules per :class:`WakeReason`:

    - ``cold_start`` / ``manual`` — empty payload, ``skills_to_preload``
      defaults to ``["orchestrator"]`` if caller doesn't pass one. Cold
      start is the only path that lets the orchestrator call ``get_master``.
    - ``task_assigned`` / ``approval_resolved`` — requires ``task_id``.
      Builds the payload via :func:`heartbeat_service.heartbeat_context`
      with the optional cursor; trimmed to fit ``PAYLOAD_BUDGET_BYTES``.
    - ``doc_drift`` — requires ``triggering_doc_ref``. Payload contains a
      compact doc snippet and the trigger metadata; the agent will pull
      the full doc on demand if it actually needs it.

    Unknown ``task_id`` propagates :class:`task_service.TaskNotFoundError`.
    Payload exceeding budget after trimming raises
    :class:`WakePayloadTooLargeError` (let it propagate — the operator
    needs to widen scope manually).
    """
    skills_to_preload = list(skills_to_preload) if skills_to_preload else ["orchestrator"]
    payload: dict[str, Any] = {}

    if reason in (WakeReason.TASK_ASSIGNED, WakeReason.APPROVAL_RESOLVED):
        if not task_id:
            raise ValueError(f"reason={reason.value} requires task_id")
        from cod_doc.services import heartbeat_service

        payload = heartbeat_service.heartbeat_context(
            session,
            task_id=task_id,
            since_revision_id=triggering_revision_id,
        )
        payload = _trim_payload_to_budget(payload)
    elif reason is WakeReason.DOC_DRIFT:
        if not triggering_doc_ref:
            raise ValueError("reason=doc_drift requires triggering_doc_ref")
        payload = {
            "doc_ref": triggering_doc_ref,
            "since_revision_id": triggering_revision_id,
        }
    # cold_start / manual → payload stays empty.

    return WakeContext(
        reason=reason,
        task_id=task_id,
        triggering_doc_ref=triggering_doc_ref,
        triggering_revision_id=triggering_revision_id,
        payload=payload,
        skills_to_preload=skills_to_preload,
    )
