#!/usr/bin/env bash
# Запуск MCP-сервера cod-doc для плагина.
#
# Бинарь ищется в том же порядке, что и CLI (см. cod-doc-env.sh), плюс
# отдельный override COD_DOC_MCP_BIN. Профиль — COD_DOC_PROFILE, дефолт
# `standard` (108 тулов): дефолтный профиль сервера `agent` даёт 6 тулов и
# не покрывает doc/plan/link-работу, ради которой плагин и ставят.
set -euo pipefail

# ADO-146: до exec любой выход — это тихая смерть, и клиент увидит только
# «Connection closed» без причины. Ловим её и печатаем в stderr: там её
# подберёт mcp-logs Claude Code. Снимаем ловушку прямо перед exec, чтобы
# штатный запуск сервера не выглядел как сбой.
_cd_die() {
	local rc=$?
	[ "$rc" -eq 0 ] && return 0
	echo "cod-doc plugin: лаунчер вышел с кодом $rc, не дойдя до запуска сервера." >&2
	echo "  cwd=$PWD COD_DOC_ROOT=${COD_DOC_ROOT:-<пусто>} COD_DOC_BIN=${COD_DOC_BIN:-<пусто>}" >&2
}
trap _cd_die EXIT

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

trap - EXIT
exec "$bin" --profile "${COD_DOC_PROFILE:-standard}"
