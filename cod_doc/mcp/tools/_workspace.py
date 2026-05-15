"""PCA-945: workspace-scoped defaults (process-memory).

Scope (cycle-3 minimum-viable):

- Single process-level default project name. In stdio mode each MCP
  server process is one session, so process-memory = session-scoped.
- Auto-resolution in every DB-tool signature is deferred — that requires
  refactoring 70+ tool signatures to make ``project`` Optional. Agents
  who want to use the default explicitly read it via
  ``get_default_project()`` at session start.
"""

from __future__ import annotations

from threading import Lock

_DEFAULT: dict[str, str | None] = {"project": None}
_LOCK = Lock()


def get() -> str | None:
    with _LOCK:
        return _DEFAULT["project"]


def set_(name: str | None) -> None:
    with _LOCK:
        _DEFAULT["project"] = name


def clear() -> None:
    set_(None)


def resolve(project: str | None) -> str:
    """Return ``project`` if given, otherwise the default. Raises if neither."""
    if project:
        return project
    default = get()
    if not default:
        raise ValueError(
            "No `project` passed and no default set. "
            "Call set_default_project(name=...) at session start, "
            "or pass project=<slug> explicitly."
        )
    return default
