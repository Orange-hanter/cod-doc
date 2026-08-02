"""PCA-940: capabilities() — single-call bootstrap для cold-start агента.

Покрывает:
- Тул зарегистрирован, без обязательных параметров.
- version + tools count + skills + enums + references присутствуют.
- enum значений совпадают с реальными источниками правды
  (task_status_machine.ALLOWED_TRANSITIONS, Priority, TaskType).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.mcp.server import mcp as live_mcp
from cod_doc.services.task_status_machine import _LEGACY_ALIASES, ALLOWED_TRANSITIONS

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def test_capabilities_is_registered_no_required_params() -> None:
    tools = asyncio.run(live_mcp.list_tools())
    by_name = {t.name: t for t in tools}
    assert "capabilities" in by_name
    required = by_name["capabilities"].inputSchema.get("required", [])
    assert required == [], "capabilities() must take no required params (it's the L0 entry point)"


def test_capabilities_returns_version_tools_skills_enums_references() -> None:
    capabilities = _get_tool(live_mcp, "capabilities")
    result = capabilities()

    assert isinstance(result, dict)
    # 1. Version reachable from a single key.
    assert "cod_doc_version" in result
    assert isinstance(result["cod_doc_version"], str)
    # 2. Tools block — total + family map.
    assert "tools" in result
    assert result["tools"]["total"] >= 90  # we have 93+ in this branch
    assert isinstance(result["tools"]["families"], dict)
    assert "task" in result["tools"]["families"]
    # 3. Skills — at least the orchestrator base skill.
    skill_names = [s["name"] for s in result["skills"]]
    assert "orchestrator" in skill_names
    # 4. Enums — canonical TaskStatus = ALLOWED_TRANSITIONS keys/values union.
    canonical = set(ALLOWED_TRANSITIONS.keys()) | {
        dst for dsts in ALLOWED_TRANSITIONS.values() for dst in dsts
    }
    assert set(result["enums"]["task_status_canonical"]) == canonical
    assert result["enums"]["task_status_legacy_aliases"] == dict(_LEGACY_ALIASES)
    assert set(result["enums"]["priority"]) == {p.value for p in Priority}
    assert set(result["enums"]["task_type"]) == {t.value for t in TaskType}
    # 5. References — pointers to SoT files.
    assert result["references"]["state_machine"].endswith("task_status_machine.py")
    assert "agents_md" in result["references"]


def test_capabilities_family_counts_sum_close_to_total() -> None:
    """Большая часть тулов должна попадать в семейство (не в misc)."""
    capabilities = _get_tool(live_mcp, "capabilities")
    result = capabilities()
    fams = result["tools"]["families"]
    total = result["tools"]["total"]
    misc = fams.get("misc", 0)
    # Допустим до 20% misc — это check_config, hash_file, verify_hash,
    # list_projects, add_project, remove_project, get_project_status,
    # list_tasks, add_task, update_task, next_pending_task, get_master,
    # update_master_hashes, check_stale_refs, generate_ref, read_file,
    # read_context, list_files, search_docs, reindex, run_agent_once,
    # get_agent_context, clear_agent_context, capabilities, context_get
    assert misc / total < 0.4, (
        f"too many tools fell into 'misc' family ({misc}/{total}). "
        f"Update _tool_family() prefix list."
    )
