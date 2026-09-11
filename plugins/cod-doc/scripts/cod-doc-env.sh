#!/usr/bin/env bash
# Environment resolver for plugin hooks and the MCP launcher.
#
# Source: `. "$(dirname "$0")/cod-doc-env.sh"`
# After sourcing, these may be empty — the caller must check:
#
#   COD_DOC_BIN   — path to the `cod-doc` CLI
#   COD_DOC_ROOT  — project root that holds .cod-doc/state.db
#   COD_DOC_SLUG  — project slug in the DB (argument for `-p`)
#
# Prints nothing and always returns 0: a hook that fails the session
# because the project is not wired is worse than a missing hook.

# GUI hosts (Cursor) spawn with a stripped PATH. Re-add locations where
# pip/uv/homebrew typically put the CLI, without overriding an explicit PATH.
PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH

# --- project root -------------------------------------------------------
# Walk up from the session directory looking for .cod-doc/state.db.
# A git worktree has no such directory (.cod-doc is unversioned) — then
# take the primary checkout from `git worktree list`.
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
	# Agent Plugins spawn with cwd = plugin root; the user project is in
	# host-specific env vars, not $PWD.
	for _cd_start in \
		"${CLAUDE_PROJECT_DIR:-}" \
		"${CURSOR_PROJECT_DIR:-}" \
		"${PWD}"; do
		[ -n "$_cd_start" ] || continue
		COD_DOC_ROOT="$(_cd_find_root "$_cd_start" || true)"
		[ -n "$COD_DOC_ROOT" ] && break
	done
	unset _cd_start
fi
if [ -z "$COD_DOC_ROOT" ] && command -v git >/dev/null 2>&1; then
	_cd_git_start="${CLAUDE_PROJECT_DIR:-${CURSOR_PROJECT_DIR:-$PWD}}"
	_cd_main="$(git -C "$_cd_git_start" worktree list 2>/dev/null | head -1 | awk '{print $1}')"
	if [ -n "$_cd_main" ] && [ -f "$_cd_main/.cod-doc/state.db" ]; then
		COD_DOC_ROOT="$_cd_main"
	fi
	unset _cd_main _cd_git_start
fi

# --- binary -------------------------------------------------------------
_cd_pick_bin() {
	local candidate
	for candidate in "$@"; do
		if [ -n "$candidate" ] && [ -x "$candidate" ]; then
			printf '%s' "$candidate"
			return 0
		fi
	done
	return 1
}

if [ -n "${COD_DOC_BIN:-}" ] && [ -x "${COD_DOC_BIN}" ]; then
	: # set from outside, keep it
else
	COD_DOC_BIN="$(_cd_pick_bin \
		"${COD_DOC_ROOT:+$COD_DOC_ROOT/.venv/bin/cod-doc}" \
		"${CLAUDE_PROJECT_DIR:+$CLAUDE_PROJECT_DIR/.venv/bin/cod-doc}" \
		"${CURSOR_PROJECT_DIR:+$CURSOR_PROJECT_DIR/.venv/bin/cod-doc}" \
		"${PWD}/.venv/bin/cod-doc" \
		"$HOME/.local/bin/cod-doc" \
		"$HOME/.local/share/uv/tools/cod-doc/bin/cod-doc" \
		"/opt/homebrew/bin/cod-doc" \
		"/usr/local/bin/cod-doc" \
		|| true)"
	if [ -z "$COD_DOC_BIN" ] && command -v cod-doc >/dev/null 2>&1; then
		COD_DOC_BIN="$(command -v cod-doc)"
	fi
	COD_DOC_BIN="${COD_DOC_BIN:-}"
fi

# --- slug ---------------------------------------------------------------
# COD_DOC_PROJECT (env) → project row whose root_path matches → first row.
# Embedded DBs usually have one project; migrated DBs can have ghosts, so
# match the path first.
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
