"""PCA-950: snapshot the live MCP tool catalog to a JSON file.

Usage:
    python -m scripts.snapshot_tools                    # writes HEAD.json
    python -m scripts.snapshot_tools --name 1.2.0       # writes 1.2.0.json
    python -m scripts.snapshot_tools --dir custom/path  # alternate root

Snapshots are consumed by ``tools_diff(since=...)`` MCP tool. CI can call
this on every release-tag commit to maintain a versioned change log.

Each snapshot is a JSON object:

    {
      "snapshot_name": "HEAD" | "<tag>",
      "created_utc": "2026-05-14T...",
      "tool_count": 96,
      "tools": [
        {"name": "...", "description": "...", "input_schema": {...}},
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from cod_doc.mcp.server import mcp

DEFAULT_DIR = Path(__file__).resolve().parents[1] / ".cod-doc" / "tool_snapshots"


def take_snapshot(name: str = "HEAD", out_dir: Path = DEFAULT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tools = asyncio.run(mcp.list_tools())
    payload = {
        "snapshot_name": name,
        "created_utc": datetime.now(UTC).isoformat(),
        "tool_count": len(tools),
        "tools": [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in sorted(tools, key=lambda x: x.name)
        ],
    }
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="HEAD")
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    args = parser.parse_args()
    path = take_snapshot(name=args.name, out_dir=Path(args.dir))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
