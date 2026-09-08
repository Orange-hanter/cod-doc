#!/usr/bin/env bash
# SessionStart: короткая сводка cod-doc в контекст сессии.
#
# ВЫКЛЮЧЕН ПО УМОЛЧАНИЮ. Включение — явное, переменной окружения:
#     "env": { "COD_DOC_SESSION_BRIEF": "1" }   в .claude/settings.json
# Причина: контекст не должен наполняться сам по себе; кто хочет brief —
# включает его руками для конкретного проекта.
#
# Печатает только то, что требует реакции: задачи, зависшие в in_progress
# (незакрытый checkout прошлой сессии). Тишина — нормальный исход.

[ "${COD_DOC_SESSION_BRIEF:-0}" = "1" ] || exit 0

# shellcheck source=./cod-doc-env.sh
. "$(dirname "$0")/cod-doc-env.sh"

[ -n "$COD_DOC_ROOT" ] || exit 0
[ -n "$COD_DOC_SLUG" ] || exit 0
command -v sqlite3 >/dev/null 2>&1 || exit 0

# Читаем БД напрямую: SessionStart-хук должен стоить миллисекунды, а не
# полсекунды на импорт питоновского пакета.
esc="${COD_DOC_SLUG//\'/\'\'}"
open="$(sqlite3 -readonly "$COD_DOC_ROOT/.cod-doc/state.db" "
	select group_concat(
	           t.task_id || ' «' || t.title || '»' ||
	           coalesce(' [' || t.checked_out_by || ']', ''),
	           '; ')
	  from task t
	  join project p on p.row_id = t.project_id
	 where p.slug = '$esc'
	   and t.status in ('in_progress', 'in-progress')" 2>/dev/null)"

[ -n "$open" ] || exit 0

printf 'cod-doc [%s]: в работе с прошлой сессии — %s. Продолжай или отпусти (task_release).\n' \
	"$COD_DOC_SLUG" "$open"
exit 0
