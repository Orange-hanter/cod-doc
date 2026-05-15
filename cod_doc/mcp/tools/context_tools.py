"""MCP tools: context_get (COD-033) and capabilities (PCA-940)."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _active_profile() -> str:
    """Defer import to avoid circular: server depends on context_tools."""
    try:
        from cod_doc.mcp.server import get_active_profile

        return get_active_profile()
    except Exception:
        return "full"


def _envelope_error(
    code: str,
    message: str,
    *,
    hint: str | None,
    related_tools: list[str],
    retry_safe: bool,
) -> dict[str, Any]:
    """Build the error half of the PCA-943 envelope."""
    return {
        "ok": False,
        "result": None,
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
            "related_tools": related_tools,
            "retry_safe": retry_safe,
        },
    }


def _tool_family(name: str) -> str:
    """Map tool name → family bucket for capabilities()."""
    # Strict family prefixes — must come before generic split.
    for prefix in (
        "task_doc_",
        "task_checkout",
        "task_release",
        "approval_",
        "routine_",
        "activity_",
        "revision_",
        "plan_",
        "story_",
        "link_",
        "run_",
        "skill_",
        "doc_",
        "task_",
    ):
        if name.startswith(prefix):
            return prefix.rstrip("_") if prefix.endswith("_") else prefix
    return "misc"


def _score_tool(query_terms: set[str], name: str, description: str) -> int:
    """Cheap relevance score for tool_search.

    Higher = better match. Name hits count 3x more than description hits.
    Each unique matching term contributes; we don't reward repeats inside
    a single description (avoids spammy keyword stuffing).
    """
    if not query_terms:
        return 0
    name_lower = name.lower()
    desc_lower = description.lower()
    score = 0
    for term in query_terms:
        if term in name_lower:
            score += 3
        if term in desc_lower:
            score += 1
    return score


def register(mcp: FastMCP) -> None:
    """Register context tools."""

    @mcp.tool()
    def set_default_project(name: str) -> dict[str, Any]:
        """Set the session-scoped default project name (PCA-945).

        After this call, ``get_default_project()`` returns ``name``. Future
        tools may consult this default when ``project`` is omitted (the
        auto-resolution rollout is staged across releases — see
        ``cod_doc/mcp/tools/_workspace.py``).

        **Admin-only for agent flow (AGT-010).** Agents using the
        ``--profile agent`` surface pass ``agent_id`` per call to
        ``agent_pick`` — they don't need a workspace state for project.
        These tools remain available under ``--profile standard|full``
        for CLI / web / multi-step admin sessions.
        """
        from cod_doc.mcp.tools import _workspace

        _workspace.set_(name)
        return {"default_project": name}

    @mcp.tool()
    def get_default_project() -> dict[str, Any]:
        """Return the current session-scoped default project name, or null."""
        from cod_doc.mcp.tools import _workspace

        return {"default_project": _workspace.get()}

    @mcp.tool()
    def clear_default_project() -> dict[str, Any]:
        """Clear the session-scoped default project name."""
        from cod_doc.mcp.tools import _workspace

        _workspace.clear()
        return {"default_project": None}

    @mcp.tool()
    def tool_describe(name: str) -> dict[str, Any]:
        """Return the full contract for one MCP tool (cycle-4 middle layer).

        Fills the gap between ``capabilities()`` (high-level counts) and
        the raw ``tools/list`` (all 102 docstrings). Single call returns
        everything an agent needs to call ``name`` correctly:

            {
              "name": "task_create",
              "family": "task",
              "description": "Create a new DB task in a plan section.\\n...",
              "input_schema": {<JSON Schema>},
              "required_params": ["project", "plan_scope", ...],
              "optional_params": [{"name": "id_prefix", "type": "...", "default": ...}, ...],
              "deprecated": false,
              "examples": [],         # placeholder — populated for future tools
              "related_tools": ["task_get", "task_update_status", ...]
            }

        On unknown ``name`` returns a structured hint with related_tools=[tool_search].
        """
        tools = mcp._tool_manager._tools  # noqa: SLF001
        if name not in tools:
            return {
                "name": name,
                "found": False,
                "hint": (
                    f"No such tool: {name!r}. Try tool_search(query=...) "
                    "or capabilities() to discover what's available."
                ),
                "related_tools": ["tool_search", "capabilities"],
            }

        tool = tools[name]
        # FastMCP internal Tool object: schema lives under `.parameters`
        # (JSON Schema dict). Different from the public ``mcp.types.Tool``
        # which uses ``inputSchema``.
        schema = getattr(tool, "parameters", None) or {}
        required = schema.get("required", [])
        props = schema.get("properties", {})
        optional = [
            {
                "name": pname,
                "type": pdef.get("type")
                or [a.get("type") for a in pdef.get("anyOf", []) if "type" in a],
                "default": pdef.get("default"),
                "description": pdef.get("description") or pdef.get("title"),
            }
            for pname, pdef in props.items()
            if pname not in required
        ]
        family = _tool_family(name)
        desc = tool.description or ""
        deprecated = "DEPRECATED" in desc.upper()[:80]

        # Crude related-tools heuristic: other tools in the same family.
        related = sorted(
            n
            for n in tools
            if n != name and _tool_family(n) == family
        )[:5]

        return {
            "name": name,
            "found": True,
            "family": family,
            "description": desc,
            "input_schema": schema,
            "required_params": list(required),
            "optional_params": optional,
            "deprecated": deprecated,
            "examples": [],
            "related_tools": related,
        }

    @mcp.tool()
    def tool_call_safe(
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call any MCP tool and return a uniform envelope (PCA-943).

        **Admin-only for agent flow (AGT-010).** Agents using the
        ``--profile agent`` surface get unified error shapes natively
        from ``agent_pick`` / ``agent_complete`` / ``agent_report`` /
        ``agent_release`` (each returns ``{ok, ...}`` with a ``hint``
        on failure). This proxy is for admin sessions calling raw
        CRUD tools that don't share that contract.


        Wraps the named tool so the caller gets ONE error shape across
        the whole surface instead of writing 5 different handlers:

            {
              "ok": bool,
              "result": <whatever the tool returned> | null,
              "error": {
                "code": "not_found" | "validation" | "duplicate" |
                        "locked" | "transition_invalid" | "internal",
                "message": str,
                "hint": str | null,
                "related_tools": list[str],
                "retry_safe": bool
              } | null
            }

        Use cases: agent shells that want unified retry/fallback logic.
        Existing direct-call paths are unchanged (per acceptance the goal
        is "agent doesn't have to write 4–5 handlers" — this proxy gives
        them one without touching the wire contract of 97 other tools).

        ``args`` is the kwargs dict you'd pass directly to ``tool_name``.
        """
        from cod_doc.services.task_service import (
            DuplicateTaskError,
            TaskAlreadyDoneError,
            TaskBlockedError,
            TaskNotFoundError,
        )
        from cod_doc.services.task_status_machine import StatusTransitionError
        from cod_doc.services.validation import ValidationError

        if tool_name not in mcp._tool_manager._tools:
            return {
                "ok": False,
                "result": None,
                "error": {
                    "code": "tool_not_found",
                    "message": f"No such MCP tool: {tool_name!r}",
                    "hint": "Call tool_search(query=...) or capabilities() to discover tools.",
                    "related_tools": ["tool_search", "capabilities"],
                    "retry_safe": False,
                },
            }

        target = mcp._tool_manager._tools[tool_name]
        kwargs = args or {}
        try:
            result = target.fn(**kwargs)
            return {"ok": True, "result": result, "error": None}
        except TaskNotFoundError as exc:
            return _envelope_error(
                "not_found", str(exc),
                hint="Verify task_id via task_list or task_find_duplicate(title=...).",
                related_tools=["task_list", "task_find_duplicate"],
                retry_safe=False,
            )
        except DuplicateTaskError as exc:
            return _envelope_error(
                "duplicate", str(exc),
                hint="Pass allow_duplicate=True to override, or update the existing task.",
                related_tools=["task_find_duplicate", "task_update_status"],
                retry_safe=False,
            )
        except TaskAlreadyDoneError as exc:
            return _envelope_error(
                "already_done", str(exc),
                hint="The task is already in 'done' status — no further action needed.",
                related_tools=["task_get"],
                retry_safe=False,
            )
        except TaskBlockedError as exc:
            return _envelope_error(
                "blocked", str(exc),
                hint="Close blocking dependencies first, then retry task_complete.",
                related_tools=["task_list_blocked", "plan_ready"],
                retry_safe=False,
            )
        except StatusTransitionError as exc:
            return _envelope_error(
                "transition_invalid", str(exc),
                hint=(
                    "See cod_doc/services/task_status_machine.py for the legal "
                    "transition graph (skill task-standard)."
                ),
                related_tools=["task_get", "capabilities"],
                retry_safe=False,
            )
        except ValidationError as exc:
            return _envelope_error(
                "validation", str(exc),
                hint="Inspect the failing field and retry with corrected args.",
                related_tools=["capabilities"],
                retry_safe=False,
            )
        except (LookupError, ValueError) as exc:
            return _envelope_error(
                "validation", str(exc),
                hint=None,
                related_tools=[],
                retry_safe=False,
            )
        except Exception as exc:  # last-resort
            return _envelope_error(
                "internal", f"{type(exc).__name__}: {exc}",
                hint="Server-side error — check logs.",
                related_tools=[],
                retry_safe=True,
            )

    @mcp.tool()
    def tools_diff(since: str = "HEAD") -> dict[str, Any]:
        """Diff the current MCP catalog against a named snapshot (PCA-950).

        Snapshots live in ``.cod-doc/tool_snapshots/<name>.json`` and are
        written by ``scripts/snapshot_tools.py`` (intended to run from CI on
        release-tag commits). For integrators caching the MCP schema between
        sessions: one call surfaces ``{added, removed, changed}``.

        Returns
        -------
        ``{
            "since": "<name>",
            "snapshot_found": bool,
            "snapshot_created_utc": ... | null,
            "added":   [tool_name, ...],   # in current but not snapshot
            "removed": [tool_name, ...],   # in snapshot but not current
            "changed": [
              {"name": ..., "fields_added": [...], "fields_removed": [...],
               "required_changed": bool, "description_changed": bool}
            ]
          }``
        """
        import json
        from pathlib import Path

        snapshot_dir = (
            Path(__file__).resolve().parents[3] / ".cod-doc" / "tool_snapshots"
        )
        snap_path = snapshot_dir / f"{since}.json"
        if not snap_path.exists():
            return {
                "since": since,
                "snapshot_found": False,
                "snapshot_created_utc": None,
                "added": [],
                "removed": [],
                "changed": [],
                "hint": (
                    f"No snapshot at {snap_path}. Run "
                    f"`python -m scripts.snapshot_tools --name {since}` first."
                ),
            }

        snap_data = json.loads(snap_path.read_text(encoding="utf-8"))
        snap_tools = {t["name"]: t for t in snap_data.get("tools", [])}

        current = {
            t.name: {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in asyncio.run(mcp.list_tools())
        }

        added = sorted(set(current) - set(snap_tools))
        removed = sorted(set(snap_tools) - set(current))
        changed: list[dict[str, Any]] = []
        for name in sorted(set(current) & set(snap_tools)):
            cur = current[name]
            snap = snap_tools[name]
            cur_props = set((cur["input_schema"].get("properties") or {}).keys())
            snap_props = set((snap["input_schema"].get("properties") or {}).keys())
            cur_req = set(cur["input_schema"].get("required") or [])
            snap_req = set(snap["input_schema"].get("required") or [])
            fields_added = sorted(cur_props - snap_props)
            fields_removed = sorted(snap_props - cur_props)
            required_changed = cur_req != snap_req
            desc_changed = cur["description"] != snap["description"]
            if fields_added or fields_removed or required_changed or desc_changed:
                changed.append(
                    {
                        "name": name,
                        "fields_added": fields_added,
                        "fields_removed": fields_removed,
                        "required_changed": required_changed,
                        "description_changed": desc_changed,
                    }
                )

        return {
            "since": since,
            "snapshot_found": True,
            "snapshot_created_utc": snap_data.get("created_utc"),
            "added": added,
            "removed": removed,
            "changed": changed,
        }

    @mcp.tool()
    def tool_search(
        query: str,
        limit: int = 3,
        family: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic-lite discovery across the MCP catalog (PCA-942).

        For a cold-start agent: typing a natural-language query (e.g.
        ``"close a task with reason"``) returns the top ``limit``
        candidates with name + short description + required params +
        family + deprecated flag. Saves 2–3 wrong tool picks per session.

        Scoring: case-insensitive substring match on each whitespace-split
        token of ``query`` — name hits weighted 3x, description hits 1x.
        Cheap and deterministic; no embedding lookups.

        ``family`` (optional) restricts to a prefix bucket (same names as
        in capabilities().tools.families, e.g. "task", "plan", "doc").
        """
        terms = {t.lower() for t in query.split() if len(t) >= 2}
        all_tools = asyncio.run(mcp.list_tools())

        scored: list[tuple[int, Any]] = []
        for tool in all_tools:
            fam = _tool_family(tool.name)
            if family is not None and fam != family:
                continue
            score = _score_tool(terms, tool.name, tool.description or "")
            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda x: (-x[0], x[1].name))
        results: list[dict[str, Any]] = []
        for score, tool in scored[:limit]:
            desc = (tool.description or "").strip()
            # First line of description as one-liner; full text via tools/list.
            one_line = desc.split("\n", 1)[0] if desc else ""
            required = tool.inputSchema.get("required", [])
            deprecated = "DEPRECATED" in desc.upper()[:80]
            results.append(
                {
                    "name": tool.name,
                    "family": _tool_family(tool.name),
                    "description": one_line,
                    "required_params": list(required),
                    "deprecated": deprecated,
                    "score": score,
                }
            )
        return results

    @mcp.tool()
    def capabilities() -> dict[str, Any]:
        """Self-describing server snapshot for fresh agent sessions (PCA-940).

        Single-call bootstrap — replaces the cold-start round-trip of
        ``skill_list`` + ``list_projects`` + reading 93 tool docstrings.

        Returns
        -------
        ``{
            "cod_doc_version": "1.1.0",
            "server_name": "COD-DOC",
            "tools": {"total": 94, "families": {"task": 13, ...}},
            "skills": [{"name": ..., "description": ...}, ...],
            "enums": {
                "task_status": ["backlog", "todo", "in_progress", ...],
                "task_type": ["feature", "bug", ...],
                "priority": ["critical", "high", "medium", "low"],
                "task_status_legacy_aliases": {"pending": "todo", ...},
            },
            "references": {
                "agents_md": "AGENTS.md",
                "state_machine": "cod_doc/services/task_status_machine.py",
                "skills_root": "cod_doc/skills/",
                "mcp_integration_doc": "docs/mcp-integration.md",
            }
        }``
        """
        from cod_doc import __version__ as version
        from cod_doc.domain.entities import Priority, TaskStatus, TaskType
        from cod_doc.mcp.tools import _workspace
        from cod_doc.mcp.tools.skill_tools import iter_skill_records
        from cod_doc.services.task_status_machine import (
            ALLOWED_TRANSITIONS,
            _LEGACY_ALIASES,
        )

        all_tools = asyncio.run(mcp.list_tools())
        family_counts = Counter(_tool_family(t.name) for t in all_tools)

        canonical_statuses = sorted(
            set(ALLOWED_TRANSITIONS.keys())
            | {dst for dsts in ALLOWED_TRANSITIONS.values() for dst in dsts}
        )

        return {
            "cod_doc_version": version,
            "server_name": "COD-DOC",
            "tools": {
                "total": len(all_tools),
                "families": dict(sorted(family_counts.items())),
            },
            "skills": [
                {"name": r.get("name"), "description": r.get("description")}
                for r in iter_skill_records()
            ],
            "enums": {
                "task_status_canonical": canonical_statuses,
                "task_status_legacy_aliases": dict(_LEGACY_ALIASES),
                "task_type": sorted(t.value for t in TaskType),
                "priority": [p.value for p in Priority],
                # TaskStatus enum members (canonical + legacy) — agent can
                # validate any status string against this superset.
                "task_status_all_known": sorted(s.value for s in TaskStatus),
            },
            "references": {
                "agents_md": "AGENTS.md",
                "state_machine": "cod_doc/services/task_status_machine.py",
                "skills_root": "cod_doc/skills/",
                "mcp_integration_doc": "docs/mcp-integration.md",
            },
            "session": {
                "default_project": _workspace.get(),
                "default_project_usage": (
                    "set via set_default_project(name=...). Then pass "
                    "project='' on subsequent DB-tool calls to auto-fall-back "
                    "to this default (cycle-4)."
                ),
            },
            "profile": _active_profile(),
        }

    @mcp.tool()
    def context_get(
        project: str,
        target_kind: str,
        target_id: str,
        depth: str = "L1",
        token_budget: int = 8000,
    ) -> dict[str, Any]:
        """Assemble minimal-sufficient context for the given target.

        Parameters
        ----------
        project:     Project slug.
        target_kind: One of ``document``, ``task``, ``plan``, ``module``.
        target_id:   Identifier within the target kind:
                     - document → doc_key (e.g. ``modules/M1-auth/overview``)
                     - task     → task_id (e.g. ``AUTH-025``)
                     - plan     → plan scope (e.g. ``M1-auth-module``)
                     - module   → module_id (e.g. ``M1-auth``)
        depth:       ``L0`` (metadata only) | ``L1`` (body + direct relations).
                     L2/L3 reserved for future semantic expansion.
        token_budget: Approximate token ceiling (default 8000).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import context_service

        SessionFactory, entry = session_factory(project)
        with transactional(SessionFactory) as session:
            project_id = require_project_id(session, project)

            # Optionally attach master_content for master_excerpt
            master_content: str | None = None
            try:
                if entry.master_path.exists():
                    master_content = entry.master_path.read_text(encoding="utf-8")
            except Exception:
                pass

            return context_service.context_get(
                session=session,
                project_id=project_id,
                target_kind=target_kind,
                target_id=target_id,
                depth=depth,
                token_budget=token_budget,
                master_content=master_content,
            )
