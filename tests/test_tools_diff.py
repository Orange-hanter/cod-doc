"""PCA-950: tools_diff(since=...) — change-log between snapshots."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from cod_doc.mcp.server import mcp


def _get_tool(name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def test_tools_diff_missing_snapshot_returns_hint(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Use a name that surely doesn't exist on disk.
    diff = _get_tool("tools_diff")
    result = diff(since="this-snapshot-does-not-exist-9999")
    assert result["snapshot_found"] is False
    assert "hint" in result


def test_tools_diff_against_fresh_snapshot_is_empty(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Take a snapshot of *current* catalog into a tmp dir, then patch the
    # tool's lookup path. (We monkeypatch via the env-aware approach by
    # writing into .cod-doc/tool_snapshots/ which is the default dir the
    # tool uses; cleanup after.)
    from scripts.snapshot_tools import take_snapshot

    snap_dir = Path(__file__).resolve().parents[1] / ".cod-doc" / "tool_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / "test-fresh.json"
    try:
        take_snapshot(name="test-fresh", out_dir=snap_dir)
        diff = _get_tool("tools_diff")
        result = diff(since="test-fresh")
        assert result["snapshot_found"] is True
        assert result["added"] == []
        assert result["removed"] == []
        # `changed` may not be strictly empty because descriptions are
        # rebuilt on every call; we expect zero structural changes.
        for ch in result["changed"]:
            assert ch["fields_added"] == [], ch
            assert ch["fields_removed"] == [], ch
    finally:
        if snap_path.exists():
            snap_path.unlink()


def test_tools_diff_against_synthetic_old_snapshot(tmp_path: Path) -> None:
    """A synthetic snapshot with a fake removed tool + missing one of ours
    should produce both 'added' (our real one) and 'removed' (the fake)."""
    snap_dir = Path(__file__).resolve().parents[1] / ".cod-doc" / "tool_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / "test-synthetic.json"

    # Build a minimal synthetic snapshot containing one fictional tool
    # and missing `capabilities` (which is real).
    synthetic = {
        "snapshot_name": "test-synthetic",
        "created_utc": "2020-01-01T00:00:00+00:00",
        "tool_count": 1,
        "tools": [
            {
                "name": "ghost_tool_removed",
                "description": "no longer exists",
                "input_schema": {"properties": {}, "required": []},
            }
        ],
    }
    snap_path.write_text(json.dumps(synthetic), encoding="utf-8")
    try:
        diff = _get_tool("tools_diff")
        result = diff(since="test-synthetic")
        assert result["snapshot_found"] is True
        assert "ghost_tool_removed" in result["removed"]
        # Many real tools are "added" relative to the synthetic single-tool snapshot.
        assert "capabilities" in result["added"]
    finally:
        snap_path.unlink(missing_ok=True)
