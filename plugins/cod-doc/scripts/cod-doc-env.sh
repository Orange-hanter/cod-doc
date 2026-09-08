#!/usr/bin/env bash
# Резолвер окружения cod-doc для хуков и MCP-лаунчера плагина.
#
# Источник вызова: `source "${CLAUDE_PLUGIN_ROOT}/scripts/cod-doc-env.sh"`.
# После этого доступны (любая может быть пустой — вызывающий обязан проверить):
#
#   COD_DOC_BIN   — путь к CLI `cod-doc`
#   COD_DOC_ROOT  — корень проекта, где лежит .cod-doc/state.db
#   COD_DOC_SLUG  — слаг проекта в БД (аргумент для `-p`)
#
# Скрипт ничего не печатает и всегда возвращает 0: хук, который шумит или
# валит сессию из-за неподключённого проекта, хуже отсутствующего хука.

# --- корень проекта -----------------------------------------------------
# Ищем .cod-doc/state.db вверх по дереву от каталога сессии. В git-worktree
# такого каталога нет (.cod-doc не версионируется) — тогда берём корень
# основного чекаута из `git worktree list`.
_cd_find_root() {
	local d="${1:-$PWD}"
	while [ "$d" != "/" ] && [ -n "$d" ]; do
		if [ -f "$d/.cod-doc/state.db" ]; then
			printf '%s' "$d"
			return 0
		fi
		d="$(dirname "$d")"
	done
	return 1
}

COD_DOC_ROOT="${COD_DOC_ROOT:-}"
if [ -z "$COD_DOC_ROOT" ]; then
	COD_DOC_ROOT="$(_cd_find_root "${CLAUDE_PROJECT_DIR:-$PWD}" || true)"
fi
if [ -z "$COD_DOC_ROOT" ] && command -v git >/dev/null 2>&1; then
	# `|| true` обязателен, как и строкой выше у _cd_find_root. Вне
	# git-репозитория `git worktree list` выходит с 128; `2>/dev/null`
	# прячет только текст, а не код возврата. У вызывающего
	# (mcp-launch.sh) стоит `set -euo pipefail`, поэтому pipefail
	# протаскивает 128 в присваивание, а set -e убивает скрипт — молча,
	# ещё до exec. Клиент видел «Connection closed» без единой строки в
	# логе: Python не успевал запуститься. См. ADO-146.
	_cd_main="$(git -C "${CLAUDE_PROJECT_DIR:-$PWD}" worktree list 2>/dev/null | head -1 | awk '{print $1}' || true)"
	if [ -n "$_cd_main" ] && [ -f "$_cd_main/.cod-doc/state.db" ]; then
		COD_DOC_ROOT="$_cd_main"
	fi
	unset _cd_main
fi

# --- бинарь -------------------------------------------------------------
if [ -n "${COD_DOC_BIN:-}" ] && [ -x "${COD_DOC_BIN}" ]; then
	: # задан снаружи, уважаем
elif [ -n "$COD_DOC_ROOT" ] && [ -x "$COD_DOC_ROOT/.venv/bin/cod-doc" ]; then
	COD_DOC_BIN="$COD_DOC_ROOT/.venv/bin/cod-doc"
elif [ -x "${CLAUDE_PROJECT_DIR:-$PWD}/.venv/bin/cod-doc" ]; then
	COD_DOC_BIN="${CLAUDE_PROJECT_DIR:-$PWD}/.venv/bin/cod-doc"
elif command -v cod-doc >/dev/null 2>&1; then
	COD_DOC_BIN="$(command -v cod-doc)"
else
	COD_DOC_BIN=""
fi

# --- слаг ---------------------------------------------------------------
# COD_DOC_PROJECT (env) → строка project с совпавшим root_path → единственная
# строка project. В embedded-БД проектов обычно один, но в мигрировавших БД
# встречаются проекты-призраки — поэтому сперва точное совпадение пути.
COD_DOC_SLUG="${COD_DOC_PROJECT:-}"
if [ -z "$COD_DOC_SLUG" ] && [ -n "$COD_DOC_ROOT" ] && command -v sqlite3 >/dev/null 2>&1; then
	_cd_db="$COD_DOC_ROOT/.cod-doc/state.db"
	_cd_esc="${COD_DOC_ROOT//\'/\'\'}"
	COD_DOC_SLUG="$(sqlite3 -readonly "$_cd_db" \
		"select slug from project where root_path = '$_cd_esc' limit 1" 2>/dev/null)"
	if [ -z "$COD_DOC_SLUG" ]; then
		COD_DOC_SLUG="$(sqlite3 -readonly "$_cd_db" \
			"select slug from project order by row_id limit 1" 2>/dev/null)"
	fi
	unset _cd_db _cd_esc
fi

export COD_DOC_BIN COD_DOC_ROOT COD_DOC_SLUG
