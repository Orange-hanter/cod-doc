#!/usr/bin/env bash
# Запуск MCP-сервера cod-doc для плагина.
#
# Бинарь ищется в том же порядке, что и CLI (см. cod-doc-env.sh), плюс
# отдельный override COD_DOC_MCP_BIN. Профиль — COD_DOC_PROFILE, дефолт
# `standard` (108 тулов): дефолтный профиль сервера `agent` даёт 6 тулов и
# не покрывает doc/plan/link-работу, ради которой плагин и ставят.
set -euo pipefail

# shellcheck source=./cod-doc-env.sh
. "$(dirname "$0")/cod-doc-env.sh"

bin=""
if [ -n "${COD_DOC_MCP_BIN:-}" ] && [ -x "${COD_DOC_MCP_BIN}" ]; then
	bin="$COD_DOC_MCP_BIN"
elif [ -n "$COD_DOC_BIN" ] && [ -x "${COD_DOC_BIN}-mcp" ]; then
	bin="${COD_DOC_BIN}-mcp"
elif command -v cod-doc-mcp >/dev/null 2>&1; then
	bin="$(command -v cod-doc-mcp)"
fi

if [ -z "$bin" ]; then
	echo "cod-doc plugin: не найден cod-doc-mcp." >&2
	echo "  Поставь пакет (pip install -e '.[dev]' в репозитории cod-doc)" >&2
	echo "  или укажи путь явно: COD_DOC_MCP_BIN=/path/to/cod-doc-mcp" >&2
	exit 1
fi

exec "$bin" --profile "${COD_DOC_PROFILE:-standard}"
