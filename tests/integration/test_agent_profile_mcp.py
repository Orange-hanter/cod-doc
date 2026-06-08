"""Cycle-5 integration tests: agent-profile MCP tools via subprocess (AGN-010..013).

Each test starts a real MCP subprocess with ``--profile agent``, seeds the
embedded SQLite DB with project/plan/section/task rows, and calls the 6-tool
agent surface through ``ClientSession.call_tool``.

AGN-010: Skeleton + fixture + smoke test for agent_capabilities.
AGN-011: agent_pick end-to-end (seed ready task, call agent_pick, assert task card).
AGN-012: agent_complete + agent_release flow.
AGN-013: agent_get + agent_report smoke.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

import cod_doc.config as config_module
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.mcp.profiles import AGENT_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _run_alembic_upgrade(db_url: str) -> None:
    """Run ``alembic upgrade head`` against the given DB URL."""
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


def _seed_project(session) -> int:
    """Create a minimal project row and return its row_id."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="agn-test", title="AGN Test", root_path="/tmp/agn", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _seed_plan(session, project_id: int) -> tuple[int, int]:
    """Create a plan + section and return (plan_row_id, section_row_id)."""
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope="agn-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Alpha", slug="A", position=0)
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id


def _seed_task(session, project_id: int, plan_id: int, section_id: int, task_id: str, **kw):
    """Create a task row via raw model insert (avoids task_service dependency)."""
    now = datetime.now(UTC)
    task = TaskModel(
        project_id=project_id,
        plan_id=plan_id,
        section_id=section_id,
        task_id=task_id,
        title=kw.get("title", f"Implement {task_id}"),
        type=kw.get("type", "feature"),
        priority=kw.get("priority", "medium"),
        status=kw.get("status", "pending"),
        acceptance=kw.get("acceptance"),
        description=kw.get("description"),
        created=now,
        last_updated=now,
    )
    session.add(task)
    session.flush()
    return task


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mcp_agent_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ProjectEntry, Path]:
    """Create an isolated COD-DOC config + project with a seeded DB.

    Returns (entry, config_dir) so tests can open an MCP subprocess against it.
    The embedded DB at ``<project_root>/.cod-doc/state.db`` is alembic-upgraded
    and pre-seeded with a project, plan, section, and optionally tasks.
    """
    config_dir = tmp_path / ".cod-doc-home"
    config_file = config_dir / "config.yaml"
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    repo = tmp_path / "agn-repo"
    repo.mkdir()
    entry = ProjectEntry(name="agn-test", path=str(repo))
    Project(entry).init()

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://example.com")
    cfg.projects = [entry.model_dump()]
    cfg.save()

    # Alembic-upgrade the embedded DB.
    db_url = f"sqlite:///{repo / '.cod-doc' / 'state.db'}"
    _run_alembic_upgrade(db_url)

    # Seed project + plan + section.
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        pid = _seed_project(session)
        plid, sid = _seed_plan(session, pid)
        # Store ids on the entry for test access.
        entry._seed_ids = (pid, plid, sid)  # type: ignore[attr-defined]
    engine.dispose()

    return entry, config_dir


def _open_stdio_client(config_dir: Path):
    """Open a stdio MCP client subprocess with ``--profile agent``."""
    params = StdioServerParameters(
        command=str(REPO_ROOT / ".venv" / "bin" / "python"),
        args=[
            "-m",
            "cod_doc.mcp.server",
            "--transport",
            "stdio",
            "--profile",
            "agent",
        ],
        # Explicitly set COD_DOC_PROFILE so the click default in the subprocess
        # evaluates to 'agent' at module import time.
        env={**os.environ, "COD_DOC_HOME": str(config_dir), "COD_DOC_PROFILE": "agent"},
        cwd=str(REPO_ROOT),
    )
    return stdio_client(params)


