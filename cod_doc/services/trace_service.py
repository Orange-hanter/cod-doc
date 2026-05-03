"""COD-063: trace ledger — record + read LLM calls per task.

The agent (and other AI surfaces) writes one row per LLM round-trip; the
task-detail "Trace" tab reads them back. Writes are best-effort: a failed
trace insert must never break the actual agent loop, so callers that wrap
this in a try/except can swallow the error safely.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import TraceCall
from cod_doc.infra.repositories import TraceCallRepository

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.orm import Session


def record(
    session: Session,
    *,
    model: str,
    task_id: int | None = None,
    kind: str = "chat",
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    tool_calls: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> TraceCall:
    """Persist one LLM-call trace row. Caller commits the transaction."""
    return TraceCallRepository(session).add(
        TraceCall(
            model=model,
            task_id=task_id,
            kind=kind,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            tool_calls=tool_calls,
            error=error,
        )
    )


def list_for_task(session: Session, task_row_id: int) -> Sequence[TraceCall]:
    """Return all trace rows for a task — newest-first."""
    return TraceCallRepository(session).list_for_task(task_row_id)


@dataclass
class TraceCollector:
    """Mutable bag the timing context fills in. Caller reads after exit."""

    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None


@contextmanager
def timed_call(collector: TraceCollector) -> Iterator[TraceCollector]:
    """Time a block and stash the elapsed ms in ``collector``.

    Pattern::

        c = TraceCollector()
        with trace_service.timed_call(c):
            response = client.chat.completions.create(...)
            c.input_tokens = response.usage.prompt_tokens
            c.output_tokens = response.usage.completion_tokens
        trace_service.record(session, model=..., task_id=..., **dataclasses.asdict(c))
    """
    start = time.monotonic()
    try:
        yield collector
    except Exception as exc:
        collector.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        collector.duration_ms = int((time.monotonic() - start) * 1000)
