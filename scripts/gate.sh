#!/usr/bin/env bash
# Полный CI-гейт одной командой — тихий на зелёном, подробный на красном.
#
# Зачем не четыре вызова подряд: успешный прогон печатает ~2400 точек pytest,
# сводку warnings и четыре «всё ок». Эта информация имеет нулевую энтропию —
# она ничего не сообщает, но занимает место и в терминале, и в контексте
# агента. Здесь весь вывод уходит в лог-файл, на stdout попадает одна строка;
# при падении печатается выжимка по каждому упавшему шагу и путь к логу.
#
# Шаги и их порядок повторяют .github/workflows/ci.yml — быстрые впереди.
# Гоняются ВСЕ четыре, даже если ранний упал: один прогон должен давать полную
# картину, иначе «починил ruff → узнал про mypy → починил → узнал про pytest»
# растягивается на три прогона по две минуты.
#
# Использование:
#   scripts/gate.sh          # весь гейт
#   scripts/gate.sh --log    # плюс путь к логу даже на зелёном
#
# Код возврата: 0 — все шаги зелёные, иначе число упавших шагов.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1

# rich красит вывод CLI внутри CliRunner, и строковые ассерты падают на
# ANSI-кодах — см. CLAUDE.md §«Конвенции».
unset FORCE_COLOR

# ADO-212: при push из worktree git отдаёт хуку GIT_DIR=<repo>/.git/worktrees/<имя>.
# Унаследовав его, git-фикстуры тестов (`git -C <tmp_path> init/add/commit`)
# работают не со своим временным каталогом, а с этим репозиторием: `init` пишет
# core.bare=true в общий конфиг, `commit` кладёт на ветку дерево из одних
# файлов фикстуры. Сбрасываем после `git rev-parse` выше — ему GIT_DIR как раз
# нужен, чтобы найти корень worktree. Тот же сброс стоит в tests/conftest.py:
# раннер — не единственный способ запустить pytest из хука.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX GIT_NAMESPACE

LOG_DIR="${TMPDIR:-/tmp}"
# X-ы обязаны быть в КОНЦЕ шаблона: BSD-mktemp (macOS) не умеет суффикс после
# них и падает с обманчивым «File exists». Без проверки дальше весь прогон
# пишется в пустое имя, и красный гейт печатает четыре пустых раздела.
LOG="$(mktemp "${LOG_DIR%/}/cod-doc-gate.XXXXXX")" || {
  echo "GATE НЕ ЗАПУЩЕН: не удалось создать лог в ${LOG_DIR}" >&2
  exit 127
}
SHOW_LOG_ON_GREEN=0
[[ "${1:-}" == "--log" ]] && SHOW_LOG_ON_GREEN=1

# Выжимка из лога упавшего шага: сколько строк печатать на stdout.
EXCERPT_LINES=60

FAILED=()
NOTES=()

# Бинарь из .venv, если он есть, иначе из PATH: на CI никакого .venv нет.
bin() {
  local name="$1"
  if [[ -x "$REPO_ROOT/.venv/bin/$name" ]]; then
    echo "$REPO_ROOT/.venv/bin/$name"
  elif command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
  else
    echo ""
  fi
}

run_step() {
  local label="$1"; shift
  local start elapsed status
  start=$SECONDS

  {
    echo "===== $label"
    echo "\$ $*"
  } >>"$LOG"

  "$@" >>"$LOG" 2>&1
  status=$?
  elapsed=$((SECONDS - start))

  echo "===== $label → exit $status (${elapsed}s)" >>"$LOG"
  [[ $status -ne 0 ]] && FAILED+=("$label")
  return $status
}

# --- шаг 4 собирается отдельно: набор флагов зависит от того, стоит ли xdist ---
pytest_args=(tests/ -q --tb=short --timeout=120)
PY="$(bin python)"
if [[ -n "$PY" ]] && "$PY" -c "import xdist" >/dev/null 2>&1; then
  # `loadfile`, а не дефолтный `load`: тесты делят внутрипроцессные глобалы,
  # и файл целиком на одном воркере сохраняет порядок обычного прогона.
  pytest_args+=(-n auto --dist loadfile)
else
  NOTES+=("без xdist — venv отстал от pyproject, почини: .venv/bin/pip install -e '.[dev]'")
fi

RUFF="$(bin ruff)"
MYPY="$(bin mypy)"
PYTEST="$(bin pytest)"
for tool in "$RUFF" "$MYPY" "$PYTEST"; do
  if [[ -z "$tool" ]]; then
    echo "GATE НЕ ЗАПУЩЕН: нет ruff/mypy/pytest ни в .venv, ни в PATH." >&2
    echo "Поставь окружение: python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
    exit 127
  fi
done

TOTAL_START=$SECONDS
run_step "ruff"   "$RUFF" check cod_doc/ tests/
run_step "format" "$RUFF" format --check cod_doc/ tests/
run_step "mypy"   "$MYPY" cod_doc/
run_step "pytest" "$PYTEST" "${pytest_args[@]}"
TOTAL=$((SECONDS - TOTAL_START))

# Из хвоста pytest — только счётчики: «2419 passed, 1 skipped». Время шага
# уже есть в общей длительности, warnings в одну строку не нужны.
tally="$(grep -oE '[0-9]+ (passed|failed|error)[^)]*' "$LOG" | tail -1 \
  | sed -E 's/,? *[0-9]+ warnings?//; s/ +in +[0-9].*$//; s/ *$//')"
[[ -z "$tally" ]] && tally="pytest: счётчик не разобран"

if [[ ${#FAILED[@]} -eq 0 ]]; then
  line="GATE OK  ruff·format·mypy  ${tally}  ${TOTAL}s"
  for note in "${NOTES[@]}"; do line+="  [${note}]"; done
  echo "$line"
  if [[ $SHOW_LOG_ON_GREEN -eq 1 ]]; then
    echo "лог: $LOG"
  else
    rm -f "$LOG"
  fi
  exit 0
fi

echo "GATE КРАСНЫЙ: ${FAILED[*]}"
echo
for label in "${FAILED[@]}"; do
  echo "--- $label ---"
  # Тело шага между его маркерами, без служебных строк самого раннера,
  # без полосы прогресса pytest и без блока warnings summary. Без этой
  # чистки `tail` упирается в 34 строки точек, а сам разбор падения не
  # влезает — ровно то, ради чего написан раннер.
  awk -v l="$label" '
    $0 == "===== " l {inside=1; next}
    inside && $0 ~ ("^===== " l " → exit") {inside=0}
    inside
  ' "$LOG" \
  | awk '
    /^[.sFEUxX]+[[:space:]]+\[[[:space:]]*[0-9]+%\]$/ {next}
    /^=+ warnings summary/ {w=1}
    w && /^-- Docs: https:\/\/docs\.pytest\.org/ {w=0; next}
    w {next}
    {print}
  ' \
  | grep -vE '^[[:space:]]*$' | tail -"$EXCERPT_LINES"
  echo
done
echo "полный лог: $LOG"
for note in "${NOTES[@]}"; do echo "примечание: $note"; done
exit "${#FAILED[@]}"
