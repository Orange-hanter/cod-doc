"""CLO-016/026: MCP ``sync_status``, ``sync_pull``, ``sync_push``, ``sync_conflicts``,
``sync_resolve`` (RFC 29 §10.7).

Acceptance: «Тулы по RFC 29 §10.7 на профилях standard/full, не в agent;
ошибки — ValueError с префиксом SYNC-NNN».

Ответ каждого тула прогоняется через ``json.dumps``: тул ходит по транспорту,
и ``datetime`` или dataclass в полезной нагрузке убил бы вызов у клиента.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.mcp.profiles import keep_tool
from cod_doc.mcp.server import mcp

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "synctool"
_TOOLS = ("sync_status", "sync_pull", "sync_push", "sync_conflicts", "sync_resolve")


def _call(name: str, **kwargs: Any) -> dict[str, Any]:
    result = mcp._tool_manager._tools[name].fn(**kwargs)
    json.dumps(result)
    assert isinstance(result, dict)
    return result


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Path:
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output
    remote = tmp_path / "remote"
    remote.mkdir()
    return remote


def test_tools_registered_on_standard_and_full_only() -> None:
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    for tool in _TOOLS:
        assert tool in names
        assert keep_tool(tool, "standard")
        assert keep_tool(tool, "full")
        assert not keep_tool(tool, "agent")
        assert not keep_tool(tool, "minimal")


@pytest.mark.parametrize("tool", ["sync_pull", "sync_push"])
def test_unconfigured_remote_is_value_error_with_code(project: Path, tool: str) -> None:
    with pytest.raises(ValueError, match=r"^SYNC-110: remote 'origin'"):
        _call(tool, project=_PROJECT)


def test_status_without_remote_is_local(project: Path) -> None:
    st = _call("sync_status", project=_PROJECT, verify=True)
    assert st["remote"] is None
    assert st["verify"] is None
    assert st["pending_ops"] > 0  # project add оставил хвост: проект, рутины, дерево


def test_push_then_status_and_pull(project: Path) -> None:
    code = (
        CliRunner()
        .invoke(main, ["remote", "add", "origin", project.as_uri(), "-p", _PROJECT])
        .exit_code
    )
    assert code == 0

    pushed = _call("sync_push", project=_PROJECT)
    assert pushed["ops"] > 0
    assert pushed["changesets"] == [pushed["head"]]

    st = _call("sync_status", project=_PROJECT, verify=True)
    assert (st["remote"], st["head_local"], st["head_remote"]) == (
        "origin",
        pushed["head"],
        pushed["head"],
    )
    assert (st["ahead"], st["behind"], st["pending_ops"]) == (0, 0, 0)
    assert st["verify"]["equal"] is True

    pulled = _call("sync_pull", project=_PROJECT, dry_run=True)
    assert pulled == {
        "applied": [],
        "ops": 0,
        "fast_forward": True,
        "conflicts": [],
        "aliases": [],
        "head": pushed["head"],
        "rebased": None,
    }


def test_sync_errors_without_code_prefix_are_still_value_errors(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Граница секции B (MergeRequiredError) приходит клиенту ValueError'ом."""
    from cod_doc.services.sync_service import pull as pull_mod
    from cod_doc.services.sync_service.errors import MergeRequiredError

    def diverged(*_a: object, **_k: object) -> None:
        raise MergeRequiredError("история разошлась")

    monkeypatch.setattr(pull_mod, "pull", diverged)
    with pytest.raises(ValueError, match="секция C"):
        _call("sync_pull", project=_PROJECT)


def test_status_counts_restricted_rows(project: Path) -> None:
    from cod_doc.mcp.tools._db import session_factory
    from tests.cli.test_cmd_sync import _seed_restricted_doc

    sf, _entry = session_factory(_PROJECT)
    _seed_restricted_doc(sf, _PROJECT)

    st = _call("sync_status", project=_PROJECT)
    assert (st["restricted_local"], st["restricted_rows"]) == (1, 3)
