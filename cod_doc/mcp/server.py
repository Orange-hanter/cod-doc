"""Native MCP server exposing COD-DOC project operations.

Provides a complete LLM interface to COD-DOC:
- Project management (CRUD, status, tasks)
- Documentation operations (MASTER.md, hashes, references)
- Context delivery (file access via hybrid refs, semantic search)
- Agent orchestration (run tasks, autonomous mode)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from mcp.server.fastmcp import FastMCP

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.hash_calc import calc_hash, check_hash, make_ref, update_hashes
from cod_doc.core.context import get_context, parse_ref
from cod_doc.core.project import Project, Task, TaskStatus
from cod_doc.logging_config import get_logger, setup_logging


log = get_logger("mcp")
mcp = FastMCP("COD-DOC", json_response=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _config() -> Config:
    return Config.load()


def _project(name: str) -> Project:
    cfg = _config()
    entry = cfg.get_project(name)
    if not entry:
        raise ValueError(f"Проект не найден: {name}")
    return Project(entry)


def _project_summary(entry: ProjectEntry) -> dict[str, Any]:
    proj = Project(entry)
    return {
        "name": entry.name,
        "path": entry.path,
        "master_md": entry.master_md,
        "auto_commit": entry.auto_commit,
        "enabled": entry.enabled,
        "master_exists": entry.master_path.exists(),
        "stats": proj.stats(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Project Management
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """List all registered COD-DOC projects with summary stats (task counts, status)."""
    cfg = _config()
    return [_project_summary(entry) for entry in cfg.list_projects()]


@mcp.tool()
def get_project_status(project_name: str) -> dict[str, Any]:
    """Return full project status: tasks, next actions parsed from MASTER.md, and broken links."""
    import re

    proj = _project(project_name)
    master_content = proj.read_master() or ""
    broken_links = re.findall(r"[^\n]*📁[^\n]*🔴[^\n]*", master_content)
    return {
        "project": _project_summary(proj.entry),
        "tasks": [task.to_dict() for task in proj.get_tasks()],
        "next_actions": proj.extract_next_actions(),
        "broken_links": broken_links,
    }


@mcp.tool()
def add_project(
    name: str,
    path: str,
    master_md: str = "MASTER.md",
    auto_commit: bool = False,
) -> dict[str, Any]:
    """Register a new project in COD-DOC, initialize .cod-doc/ state directory and MASTER.md template."""
    cfg = _config()
    entry = ProjectEntry(name=name, path=path, master_md=master_md, auto_commit=auto_commit)
    cfg.add_project(entry)
    proj = Project(entry)
    proj.init()
    log.info("Project added via MCP", extra={"project": name, "event_type": "mcp_add_project"})
    return {"created": name, "project": _project_summary(entry)}


@mcp.tool()
def remove_project(project_name: str) -> dict[str, Any]:
    """Unregister a project from COD-DOC (does not delete files on disk)."""
    cfg = _config()
    removed = cfg.remove_project(project_name)
    if not removed:
        raise ValueError(f"Проект не найден: {project_name}")
    return {"removed": project_name}


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Task Management
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_tasks(
    project_name: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks for a project, optionally filtered by status (pending/in_progress/done/failed/blocked)."""
    proj = _project(project_name)
    filter_status = TaskStatus(status) if status else None
    return [t.to_dict() for t in proj.get_tasks(filter_status)]


