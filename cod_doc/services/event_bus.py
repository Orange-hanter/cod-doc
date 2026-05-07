"""COD-062 / COD-065: in-process event bus for live UI updates.

Each project gets its own broadcast channel. Producers (task_service,
agent orchestrator, doc_service hooks) call ``publish``; subscribers
(WebSocket connections via the web layer) iterate ``subscribe`` to
forward events to their browser clients.

The bus is intentionally tiny — no persistence, no replay, no fan-in
across projects. It serves the "tell connected browsers what just
happened" use-case; missing an event because the browser was offline
is fine, the client refetches on reconnect.

COD-072: callers that emit inside an open SQLAlchemy transaction should
prefer ``queue_emit(session, …)`` over ``emit(…)`` so events are deferred
until the transaction actually commits — otherwise a rollback would leave
browsers showing state that never landed in the DB.

This module has zero infra/web imports beyond the SQLAlchemy after_commit /
after_rollback hooks for the queue helper.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

_QUEUE_MAXSIZE = 256
_PENDING_KEY = "_cod_doc_pending_events"


@dataclass(slots=True)
class Event:
    """One broadcast item.

    ``project`` scopes routing; ``kind`` is the discriminator the client
    branches on (e.g. ``task.status_changed``); ``payload`` is JSON-safe.
    ``ts`` is filled in by ``publish`` so the wire format is stable.
    """

    project: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0

    def to_wire(self) -> dict[str, Any]:
        return {"project": self.project, "kind": self.kind, "payload": self.payload, "ts": self.ts}


_subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)
_lock = asyncio.Lock()
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def publish(project: str, kind: str, payload: dict[str, Any] | None = None) -> Event:
    """Broadcast an event to every active subscriber of ``project``.

    COD-073: dispatch happens under ``_lock`` so a subscriber that just
    exited cannot receive an event into its (now orphaned) queue. ``put_nowait``
    is non-blocking, so holding the lock for the duration of the loop is
    O(subscribers) of microseconds — fine for in-process pub/sub.
    """
    event = Event(project=project, kind=kind, payload=payload or {}, ts=time.time())
    async with _lock:
        targets = tuple(_subscribers.get(project, ()))
        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop oldest to make room. Better than blocking
                # the producer, since the producer holds DB locks elsewhere.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(event)
    return event


def publish_sync(project: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    """Best-effort sync emit — used from sync FastAPI handlers and services.

    Schedules the publish on the running asyncio loop; silently no-ops if
    no loop is active (e.g. CLI / tests with no async context).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(publish(project, kind, payload))
    # Hold a reference until the task finishes — otherwise the GC may
    # collect the only ref and the publish silently drops (RUF006).
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def emit(project: str, kind: str, **payload: Any) -> None:
    """Convenience wrapper preferred by service callers."""
    publish_sync(project, kind, payload or None)


def queue_emit(session: Session, project: str, kind: str, **payload: Any) -> None:
    """Defer ``emit`` until ``session`` commits successfully.

    Events accumulate on ``session.info[_PENDING_KEY]`` and are flushed by
    the after-commit listener below. On rollback they are discarded.

    Use this from any service-layer write path so browsers never see state
    that didn't actually persist.
    """
    pending: list[tuple[str, str, dict[str, Any]]] = session.info.setdefault(
        _PENDING_KEY, []
    )
    pending.append((project, kind, dict(payload)))


@event.listens_for(Session, "after_commit")
def _flush_pending_events(session: Session) -> None:  # pragma: no cover via integration
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    for project, kind, payload in pending:
        publish_sync(project, kind, payload or None)


@event.listens_for(Session, "after_rollback")
def _drop_pending_events(session: Session) -> None:  # pragma: no cover via integration
    session.info.pop(_PENDING_KEY, None)


class Subscription:
    """Async iterator over events for a single project.

    Use::

        async with subscribe("demo") as sub:
            async for event in sub:
                ...
    """

    def __init__(self, project: str) -> None:
        self.project = project
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

    async def __aenter__(self) -> Subscription:
        async with _lock:
            _subscribers[self.project].add(self._queue)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        async with _lock:
            _subscribers[self.project].discard(self._queue)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        return await self._queue.get()


def subscribe(project: str) -> Subscription:
    """Return an async-context-manager Subscription for the given project."""
    return Subscription(project)


def active_subscribers(project: str) -> int:
    """Return the count of live subscribers — used by tests + diagnostics."""
    return len(_subscribers.get(project, ()))
