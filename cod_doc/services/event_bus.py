"""COD-062 / COD-065: in-process event bus for live UI updates.

Each project gets its own broadcast channel. Producers (task_service,
agent orchestrator, doc_service hooks) call ``publish``; subscribers
(WebSocket connections via the web layer) iterate ``subscribe`` to
forward events to their browser clients.

The bus is intentionally tiny — no persistence, no replay, no fan-in
across projects. It serves the "tell connected browsers what just
happened" use-case; missing an event because the browser was offline
is fine, the client refetches on reconnect.

This module has zero infra/web imports so it stays at the bottom of
the dependency graph.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

_QUEUE_MAXSIZE = 256


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


async def publish(project: str, kind: str, payload: dict[str, Any] | None = None) -> Event:
    """Broadcast an event to every active subscriber of ``project``."""
    event = Event(project=project, kind=kind, payload=payload or {}, ts=time.time())
    async with _lock:
        targets = list(_subscribers.get(project, ()))
    for q in targets:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumer — drop oldest to make room. Better than blocking the
            # producer, since the producer holds DB locks elsewhere.
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
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
    loop.create_task(publish(project, kind, payload))


def emit(project: str, kind: str, **payload: Any) -> None:
    """Convenience wrapper preferred by service callers."""
    publish_sync(project, kind, payload or None)


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
