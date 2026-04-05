#!/usr/bin/env bash
# Установить COD-DOC git-хуки в проект
# Использование: bash /path/to/cod-doc/hooks/install.sh [--remove]
set -euo pipefail
HOOKS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DST="$REPO_ROOT/.git/hooks"
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

install() {
  for hook in pre-commit post-merge; do
    src="$HOOKS_SRC/$hook"; dst="$HOOKS_DST/$hook"
    [[ -f "$dst" ]] && ! diff -q "$src" "$dst" &>/dev/null && cp "$dst" "$dst.bak" && echo -e "${YELLOW}⚠️  $hook.bak создан${NC}"
    cp "$src" "$dst" && chmod +x "$dst" && echo -e "${GREEN}✅ $hook установлен${NC}"
  done
}

remove() {
  for hook in pre-commit post-merge; do
    dst="$HOOKS_DST/$hook"
    [[ -f "$dst" ]] && rm "$dst" && echo -e "${GREEN}✅ $hook удалён${NC}"
    [[ -f "$dst.bak" ]] && mv "$dst.bak" "$dst" && echo -e "${YELLOW}↩️  $hook восстановлен из .bak${NC}"
  done
}

case "${1:-}" in --remove) remove ;; *) install ;; esac
