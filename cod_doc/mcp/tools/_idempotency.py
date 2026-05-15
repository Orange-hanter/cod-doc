"""PCA-948: in-process idempotency cache for write-path MCP tools.

Scope (cycle-3 minimum-viable):

- In-memory only — survives the process lifetime, NOT a server restart.
  Sufficient for the documented use case (network-flap retry: client
  retries within seconds of a transient failure).
- Cross-process / multi-replica idempotency requires a DB-backed
  ``idempotency_record`` table; deferred to a follow-up (see
  ``acceptance`` of PCA-948 for the long-form contract).

Contract:

- ``check(tool_name, key)`` returns the cached result dict if a previous
  call with ``key`` succeeded; ``None`` otherwise.
- ``store(tool_name, key, result)`` records a result; subsequent
  ``check(tool_name, key)`` returns the same dict.
- ``key=None`` short-circuits both (no caching).
"""

from __future__ import annotations

from threading import Lock
from typing import Any

_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_LOCK = Lock()


def check(tool_name: str, key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    with _LOCK:
        cached = _CACHE.get((tool_name, key))
    return dict(cached) if cached is not None else None


def store(tool_name: str, key: str | None, result: dict[str, Any]) -> None:
    if not key:
        return
    with _LOCK:
        _CACHE[(tool_name, key)] = dict(result)


def clear() -> None:
    """Drop all cached records (for tests)."""
    with _LOCK:
        _CACHE.clear()
