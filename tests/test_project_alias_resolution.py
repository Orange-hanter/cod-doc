"""PCA-934: legacy MCP-тулы принимают ``project`` (canonical) + ``project_name``
(deprecated alias).

Покрывает:
1. resolve_project_name() — основной helper.
2. Каждый legacy-тул через end-to-end MCP-клиент: один тул с обоими
   именами параметров возвращает одинаковый результат.
3. JSON schema: новые параметры project + project_name присутствуют у
   каждого legacy-тула.
"""

from __future__ import annotations

import asyncio
import os
import warnings
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

import cod_doc.config as config_module
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.mcp.server import mcp
from cod_doc.mcp.tools._legacy import resolve_project_name

# --------------------------------------------------------------------------- #
# Unit tests for the resolver                                                  #
# --------------------------------------------------------------------------- #


def test_resolver_prefers_project_over_project_name() -> None:
    assert resolve_project_name("foo", None, "t") == "foo"


def test_resolver_accepts_project_name_alone() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = resolve_project_name(None, "legacy-x", "t")
    assert out == "legacy-x"
    assert any(
        issubclass(w.category, DeprecationWarning) and "project_name" in str(w.message)
        for w in caught
    ), f"expected DeprecationWarning, got: {[(w.category, w.message) for w in caught]}"


def test_resolver_accepts_matching_pair_silently() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = resolve_project_name("foo", "foo", "t")
    assert out == "foo"
    # Same value passed twice — no deprecation noise.
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]


def test_resolver_rejects_disagreeing_pair() -> None:
    with pytest.raises(ValueError, match=r"disagree"):
        resolve_project_name("foo", "bar", "t")


def test_resolver_rejects_neither() -> None:
    with pytest.raises(ValueError, match=r"required"):
        resolve_project_name(None, None, "t")
    with pytest.raises(ValueError, match=r"required"):
        resolve_project_name("", "", "t")


# --------------------------------------------------------------------------- #
# JSON-schema invariant: every legacy tool exposes both names.                  #
# --------------------------------------------------------------------------- #

# Tools that previously required project_name and now must accept both. Excludes:
#  - resources / prompts (URI-template arg name is part of the template).
#  - tools without any project arg (list_projects, add_project, check_config).
LEGACY_TOOLS_WITH_BOTH_ALIASES: tuple[str, ...] = (
    # legacy_master_tools
    "get_master",
    "update_master_hashes",
    "check_stale_refs",
    "generate_ref",
    "read_context",
    "read_file",
    "list_files",
    "hash_file",
    "verify_hash",
    # legacy_project_tools
    "get_project_status",
    "remove_project",
    "list_tasks",
    "add_task",
    "update_task",
    "next_pending_task",
    # legacy_agent_tools
    "run_agent_once",
    "get_agent_context",
    "clear_agent_context",
    # legacy_search_tools
    "search_docs",
    "reindex",
)


@pytest.mark.parametrize("tool_name", LEGACY_TOOLS_WITH_BOTH_ALIASES)
def test_legacy_tool_schema_exposes_both_aliases(tool_name: str) -> None:
    tools = asyncio.run(mcp.list_tools())
    by_name = {t.name: t for t in tools}
    assert tool_name in by_name, f"tool {tool_name!r} not registered"
    props = by_name[tool_name].inputSchema.get("properties", {})
    required = set(by_name[tool_name].inputSchema.get("required", []))

    assert "project" in props, f"{tool_name}: schema missing `project` property"
    assert "project_name" in props, (
        f"{tool_name}: schema missing `project_name` legacy alias property"
    )
    # Neither should be in `required` — they're mutually-exclusive optionals.
    assert "project" not in required and "project_name" not in required, (
        f"{tool_name}: project/project_name must be optional in schema "
        f"(resolved at runtime). required={sorted(required)}"
    )


# --------------------------------------------------------------------------- #
# End-to-end: a legacy tool called with `project=` works without warning,      #
# called with `project_name=` works but emits the alias log line.              #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mcp_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ProjectEntry, Path]:
    config_dir = tmp_path / ".cod-doc-home"
    config_file = config_dir / "config.yaml"
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    repo = tmp_path / "alias-repo"
    repo.mkdir()
    entry = ProjectEntry(name="alias-test", path=str(repo))
    Project(entry).init()
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://example.com")
    cfg.projects = [entry.model_dump()]
    cfg.save()
    return entry, config_dir


def _open_stdio_client(config_dir: Path):
    params = StdioServerParameters(
        command=str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"),
        # Need legacy `get_master` exposed → --profile full.
        args=["-m", "cod_doc.mcp.server", "--transport", "stdio", "--profile", "full"],
        env={**os.environ, "COD_DOC_HOME": str(config_dir)},
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    return stdio_client(params)


@pytest.mark.anyio
async def test_get_master_accepts_canonical_project(
    mcp_project: tuple[ProjectEntry, Path],
) -> None:
    entry, config_dir = mcp_project
    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("get_master", {"project": entry.name})

    # Один из текстовых блоков должен содержать MASTER.md заголовок.
    assert result.content, "get_master returned no content"
    assert not result.isError, f"get_master errored on project=: {result.content}"


@pytest.mark.anyio
async def test_get_master_accepts_legacy_project_name(
    mcp_project: tuple[ProjectEntry, Path],
) -> None:
    entry, config_dir = mcp_project
    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("get_master", {"project_name": entry.name})

    assert result.content, "get_master returned no content for project_name="
    assert not result.isError, (
        f"get_master errored on project_name=: {result.content}"
    )


@pytest.mark.anyio
async def test_legacy_tool_rejects_missing_project(
    mcp_project: tuple[ProjectEntry, Path],
) -> None:
    _, config_dir = mcp_project
    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("get_master", {})

    assert result.isError, "expected error when neither project nor project_name passed"