# --------------------------------------------------------------------------- #
# AGN-010: Skeleton + fixture + smoke test for agent_capabilities             #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_agn010_agent_capabilities_smoke(
    mcp_agent_project: tuple[ProjectEntry, Path],
) -> None:
    """AGN-010: agent_capabilities returns L0 payload with exactly 6 tools registered."""
    _, config_dir = mcp_agent_project
    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # Verify exactly 6 agent tools are registered.
        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        assert tool_names == AGENT_TOOLS, (
            f"Expected {sorted(AGENT_TOOLS)}, got {sorted(tool_names)}"
        )

        # Call agent_capabilities and verify L0 shape.
        result = await session.call_tool("agent_capabilities", {})
        caps = result.content[0].text

    for key in (
        "server_version",
        "profile",
        "skills",
        "task_status_canonical",
        "task_status_legacy_aliases",
        "default_project",
        "orchestrator_skill",
        "next_action_hint",
    ):
        assert key in caps, f"agent_capabilities missing key: {key}"
    assert '"profile": "agent"' in caps
    assert '"skills"' in caps
    assert '"todo"' in caps
    assert '"in_progress"' in caps


# --------------------------------------------------------------------------- #
# AGN-011: agent_pick end-to-end                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_agn011_agent_pick_end_to_end(
    mcp_agent_project: tuple[ProjectEntry, Path],
) -> None:
    """AGN-011: seed a ready task, call agent_pick, assert full task card."""
    entry, config_dir = mcp_agent_project
    pid, plid, sid = entry._seed_ids  # type: ignore[attr-defined]

    # Seed a ready task directly in the DB.
    repo = Path(entry.path)
    db_url = f"sqlite:///{repo / '.cod-doc' / 'state.db'}"
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        _seed_task(
            session,
            pid,
            plid,
            sid,
            "AGN-011",
            title="Integration pick test",
            priority="high",
            acceptance="✓ Step one done; ✓ Step two done",
        )
    engine.dispose()

    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "agent_pick",
            {"project": "agn-test", "agent_id": "agn-011-agent"},
        )
        card_text = result.content[0].text

    # Task card shape.
    assert '"task"' in card_text
    assert '"context"' in card_text
    assert '"navigation"' in card_text
    # Task details.
    assert '"AGN-011"' in card_text
    assert '"in-progress"' in card_text or '"in_progress"' in card_text
    # Navigation block with skills.
    assert '"applicable_skills"' in card_text
    assert '"success_criteria"' in card_text
    assert '"legal_status_transitions"' in card_text
    # Context block.
    assert '"plan"' in card_text
    assert '"related_docs"' in card_text
    assert '"siblings"' in card_text


# --------------------------------------------------------------------------- #
# AGN-012: agent_complete + agent_release flow                                #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_agn012_agent_complete_flow(
    mcp_agent_project: tuple[ProjectEntry, Path],
) -> None:
    """AGN-012: pick → complete → verify done + lock released."""
    entry, config_dir = mcp_agent_project
    pid, plid, sid = entry._seed_ids  # type: ignore[attr-defined]

    repo = Path(entry.path)
    db_url = f"sqlite:///{repo / '.cod-doc' / 'state.db'}"
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        _seed_task(
            session,
            pid,
            plid,
            sid,
            "AGN-012A",
            title="Complete me",
            priority="high",
        )
    engine.dispose()

    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # Pick the task.
        pick_result = await session.call_tool(
            "agent_pick",
            {"project": "agn-test", "agent_id": "agn-012-agent"},
        )
        pick_text = pick_result.content[0].text
        assert '"AGN-012A"' in pick_text

        # Complete the task.
        complete_result = await session.call_tool(
            "agent_complete",
            {
                "project": "agn-test",
                "task_id": "AGN-012A",
                "agent_id": "agn-012-agent",
                "summary": "Done via integration test",
            },
        )
        complete_text = complete_result.content[0].text

    assert '"ok": true' in complete_text or '"ok": True' in complete_text
    assert '"AGN-012A"' in complete_text
    assert '"done"' in complete_text

    # Verify DB: task status is "done", checked_out_by is None.
    engine2 = make_engine(db_url)
    factory2 = make_session_factory(engine2)
    with transactional(factory2) as session:
        from sqlalchemy import select

        row = session.execute(select(TaskModel).where(TaskModel.task_id == "AGN-012A")).scalar_one()
        assert row.status == "done", f"Expected done, got {row.status}"
        assert row.checked_out_by is None, "Lock should be released after complete"
    engine2.dispose()


