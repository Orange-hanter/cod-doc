#!/usr/bin/env bash
# install_hooks.sh — Установить COD-DOC Git-хуки в текущий репозиторий.
#
# Использование:
#   bash scripts/install_hooks.sh           # Установить
#   bash scripts/install_hooks.sh --remove  # Удалить

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_SRC="$REPO_ROOT/scripts/hooks"
HOOKS_DST="$REPO_ROOT/.git/hooks"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
HOOK_NAMES=("pre-commit" "post-merge")

install() {
  echo "📦 Установка COD-DOC Git-хуков..."
  for hook in "${HOOK_NAMES[@]}"; do
    src="$HOOKS_SRC/$hook"
    dst="$HOOKS_DST/$hook"

    if [[ ! -f "$src" ]]; then
      echo -e "${RED}❌ Файл хука не найден: $src${NC}"
      exit 1
    fi

    # Создать резервную копию, если уже есть другой хук
    if [[ -f "$dst" ]] && ! diff -q "$src" "$dst" &>/dev/null; then
      cp "$dst" "$dst.bak"
      echo -e "${YELLOW}⚠️  Существующий $hook сохранён в $hook.bak${NC}"
    fi

    cp "$src" "$dst"
    chmod +x "$dst"
    echo -e "${GREEN}✅ $hook установлен → $dst${NC}"
  done
  echo -e "${GREEN}\n✅ Все хуки установлены. Готово.${NC}"
}

remove() {
  echo "🗑️  Удаление COD-DOC Git-хуков..."
  for hook in "${HOOK_NAMES[@]}"; do
    dst="$HOOKS_DST/$hook"
    if [[ -f "$dst" ]]; then
      rm "$dst"
      echo -e "${GREEN}✅ $hook удалён.${NC}"
      # Восстановить резервную копию
      if [[ -f "$dst.bak" ]]; then
        mv "$dst.bak" "$dst"
        echo -e "${YELLOW}↩️  Восстановлен $hook.bak${NC}"
      fi
    else
      echo -e "${YELLOW}⚠️  $hook не найден, пропуск.${NC}"
    fi
  done
}

case "${1:-}" in
  --remove) remove ;;
  *)        install ;;
esac
