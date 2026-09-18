#!/usr/bin/env bash
# Поставить zsh-completion cod-doc в $fpath пользователя.
#
#   scripts/install-zsh-completion.sh [каталог]
#
# По умолчанию — ~/.oh-my-zsh/custom/completions: oh-my-zsh держит его в
# $fpath безусловно, даже когда каталога ещё нет, так что править .zshrc не
# требуется. Без omz — ~/.zsh/completions (его в fpath надо добавить руками,
# скрипт об этом скажет).
#
# Ставим СИМЛИНК, а не копию: `cod-doc` установлен editable-режимом и смотрит
# в этот же чекаут, поэтому симлинк держит completion в ногу с CLI после
# обычного `git pull`, без повторного запуска установки.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo_root/cod_doc/cli/completion/_cod-doc"

if [[ ! -f $src ]]; then
  echo "нет артефакта: $src" >&2
  echo "собери: python -m cod_doc.cli.completion --write" >&2
  exit 1
fi

target="${1:-}"
if [[ -z $target ]]; then
  omz="${ZSH:-$HOME/.oh-my-zsh}"
  if [[ -d $omz ]]; then
    target="$omz/custom/completions"
  else
    target="$HOME/.zsh/completions"
  fi
fi

mkdir -p "$target"
ln -sfn "$src" "$target/_cod-doc"
echo "✅ $target/_cod-doc -> $src"

# compinit сверяет строку «#files: N» в дампе с числом _*-файлов в $fpath и
# обычно пересобирается сам. Но это СЧЁТЧИК, а не список: если в том же окне
# исчез другой completion, суммы совпадут и новый файл будет молча
# проигнорирован. Поэтому сносим явно.
#
# Заодно убираем протухший .lock — это КАТАЛОГ, и пока он существует,
# `mkdir "$ZSH_COMPDUMP.lock"` в oh-my-zsh.sh падает при каждом старте,
# а zrecompile не запускается вообще никогда.
rm -rf "$HOME"/.zcompdump* 2>/dev/null || true
echo "🧹 снесены ~/.zcompdump* (включая .lock-каталог и .zwc)"

if ! zsh -ic 'print -l $fpath' 2>/dev/null | grep -qxF "$target"; then
  echo "⚠️  $target не в \$fpath. Добавь в ~/.zshrc ПЕРЕД source oh-my-zsh.sh:"
  echo "        fpath=($target \$fpath)"
fi

# omz зовёт `compinit -i`, а тот молча выбрасывает «небезопасные»
# (group/world-writable) каталоги — без единого сообщения.
if command -v zsh >/dev/null && zsh -c 'autoload -U compaudit; compaudit' 2>/dev/null \
    | grep -qF "$target"; then
  echo "⚠️  compaudit считает $target небезопасным — completion будет проигнорирован."
  echo "        chmod g-w,o-w '$target' '$repo_root'"
fi

echo
echo "Перезапусти shell:  exec zsh"
echo "Проверка:           cod-doc task show -p <TAB>"
