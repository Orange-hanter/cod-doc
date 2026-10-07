#!/usr/bin/env bash
# DEBT-001: пересчитать производные артефакты, которые раньше правились руками.
#
#   scripts/regen.sh        # stdout — пути изменённых файлов, по одному на строку
#
#   - числа тулов MCP: докстринг cod_doc/mcp/profiles.py и docs/mcp-integration.md
#     (python -m cod_doc.mcp.profile_counts --write);
#   - zsh-дополнение cod_doc/cli/completion/_cod-doc
#     (python -m cod_doc.cli.completion --write).
#
# Зовёт его pre-commit (hooks/pre-commit) и добавляет напечатанное в коммит.
# Чего автомат не решит сам (тул не вписан ни в одно семейство таблицы
# каталога) — уходит в stderr; красным это станет в тестах и CI, а не здесь.
# Нет .venv — предупреждение и выход 0: хук не должен блокировать коммит
# из-за окружения (в свежем worktree .venv подкладывают симлинком).
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "regen: нет $PY — производные артефакты не пересчитаны" >&2
  exit 0
fi

cd "$REPO_ROOT" || exit 0

"$PY" -m cod_doc.mcp.profile_counts --write

if ! "$PY" -m cod_doc.cli.completion --check >/dev/null 2>&1; then
  "$PY" -m cod_doc.cli.completion --write >/dev/null && echo "cod_doc/cli/completion/_cod-doc"
fi
exit 0
