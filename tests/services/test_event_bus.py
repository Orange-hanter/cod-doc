"""COD-062 / COD-065: event_bus pub/sub semantics."""

from __future__ import annotations

import asyncio

import pytest

from cod_doc.services import event_bus


pytestmark = pytest.mark.asyncio


async def test_subscriber_receives_published_event() -> None:
    async with event_bus.subscribe("demo") as sub:
        await event_bus.publish("demo", "task.status_changed", {"task_id": "T-001"})
        event = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert event.project == "demo"
    assert event.kind == "task.status_changed"
    assert event.payload == {"task_id": "T-001"}
    assert event.ts > 0


async def test_publish_to_other_project_is_not_seen() -> None:
    async with event_bus.subscribe("demo") as sub:
        await event_bus.publish("other", "task.status_changed", {"x": 1})
        await event_bus.publish("demo", "task.status_changed", {"y": 2})
        event = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert event.payload == {"y": 2}


async def test_multiple_subscribers_each_get_events() -> None:
    async with event_bus.subscribe("demo") as a:
        async with event_bus.subscribe("demo") as b:
            await event_bus.publish("demo", "k", {})
            ea = await asyncio.wait_for(a.__anext__(), timeout=1)
            eb = await asyncio.wait_for(b.__anext__(), timeout=1)
    assert ea.kind == "k"
    assert eb.kind == "k"


async def test_subscriber_unregistered_after_exit() -> None:
    async with event_bus.subscribe("demo"):
        assert event_bus.active_subscribers("demo") == 1
    assert event_bus.active_subscribers("demo") == 0


async def test_publish_sync_inside_async_context_delivers() -> None:
    """publish_sync schedules on the running loop; the event lands like normal."""
    async with event_bus.subscribe("demo") as sub:
        event_bus.publish_sync("demo", "k", {"src": "sync"})
        event = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert event.payload == {"src": "sync"}


@pytest.mark.asyncio(loop_scope="session")
async def test_emit_inside_async_context_is_noop_for_no_subscribers() -> None:
    """emit() outside any subscription is a fire-and-forget no-op (no crash)."""
    # No subscribers — just verifies it doesn't raise.
    event_bus.emit("demo-no-sub", "task.status_changed", task_id="T-1")
    await asyncio.sleep(0)  # let the scheduled task run


# ── COD-072: queue_emit defers until commit ────────────────────────────


async def test_queue_emit_fires_only_after_commit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """queue_emit accumulates events; after_commit listener flushes them."""
    from sqlalchemy.orm import Session as _Session

    session = _Session(bind=engine_with_schema)
    try:
        async with event_bus.subscribe("demo-commit") as sub:
            event_bus.queue_emit(session, "demo-commit", "task.x", k="v")
            # Nothing delivered yet — still pending in the session.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(sub.__anext__(), timeout=0.05)
            session.commit()
            # publish_sync schedules on the running loop; let it run.
            event = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert event.kind == "task.x"
        assert event.payload == {"k": "v"}
    finally:
        session.close()


async def test_publish_does_not_leak_into_unsubscribed_queue() -> None:
    """COD-073: publish runs under the bus lock, so an exited subscriber
    never receives a stray event into its orphaned queue."""
    sub = event_bus.subscribe("demo-leak")
    await sub.__aenter__()
    queue = sub._queue
    await sub.__aexit__(None, None, None)
    # No subscribers registered now.
    assert event_bus.active_subscribers("demo-leak") == 0
    # A burst of publishes must not push anything into the orphan queue.
    for _ in range(10):
        await event_bus.publish("demo-leak", "task.x", {"i": _})
    assert queue.empty()


async def test_queue_emit_drops_events_on_rollback(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A rolled-back transaction must NOT push any event to subscribers."""
    from sqlalchemy.orm import Session as _Session

    session = _Session(bind=engine_with_schema)
    try:
        async with event_bus.subscribe("demo-rollback") as sub:
            event_bus.queue_emit(session, "demo-rollback", "task.x", k="v")
            session.rollback()
            # Even after the rollback, nothing should land in the queue.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(sub.__anext__(), timeout=0.1)
    finally:
        session.close()
