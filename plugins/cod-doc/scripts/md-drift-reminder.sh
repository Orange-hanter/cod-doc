#!/usr/bin/env bash
# PostToolUse(Edit|Write|MultiEdit): напоминание про синхронизацию БД после
# правки markdown, который зарегистрирован в cod-doc.
#
# В cod-doc markdown — проекция, source of truth — БД (.cod-doc/state.db).
# Правка tracked-документа на диске без `doc import` даёт drift
# (edited_in_place). Хук ТОЛЬКО напоминает — молчаливых записей в БД из хука
# быть не должно.
#
# Отличие от репо-локального предшественника: файл проверяется по таблице
# `document`, поэтому untracked-markdown (README форка, черновики, чужие
# каталоги) не порождает шум.

_HOOK_JSON="$(cat)"
export _HOOK_JSON

# shellcheck source=./cod-doc-env.sh
. "$(dirname "$0")/cod-doc-env.sh"

[ -n "$COD_DOC_ROOT" ] || exit 0
[ -n "$COD_DOC_SLUG" ] || exit 0
command -v sqlite3 >/dev/null 2>&1 || exit 0

# JSON хука пришёл на stdin и уже лежит в _HOOK_JSON — парсим питоном, а не
# регуляркой: в tool_input встречается несколько путей, нужен именно file_path.
path="$(python3 -c 'import json,os,sys
try:
    data = json.loads(os.environ.get("_HOOK_JSON") or "{}")
except Exception:
    sys.exit(0)
print((data.get("tool_input") or {}).get("file_path", ""))' 2>/dev/null)"
case "$path" in
*.md) ;;
*) exit 0 ;;
esac

# Путь в БД хранится относительно корня проекта.
rel="${path#"$COD_DOC_ROOT"/}"
[ "$rel" != "$path" ] || exit 0

# Исключения проверяем по ОТНОСИТЕЛЬНОМУ пути: сам проект может лежать внутри
# каталога с именем .claude (worktree, job-каталог), и абсолютная проверка
# глушила бы весь проект целиком.
case "$rel" in
.claude/* | .codex/* | node_modules/* | */node_modules/*) exit 0 ;;
esac

esc="${rel//\'/\'\'}"
tracked="$(sqlite3 -readonly "$COD_DOC_ROOT/.cod-doc/state.db" \
	"select doc_key from document where path = '$esc' limit 1" 2>/dev/null)"
[ -n "$tracked" ] || exit 0

bin="${COD_DOC_BIN:-cod-doc}"
msg="cod-doc drift: правлен tracked-документ ${rel} (doc_key ${tracked}). Синхронизируй БД: ${bin} doc import '${rel}' -p ${COD_DOC_SLUG}"

# Реестр хэшей корневого MASTER.md: если файл в нём — нужен ещё пересчёт.
if [ -f "$COD_DOC_ROOT/MASTER.md" ] && grep -qF "$rel" "$COD_DOC_ROOT/MASTER.md" 2>/dev/null; then
	msg="${msg} ; файл упомянут в MASTER.md — если он в hash-реестре, ещё и ${bin} hash update"
fi

printf '%s ; проверка: %s doc drift -p %s --all\n' "$msg" "$bin" "$COD_DOC_SLUG"
exit 0