@pytest.mark.anyio
async def test_agn012_agent_release_flow(
    mcp_agent_project: tuple[ProjectEntry, Path],
) -> None:
    """AGN-012: pick → release → verify todo + lock released."""
    entry, config_dir = mcp_agent_project
    pid, plid, sid = entry._seed_ids  # type: ignore[attr-defined]

    repo = Path(entry.path)
    db_url = f"sqlite:///{repo / '.cod-doc' / 'state.db'}"
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        _seed_task(
            session,
            pid,
            plid,
            sid,
            "AGN-012B",
            title="Release me",
            priority="high",
        )
    engine.dispose()

    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # Pick the task.
        pick_result = await session.call_tool(
            "agent_pick",
            {"project": "agn-test", "agent_id": "agn-012-rel-agent"},
        )
        pick_text = pick_result.content[0].text
        assert '"AGN-012B"' in pick_text

        # Release the task (give up without done).
        release_result = await session.call_tool(
            "agent_release",
            {
                "project": "agn-test",
                "task_id": "AGN-012B",
                "agent_id": "agn-012-rel-agent",
                "reason": "Not ready yet",
            },
        )
        release_text = release_result.content[0].text

    assert '"ok": true' in release_text or '"ok": True' in release_text
    assert '"AGN-012B"' in release_text
    assert '"todo"' in release_text

    # Verify DB: task status is "todo", checked_out_by is None.
    engine2 = make_engine(db_url)
    factory2 = make_session_factory(engine2)
    with transactional(factory2) as session:
        from sqlalchemy import select

        row = session.execute(select(TaskModel).where(TaskModel.task_id == "AGN-012B")).scalar_one()
        assert row.status == "todo", f"Expected todo, got {row.status}"
        assert row.checked_out_by is None, "Lock should be released after release"
    engine2.dispose()


# --------------------------------------------------------------------------- #
# AGN-013: agent_get + agent_report smoke                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_agn013_agent_get_unknown_what(
    mcp_agent_project: tuple[ProjectEntry, Path],
) -> None:
    """AGN-013: agent_get with unknown ``what`` returns legal_what hint."""
    entry, config_dir = mcp_agent_project
    pid, plid, sid = entry._seed_ids  # type: ignore[attr-defined]

    repo = Path(entry.path)
    db_url = f"sqlite:///{repo / '.cod-doc' / 'state.db'}"
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        _seed_task(session, pid, plid, sid, "AGN-013", title="Get test")
    engine.dispose()

    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # agent_get with unknown "what" → legal_what hint.
        result = await session.call_tool(
            "agent_get",
            {"project": "agn-test", "task_id": "AGN-013", "what": "weird"},
        )
        text = result.content[0].text

    assert '"found": false' in text or '"found": False' in text
    assert '"legal_what"' in text
    assert '"full_doc_body"' in text
    assert '"related_task"' in text
    assert '"story_full"' in text
    assert '"plan_export"' in text
    assert '"file_content"' in text


@pytest.mark.anyio
async def test_agn013_agent_report_progress(
    mcp_agent_project: tuple[ProjectEntry, Path],
) -> None:
    """AGN-013: agent_report kind='progress' returns ok=True."""
    entry, config_dir = mcp_agent_project
    pid, plid, sid = entry._seed_ids  # type: ignore[attr-defined]

    repo = Path(entry.path)
    db_url = f"sqlite:///{repo / '.cod-doc' / 'state.db'}"
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        _seed_task(session, pid, plid, sid, "AGN-013B", title="Report test")
    engine.dispose()

    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        # agent_report kind='progress' → ok=True.
        result = await session.call_tool(
            "agent_report",
            {
                "project": "agn-test",
                "task_id": "AGN-013B",
                "kind": "progress",
                "message": "Making good progress on this task",
                "agent_id": "agn-013-agent",
            },
        )
        text = result.content[0].text

    assert '"ok": true' in text or '"ok": True' in text
    assert '"progress"' in text
    assert '"AGN-013B"' in text
    assert '"next_actions"' in text
