# ─────────────────────── cod-doc: динамические значения ──────────────────────
# Рукописный runtime. Генератор вшивает этот файл в `_cod-doc` дословно,
# подставляя только SQL по плейсхолдерам @@SQL:<ключ>@@ (см. sources.py).
#
# Три правила, из которых всё остальное следует:
#
#  1. НИКОГДА не звать `cod-doc`. Замеры: `--help` 470 мс, `task list --json`
#     710 мс, `project list` 870 мс — CLI жадно импортирует 26 групп, а
#     cmd_hub тянет alembic + SQLAlchemy. Реестр читаем awk'ом (<10 мс),
#     всё остальное — одним `sqlite3 -readonly` (~20 мс со стартом процесса).
#  2. Любая осечка — `return 1` и НОЛЬ вывода. Тогда zsh молча уходит в
#     `_default`, а не сыплет ошибками поверх промпта.
#  3. Кэша нет намеренно. 20 мс ниже порога восприятия, а протухший кэш
#     («создал ADO-500, жму TAB — её нет») дороже любой экономии.

# ── реестр проектов ──────────────────────────────────────────────────────────

(( $+functions[_cod_doc_registry] )) || _cod_doc_registry() {
  # name<TAB>path<TAB>db_url для каждой записи ~/.cod-doc/config.yaml.
  # db_url == null|~ отдаём пустым — значит embedded <path>/.cod-doc/state.db.
  local cfg="${COD_DOC_HOME:-$HOME/.cod-doc}/config.yaml"
  [[ -r $cfg ]] || return 1
  (( $+commands[awk] )) || return 1
  # \047 — одинарная кавычка: держим awk-программу свободной от них, иначе
  # её не завернуть в одинарные кавычки zsh.
  awk '
    function flush() {
      if (name != "" && path != "") print name "\t" path "\t" url
      name = ""; path = ""; url = ""
    }
    function val(s,   v) {
      v = s
      sub(/^[^:]+:[ \t]*/, "", v)
      gsub(/^["\047]|["\047]$/, "", v)
      return v
    }
    /^projects:[ \t]*$/ { inp = 1; next }
    {
      if (!inp) next
      line = $0
      if (line ~ /^[^ \t#-]/) { flush(); inp = 0; next }
      sub(/^[ \t]*/, "", line)
      if (line ~ /^-[ \t]/) { flush(); sub(/^-[ \t]+/, "", line) }
      else if (line == "-") { flush(); next }
      if (line ~ /^name:[ \t]/)         { name = val(line) }
      else if (line ~ /^path:[ \t]/)    { path = val(line) }
      else if (line ~ /^db_url:[ \t]/)  { url = val(line); if (url == "null" || url == "~") url = "" }
    }
    END { flush() }
  ' "$cfg" 2>/dev/null
}

# ── какой проект имеет в виду пользователь ───────────────────────────────────

(( $+functions[_cod_doc_safe_slug] )) || _cod_doc_safe_slug() {
  # Слаг пойдёт в SQL-литерал. Пропускаем только [A-Za-z0-9._-]; проверка без
  # EXTENDED_GLOB, потому что в completion-контексте он не гарантирован.
  [[ -n $1 && -z ${1//[A-Za-z0-9._-]/} ]]
}

(( $+functions[_cod_doc_slug] )) || _cod_doc_slug() {
  # 1) уже разобранный -p/--project: _arguments кладёт opt_args в динамическую
  #    область видимости вызывающего, поэтому action-функции он виден даром;
  # 2) ручной скан $words — когда дополняем позиционный ДО -p, и для слитной
  #    формы -pcod-doc;
  # 3) подъём от $PWD к .cod-doc/state.db — тот же приём, что у
  #    workspace-discovery в cod_doc/config.py.
  local slug="" i
  slug="${(Q)opt_args[--project]:-${(Q)opt_args[-p]}}"
  if [[ -z $slug ]]; then
    for (( i = 1; i <= $#words; i++ )); do
      case ${words[i]} in
        (--project=*) slug=${words[i]#--project=} ;;
        (-p|--project) slug=${words[i+1]} ;;
        (-p?*) slug=${words[i]#-p} ;;
      esac
    done
  fi
  _cod_doc_safe_slug "$slug" || return 1
  print -r -- "$slug"
}

(( $+functions[_cod_doc_db] )) || _cod_doc_db() {
  # Путь к sqlite-файлу проекта. Пусто + return 1, если проект на Postgres.
  # ВНИМАНИЕ: не заводить локальную переменную `path` — в zsh она специальная
  # и привязана к $PATH; локальное объявление тихо ломает и поиск команд, и
  # сам цикл. Отсюда суффикс entry_*.
  local slug entry entry_name entry_root url dir
  slug=$(_cod_doc_slug)

  if [[ -n $slug ]]; then
    for entry in ${(f)"$(_cod_doc_registry)"}; do
      entry_name=${entry%%$'\t'*}
      [[ $entry_name == $slug ]] || continue
      entry_root=${${entry#*$'\t'}%%$'\t'*}
      url=${entry##*$'\t'}
      if [[ -n $url ]]; then
        # db_url_for_entry (cod_doc/infra/db.py): непустой db_url бьёт
        # embedded. Правило по СХЕМЕ, а не по «null/не-null»: запись orakul
        # это sqlite:////Users/... — вполне читаемый файл. Hub на Postgres —
        # пас, дополнять нечем.
        [[ $url == sqlite*://* ]] || return 1
        print -r -- "${url#sqlite*:///}"
        return 0
      fi
      print -r -- "$entry_root/.cod-doc/state.db"
      return 0
    done
    return 1
  fi

  # Проект не назван — берём ближайшую БД вверх по дереву от $PWD.
  dir=$PWD
  while [[ -n $dir && $dir != / ]]; do
    if [[ -f $dir/.cod-doc/state.db ]]; then
      print -r -- "$dir/.cod-doc/state.db"
      return 0
    fi
    dir=${dir:h}
  done
  return 1
}

(( $+functions[_cod_doc_sql] )) || _cod_doc_sql() {
  # Один read-only вызов. Плоский путь, а НЕ file:-URI: URI требует
  # percent-encoding, а пути проектов могут содержать пробелы.
  local sql=$1 db out
  (( $+commands[sqlite3] )) || return 1
  db=$(_cod_doc_db) || return 1
  [[ -n $db && -r $db ]] || return 1
  out=$(sqlite3 -readonly -batch -noheader -cmd '.timeout 300' "$db" "$sql" 2>/dev/null) || return 1
  [[ -n $out ]] || return 1
  print -r -- "$out"
}

(( $+functions[_cod_doc_where_project] )) || _cod_doc_where_project() {
  # Hub-БД держит много проектов, embedded — один. Фильтруем, когда слаг
  # известен; иначе показываем всё, что лежит в найденном файле.
  local slug
  slug=$(_cod_doc_slug) || return 0
  [[ -n $slug ]] || return 0
  print -rn -- "and p.slug = '${slug//\'/\'\'}'"
}

# ── источники значений ───────────────────────────────────────────────────────

(( $+functions[_cod_doc_projects] )) || _cod_doc_projects() {
  local entry
  local -a slugs
  for entry in ${(f)"$(_cod_doc_registry)"}; do
    slugs+=( "${entry%%$'\t'*}:${${entry#*$'\t'}%%$'\t'*}" )
  done
  (( $#slugs )) || return 1
  _describe -t cod-doc-projects 'project' slugs
}

(( $+functions[_cod_doc_tasks] )) || _cod_doc_tasks() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:tasks@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-tasks 'task' rows
}

(( $+functions[_cod_doc_docs] )) || _cod_doc_docs() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:docs@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-docs 'document' rows
}

(( $+functions[_cod_doc_plans] )) || _cod_doc_plans() {
  # plan.scope И ЕСТЬ идентификатор плана (UNIQUE), отдельного id нет.
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:plans@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-plans 'plan scope' rows
}

(( $+functions[_cod_doc_plan_sections] )) || _cod_doc_plan_sections() {
  # `task create --section A` — буква секции; сужаем по уже набранному --plan.
  local scope filter=""
  local -a rows
  scope="${(Q)opt_args[--plan]}"
  _cod_doc_safe_slug "$scope" && filter="and pl.scope = '${scope//\'/\'\'}'"
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:plan_sections@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-plan-sections 'plan section' rows
}

(( $+functions[_cod_doc_doc_sections] )) || _cod_doc_doc_sections() {
  # `link sync DOC_KEY --section ANCHOR`: doc_key приходит ПОЗИЦИОННЫМ, его
  # кладёт в $line сам _arguments. У scenario new|update он в --doc-key.
  local key filter=""
  local -a rows
  key="${(Q)opt_args[--doc-key]:-${line[1]}}"
  if [[ -n $key && -z ${key//[A-Za-z0-9._\/-]/} ]]; then
    filter="and d.doc_key = '${key//\'/\'\'}'"
  fi
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:doc_sections@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-doc-sections 'section anchor' rows
}

(( $+functions[_cod_doc_adrs] )) || _cod_doc_adrs() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:adrs@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-adrs 'ADR' rows
}

(( $+functions[_cod_doc_stories] )) || _cod_doc_stories() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:stories@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-stories 'user story' rows
}

(( $+functions[_cod_doc_scenarios] )) || _cod_doc_scenarios() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:scenarios@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-scenarios 'scenario' rows
}

(( $+functions[_cod_doc_scenario_groups] )) || _cod_doc_scenario_groups() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:scenario_groups@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-scenario-groups 'scenario group' rows
}

(( $+functions[_cod_doc_revisions] )) || _cod_doc_revisions() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:revisions@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-revisions 'revision' rows
}

(( $+functions[_cod_doc_routines] )) || _cod_doc_routines() {
  local -a rows
  rows=( ${(f)"$(_cod_doc_sql "@@SQL:routines@@")"} )
  (( $#rows )) || return 1
  _describe -t cod-doc-routines 'routine' rows
}

(( $+functions[_cod_doc_adapters] )) || _cod_doc_adapters() {
  # Адаптеры живут не в БД, а в ~/.cod-doc/adapters.json (cmd_adapter.py).
  local file="${COD_DOC_HOME:-$HOME/.cod-doc}/adapters.json"
  local -a names
  [[ -r $file ]] || return 1
  names=( ${(f)"$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$file" 2>/dev/null)"} )
  (( $#names )) || return 1
  _describe -t cod-doc-adapters 'adapter' names
}