@mcp.tool()
def add_task(
    project_name: str,
    title: str,
    description: str = "",
    priority: int = 5,
    context_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Create a documentation task for a COD-DOC project. Lower priority number = higher priority."""
    proj = _project(project_name)
    task = Task(title=title, description=description, priority=priority, context_refs=context_refs or [])
    proj.add_task(task)
    log.info(
        "Task added via MCP",
        extra={"project": project_name, "task_id": task.id, "event_type": "mcp_add_task"},
    )
    return task.to_dict()


@mcp.tool()
def update_task(
    project_name: str,
    task_id: str,
    status: str | None = None,
    result: str | None = None,
    description: str | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """Update fields of an existing task. Only provided fields are changed."""
    changes: dict[str, Any] = {}
    if status is not None:
        changes["status"] = status
    if result is not None:
        changes["result"] = result
    if description is not None:
        changes["description"] = description
    if priority is not None:
        changes["priority"] = priority

    proj = _project(project_name)
    task = proj.update_task(task_id, **changes)
    if not task:
        raise ValueError(f"Задача не найдена: {task_id}")
    return task.to_dict()


@mcp.tool()
def next_pending_task(project_name: str) -> dict[str, Any]:
    """Return the highest-priority pending task, or null if the queue is empty."""
    proj = _project(project_name)
    task = proj.next_pending_task()
    if not task:
        return {"task": None, "message": "Очередь задач пуста"}
    return task.to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — MASTER.md & Documentation
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_master(project_name: str) -> str:
    """Return raw MASTER.md content for a project."""
    proj = _project(project_name)
    content = proj.read_master()
    if content is None:
        raise ValueError(f"MASTER.md не найден для проекта: {project_name}")
    return content


@mcp.tool()
def update_master_hashes(project_name: str) -> dict[str, Any]:
    """Recalculate all SHA-256 hashes in MASTER.md hybrid references and report stale/broken ones."""
    proj = _project(project_name)
    updated, warnings = update_hashes(proj.entry.master_path)
    return {"updated": updated, "warnings": warnings}


@mcp.tool()
def check_stale_refs(project_name: str) -> dict[str, Any]:
    """Scan MASTER.md for hybrid references and check which files have changed (stale) or are missing (broken)."""
    import re
    from cod_doc.core.hash_calc import LINK_PATTERN

    proj = _project(project_name)
    content = proj.read_master() or ""
    repo_root = proj.entry.root

    results: list[dict[str, str]] = []
    for m in LINK_PATTERN.finditer(content):
        rel = m.group("path").lstrip("/")
        expected = m.group("hash")
        target = repo_root / rel
        if not target.exists():
            results.append({"path": rel, "status": "BROKEN", "expected": expected})
        elif not check_hash(target, expected):
            actual = calc_hash(target)
            results.append({"path": rel, "status": "STALE", "expected": expected, "actual": actual})
        else:
            results.append({"path": rel, "status": "VALID", "hash": expected})

    stale = sum(1 for r in results if r["status"] == "STALE")
    broken = sum(1 for r in results if r["status"] == "BROKEN")
    return {"refs": results, "summary": {"total": len(results), "valid": len(results) - stale - broken, "stale": stale, "broken": broken}}


@mcp.tool()
def generate_ref(project_name: str, file_path: str) -> str:
    """Generate a hybrid reference (📁 path | 🗃️ vec_id | 🔑 sha:hash) for a file relative to the project root."""
    proj = _project(project_name)
    target = proj.entry.root / file_path
    if not target.exists():
        raise ValueError(f"Файл не найден: {file_path}")
    return make_ref(target, proj.entry.root)


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Context Delivery (file access via hybrid refs)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def read_context(
    project_name: str,
    ref: str,
    depth: str = "L1",
    page: int = 1,
) -> dict[str, Any]:
    """Read file content by hybrid reference with hash validation. Use depth=L2 to also resolve inline dependencies. Paginated (200 lines/page)."""
    proj = _project(project_name)
    return get_context(ref, proj.entry.root, depth=depth, page=page)


@mcp.tool()
def read_file(
    project_name: str,
    file_path: str,
    page: int = 1,
) -> dict[str, Any]:
    """Read file content by relative path (no hash validation). Returns content, line count, and pagination info."""
    proj = _project(project_name)
    target = proj.entry.root / file_path
    if not target.exists():
        raise ValueError(f"Файл не найден: {file_path}")

    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    page_size = 200
    total_pages = max(1, (len(lines) + page_size - 1) // page_size)
    start = (page - 1) * page_size
    content = "".join(lines[start:start + page_size])

    return {
        "path": file_path,
        "content": content,
        "total_lines": len(lines),
        "page": page,
        "total_pages": total_pages,
        "has_more": page < total_pages,
    }


@mcp.tool()
def list_files(
    project_name: str,
    directory: str = ".",
    pattern: str = "*",
) -> list[str]:
    """List files in a project directory matching a glob pattern. Returns relative paths."""
    proj = _project(project_name)
    target = proj.entry.root / directory
    if not target.exists():
        raise ValueError(f"Директория не найдена: {directory}")
    return sorted(
        str(f.relative_to(proj.entry.root))
        for f in target.rglob(pattern)
        if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts
    )


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Hash Utilities
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def hash_file(project_name: str, file_path: str) -> dict[str, str]:
    """Compute SHA-256 hash (first 12 hex chars) for a project file."""
    proj = _project(project_name)
    target = proj.entry.root / file_path
    if not target.exists():
        raise ValueError(f"Файл не найден: {file_path}")
    return {"path": file_path, "hash": calc_hash(target)}


@mcp.tool()
def verify_hash(project_name: str, file_path: str, expected_hash: str) -> dict[str, Any]:
    """Check if a file's current hash matches the expected value."""
    proj = _project(project_name)
    target = proj.entry.root / file_path
    if not target.exists():
        raise ValueError(f"Файл не найден: {file_path}")
    actual = calc_hash(target)
    return {
        "path": file_path,
        "expected": expected_hash,
        "actual": actual,
        "valid": check_hash(target, expected_hash),
    }


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Semantic Search (ChromaDB)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def search_docs(
    project_name: str,
    query: str,
    n_results: int = 5,
) -> list[dict[str, Any]]:
    """Semantic search across indexed documentation files. Returns matched snippets with paths, scores, and hashes."""
    from cod_doc.core.reindex import search_documents

    proj = _project(project_name)
    chroma_path = str(proj.entry.cod_doc_dir / "chroma")
    return search_documents(query, chroma_path, project_root=str(proj.entry.root), n_results=n_results)


@mcp.tool()
def reindex(project_name: str) -> dict[str, Any]:
    """Rebuild the ChromaDB vector index for a project's documentation files (specs/, arch/, models/, docs/)."""
    from cod_doc.core.reindex import reindex_project

    proj = _project(project_name)
    chroma_path = str(proj.entry.cod_doc_dir / "chroma")
    return reindex_project(proj.entry.root, chroma_path)


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Agent Orchestration
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def run_agent_once(project_name: str, autonomous: bool = True) -> list[dict[str, Any]]:
    """Run the COD-DOC agent: autonomous mode generates tasks from MASTER.md; non-autonomous runs the next pending task."""
    from cod_doc.agent.orchestrator import Orchestrator

    cfg = _config()
    if not cfg.is_configured:
        raise ValueError("API-ключ не настроен. Запустите cod-doc wizard")

    proj = _project(project_name)
    orch = Orchestrator(proj, cfg)
    events: list[dict[str, Any]] = []

    if autonomous:
        gen = orch.run_autonomous()
    else:
        task = proj.next_pending_task()
        if not task:
            return []
        gen = orch.run_task(task)

    async for event in gen:
        events.append(event.to_dict())
    return events


@mcp.tool()
def get_agent_context(project_name: str) -> list[dict[str, str]]:
    """Return the agent's conversation history (last 50 messages) for a project."""
    proj = _project(project_name)
    return proj.get_context_messages()


@mcp.tool()
def clear_agent_context(project_name: str) -> dict[str, str]:
    """Clear the agent's conversation history for a project."""
    proj = _project(project_name)
    proj.clear_context()
    return {"cleared": project_name}


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Configuration
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def check_config() -> dict[str, Any]:
    """Check COD-DOC configuration status: whether API key is set and how many projects are registered."""
    cfg = _config()
    return {
        "is_configured": cfg.is_configured,
        "project_count": len(cfg.list_projects()),
        "api_host": cfg.api_host,
        "api_port": cfg.api_port,
    }


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCES
# ══════════════════════════════════════════════════════════════════════════════

@mcp.resource("cod-doc://config")
def config_resource() -> str:
    """COD-DOC configuration (sanitized, no API key)."""
    cfg = _config().model_dump()
    cfg.pop("api_key", None)
    return json.dumps(cfg, ensure_ascii=False, indent=2)


@mcp.resource("cod-doc://projects")
def projects_resource() -> str:
    """Registry of all COD-DOC projects with stats."""
    return json.dumps(list_projects(), ensure_ascii=False, indent=2)


@mcp.resource("cod-doc://project/{project_name}/master")
def project_master_resource(project_name: str) -> str:
    """MASTER.md content for a specific project."""
    return get_master(project_name)


@mcp.resource("cod-doc://project/{project_name}/tasks")
def project_tasks_resource(project_name: str) -> str:
    """Task list for a specific project."""
    proj = _project(project_name)
    return json.dumps([t.to_dict() for t in proj.get_tasks()], ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

@mcp.prompt()
def doc_review(project_name: str, focus: str = "structure and stale links") -> str:
    """Review project documentation: identify missing modules, stale hashes, and next actions."""
    return (
        f"Review the COD-DOC documentation for project '{project_name}'. "
        f"Focus on {focus}. "
        "Use the project status, task list, and MASTER.md to identify missing modules, stale hashes, "
        "and the next concrete documentation actions."
    )


@mcp.prompt()
def doc_plan(project_name: str) -> str:
    """Generate a documentation plan for a project from scratch."""
    return (
        f"Create a comprehensive documentation plan for the project '{project_name}'. "
        "Steps:\n"
        "1. Use list_files to discover the project structure\n"
        "2. Read key source files to understand the architecture\n"
        "3. Check the current MASTER.md state\n"
        "4. Identify all documentation levels needed (L0 overview, L1 modules, L2 implementation)\n"
        "5. Create tasks for each documentation unit\n"
        "6. Prioritize: architecture overview first, then API/interface docs, then implementation details"
    )


@mcp.prompt()
def onboard_project(project_name: str) -> str:
    """Onboard a new project into COD-DOC: scan, plan docs, create initial tasks."""
    return (
        f"Onboard the project '{project_name}' into COD-DOC documentation system. "
        "Workflow:\n"
        "1. Use get_project_status to check current state\n"
        "2. Use list_files to discover project structure\n"
        "3. Read MASTER.md to understand what's already documented\n"
        "4. Use check_stale_refs to find broken or outdated references\n"
        "5. Create documentation tasks covering all undocumented areas\n"
        "6. Run update_master_hashes to fix stale hashes\n"
        "7. Provide a summary of the documentation coverage and gaps"
    )


@click.command()
@click.option("--transport", type=click.Choice(["stdio", "streamable-http"]), default="stdio")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, type=int, show_default=True)
@click.option("--log-level", default=None, envvar="LOG_LEVEL")
@click.option("--log-format", default=None, envvar="LOG_FORMAT")
def main(transport: str, host: str, port: int, log_level: str | None, log_format: str | None) -> None:
    """Run the COD-DOC MCP server."""
    setup_logging(level=log_level, fmt=log_format)
    if transport == "streamable-http":
        mcp.run(transport=transport, host=host, port=port, stateless_http=True)
        return
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()