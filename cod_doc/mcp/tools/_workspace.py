"""PCA-945: workspace-scoped defaults (process-memory).

Scope (cycle-3 minimum-viable):

- Single process-level default project name. In stdio mode each MCP
  server process is one session, so process-memory = session-scoped.
- Auto-resolution in every DB-tool signature is deferred — that requires
  refactoring 70+ tool signatures to make ``project`` Optional. Agents
  who want to use the default explicitly read it via
  ``get_default_project()`` at session start.

Shared mode (streamable-http)
-----------------------------

Under ``--transport streamable-http`` a single process serves every local
harness at once, and the "process == session" equality above stops
holding: ``stateless_http`` builds a transport per request, but module
state is shared and there is nothing per-request to hang a default on.
A shared default there is not a convenience, it is a silent cross-client
redirect — one client calling ``set_default_project`` would retarget
every ``project=None`` call made by all the others.

``set_shared(True)`` therefore removes the default entirely: ``set_``
refuses to install one and ``resolve`` demands an explicit ``project``.
stdio behaviour is unchanged.
"""

from __future__ import annotations

from threading import Lock

_DEFAULT: dict[str, str | None] = {"project": None}
_SHARED: dict[str, bool] = {"shared": False}
_LOCK = Lock()

#: Appended to every error raised because the process is shared. Kept as one
#: string so the CLI, the MCP error payload and the tests quote it verbatim.
SHARED_HINT = (
    "This server is shared by every local harness over HTTP, so it has no "
    "per-session default project. Pass project=<slug> explicitly."
)


def set_shared(shared: bool) -> None:
    """Declare whether this process serves more than one client.

    Called once at server start from :func:`cod_doc.mcp.server.run_mcp_server`
    — ``True`` for ``streamable-http``, ``False`` for stdio. Turning it on
    drops any default already installed, so a daemon cannot inherit one from
    startup code.
    """
    with _LOCK:
        _SHARED["shared"] = shared
        if shared:
            _DEFAULT["project"] = None


def is_shared() -> bool:
    """True when the process serves multiple clients (HTTP transport)."""
    with _LOCK:
        return _SHARED["shared"]


def get() -> str | None:
    with _LOCK:
        return _DEFAULT["project"]


def set_(name: str | None) -> None:
    """Install the default project name.

    Raises ``ValueError`` when the process is shared and ``name`` is truthy;
    clearing (``name=None``) stays a no-op that is always allowed.
    """
    with _LOCK:
        if _SHARED["shared"] and name:
            raise ValueError(f"Setting a default project is unavailable. {SHARED_HINT}")
        _DEFAULT["project"] = name


def clear() -> None:
    set_(None)


def resolve(project: str | None) -> str:
    """Return ``project`` if given, otherwise the default. Raises if neither."""
    if project:
        return project
    with _LOCK:
        shared = _SHARED["shared"]
        default = _DEFAULT["project"]
    if shared:
        raise ValueError(f"`project` is required. {SHARED_HINT}")
    if not default:
        raise ValueError(
            "No `project` passed and no default set. "
            "Call set_default_project(name=...) at session start, "
            "or pass project=<slug> explicitly."
        )
    return default
