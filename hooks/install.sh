#!/usr/bin/env bash
# Установить COD-DOC git-хуки в проект
# Использование: bash /path/to/cod-doc/hooks/install.sh [--remove]
set -euo pipefail
HOOKS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# --git-common-dir, а не "$REPO_ROOT/.git": в worktree `.git` — файл-указатель,
# и путь `<worktree>/.git/hooks` не существует. Хуки у всех worktree общие и
# лежат в каталоге основного чекаута, поэтому ставить надо именно туда —
# тогда установка из worktree работает и накрывает все деревья сразу.
HOOKS_DST="$(cd "$(git rev-parse --git-common-dir)" && pwd)/hooks"
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

# DEBT-001: post-merge снят — он пересчитывал хэши реестра MASTER.md и
# автокоммитил их с --no-verify, а хэши в ссылках больше не хранятся. Старую
# копию из .git/hooks убираем, только если она наша (по шапке), чужой хук
# не трогаем.
drop_retired_post_merge() {
  local dst="$HOOKS_DST/post-merge"
  if [[ -f "$dst" ]] && grep -q '^# COD-DOC post-merge hook' "$dst"; then
    rm "$dst" && echo -e "${YELLOW}🗑  post-merge снят (DEBT-001)${NC}"
  fi
}

install() {
  for hook in pre-commit pre-push; do
    src="$HOOKS_SRC/$hook"; dst="$HOOKS_DST/$hook"
    [[ -f "$dst" ]] && ! diff -q "$src" "$dst" &>/dev/null && cp "$dst" "$dst.bak" && echo -e "${YELLOW}⚠️  $hook.bak создан${NC}"
    cp "$src" "$dst" && chmod +x "$dst" && echo -e "${GREEN}✅ $hook установлен${NC}"
  done
  drop_retired_post_merge
}

remove() {
  drop_retired_post_merge
  for hook in pre-commit pre-push; do
    dst="$HOOKS_DST/$hook"
    [[ -f "$dst" ]] && rm "$dst" && echo -e "${GREEN}✅ $hook удалён${NC}"
    [[ -f "$dst.bak" ]] && mv "$dst.bak" "$dst" && echo -e "${YELLOW}↩️  $hook восстановлен из .bak${NC}"
  done
}

case "${1:-}" in --remove) remove ;; *) install ;; esac
