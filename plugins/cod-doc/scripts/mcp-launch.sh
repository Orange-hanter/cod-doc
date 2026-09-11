#!/usr/bin/env bash
# Claude Code plugin launcher only. Cursor and other hosts must spawn
# `cod-doc-mcp` via `cod-doc connect install` (absolute command in the
# host config). Do not paste this script into a JSON `bash -c` string.
#
# Binary lookup matches the CLI (see cod-doc-env.sh), plus COD_DOC_MCP_BIN.
# Profile is COD_DOC_PROFILE, default `standard` (110 CRUD): the server's
# own default `agent` exposes a 6-tool surface and does not cover
# doc/plan/link work.
set -euo pipefail

# shellcheck source=./cod-doc-env.sh
. "$(dirname "$0")/cod-doc-env.sh"

bin=""
if [ -n "${COD_DOC_MCP_BIN:-}" ] && [ -x "${COD_DOC_MCP_BIN}" ]; then
	bin="$COD_DOC_MCP_BIN"
elif [ -n "${COD_DOC_BIN:-}" ] && [ -x "${COD_DOC_BIN}-mcp" ]; then
	bin="${COD_DOC_BIN}-mcp"
else
	for candidate in \
		"${COD_DOC_ROOT:+$COD_DOC_ROOT/.venv/bin/cod-doc-mcp}" \
		"$HOME/.local/bin/cod-doc-mcp" \
		"$HOME/.local/share/uv/tools/cod-doc/bin/cod-doc-mcp" \
		"/opt/homebrew/bin/cod-doc-mcp" \
		"/usr/local/bin/cod-doc-mcp"; do
		if [ -n "${candidate:-}" ] && [ -x "$candidate" ]; then
			bin="$candidate"
			break
		fi
	done
fi
if [ -z "$bin" ] && command -v cod-doc-mcp >/dev/null 2>&1; then
	bin="$(command -v cod-doc-mcp)"
fi

if [ -z "$bin" ]; then
	echo "cod-doc plugin: cod-doc-mcp not found." >&2
	echo "  Install the package (pip install -e '.[dev]' in the cod-doc repo)" >&2
	echo "  or set the path explicitly: COD_DOC_MCP_BIN=/path/to/cod-doc-mcp" >&2
	exit 1
fi

exec "$bin" --profile "${COD_DOC_PROFILE:-standard}"
