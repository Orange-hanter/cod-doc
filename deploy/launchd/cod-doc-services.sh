#!/usr/bin/env bash
# Управление постоянными сервисами cod-doc на macOS (launchd) и доставка
# свежих ревизий в них.
#
#   ./cod-doc-services.sh install          # отрендерить plist'ы и загрузить
#   ./cod-doc-services.sh status           # что загружено, отвечает ли, какая ревизия
#   ./cod-doc-services.sh build [<ref>]    # собрать и проверить, ничего не подменяя
#   ./cod-doc-services.sh upgrade [<ref>]  # собрать origin/main и перезапустить
#   ./cod-doc-services.sh restart          # перечитать бинарь без пересборки
#   ./cod-doc-services.sh version          # версии всех установок на машине
#   ./cod-doc-services.sh rollback         # вернуть предыдущий рантайм
#   ./cod-doc-services.sh uninstall        # выгрузить и удалить plist'ы
#   ./cod-doc-services.sh render           # только показать plist'ы
#
# Все три сервиса смотрят на ОДНУ пиннованную non-editable сборку в
# ~/.cod-doc/runtime. Editable-инстал рабочего дерева означает, что любая
# правка, ребейз или незавершённый мёрдж мгновенно уезжают в живые сервисы:
# у MCP это роняет все харнессы машины разом, у веб-UI — молча подменяет то,
# что видит человек. Апгрейд обязан быть осознанным действием.
set -euo pipefail

REPO="${COD_DOC_REPO:-$HOME/Git/_my/cod-doc}"
RUNTIME="${COD_DOC_RUNTIME:-$HOME/.cod-doc/runtime}"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs"
# Нейтральный cwd: у долгоживущего процесса рабочий каталог заморожен, и
# discovery проектов по дереву вверх от cwd в нём смысла не имеет.
WORKDIR="$HOME/.cod-doc"
PYTHON_VERSION="${COD_DOC_PYTHON:-3.13}"

# label:kind:port:profile
# kind=mcp — MCP-демон (порт задаёт профиль), kind=web — REST API + web UI.
SERVICES=(
	"com.cod-doc.mcp:mcp:8801:standard"
	"com.cod-doc.mcp-agent:mcp:8802:agent"
	"com.cod-doc.web:web::"
)

# Веб жил под личным лейблом и из editable-venv репозитория. `install`
# снимает его, чтобы два процесса не дрались за один порт.
LEGACY_WEB_LABEL="com.dakh.cod-doc"

die() {
	echo "cod-doc-services: $*" >&2
	exit 1
}

note() { echo "cod-doc-services: $*"; }

# ── порт веба ───────────────────────────────────────────────────────────────
# Читается из ~/.cod-doc/config.yaml, а не дублируется здесь: plist намеренно
# не передаёт `--port`, иначе конфиг и plist разъезжаются молча.
web_port() {
	"$RUNTIME/bin/python" - <<-'PY' 2>/dev/null || echo 8765
		import pathlib, sys
		try:
		    import yaml
		except ImportError:
		    sys.exit(1)
		p = pathlib.Path.home() / ".cod-doc" / "config.yaml"
		if not p.exists():
		    sys.exit(1)
		data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
		print(data.get("api_port", 8765))
	PY
}

# ── рендер plist ────────────────────────────────────────────────────────────
render_args() {
	local kind="$1" port="$2" profile="$3"
	if [ "$kind" = "mcp" ]; then
		cat <<-ARGS
			    <string>${RUNTIME}/bin/cod-doc-mcp</string>
			    <string>--transport</string>
			    <string>streamable-http</string>
			    <string>--host</string>
			    <string>127.0.0.1</string>
			    <string>--port</string>
			    <string>${port}</string>
			    <string>--profile</string>
			    <string>${profile}</string>
		ARGS
	else
		cat <<-ARGS
			    <string>${RUNTIME}/bin/cod-doc</string>
			    <string>serve</string>
		ARGS
	fi
}

render() {
	local label="$1" kind="$2" port="$3" profile="$4"
	cat <<-PLIST
		<?xml version="1.0" encoding="UTF-8"?>
		<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
		<plist version="1.0">
		<dict>
		  <key>Label</key>
		  <string>${label}</string>
		  <key>ProgramArguments</key>
		  <array>
		$(render_args "$kind" "$port" "$profile")
		  </array>
		  <key>WorkingDirectory</key>
		  <string>${WORKDIR}</string>
		  <key>EnvironmentVariables</key>
		  <dict>
		    <key>PATH</key>
		    <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
		    <key>PYTHONUNBUFFERED</key>
		    <string>1</string>
		  </dict>
		  <key>RunAtLoad</key><true/>
		  <key>KeepAlive</key><true/>
		  <key>ThrottleInterval</key><integer>10</integer>
		  <key>StandardOutPath</key><string>${LOGS}/${label}.log</string>
		  <key>StandardErrorPath</key><string>${LOGS}/${label}.log</string>
		</dict>
		</plist>
	PLIST
}

cmd_render() {
	for s in "${SERVICES[@]}"; do
		IFS=: read -r label kind port profile <<<"$s"
		echo "=== ${AGENTS}/${label}.plist"
		render "$label" "$kind" "$port" "$profile"
	done
}

# ── install / uninstall / restart ───────────────────────────────────────────
drop_legacy_web() {
	if launchctl print "gui/$(id -u)/${LEGACY_WEB_LABEL}" >/dev/null 2>&1; then
		launchctl bootout "gui/$(id -u)/${LEGACY_WEB_LABEL}" 2>/dev/null || true
		note "выгружен прежний веб-сервис ${LEGACY_WEB_LABEL} (работал из editable-venv)"
	fi
	if [ -f "${AGENTS}/${LEGACY_WEB_LABEL}.plist" ]; then
		mv "${AGENTS}/${LEGACY_WEB_LABEL}.plist" "${AGENTS}/${LEGACY_WEB_LABEL}.plist.replaced"
		note "plist ${LEGACY_WEB_LABEL} сохранён как .replaced"
	fi
}

cmd_install() {
	[ -x "$RUNTIME/bin/cod-doc-mcp" ] || die "рантайм не собран: нет $RUNTIME/bin/cod-doc-mcp — сначала 'upgrade'"
	mkdir -p "$AGENTS" "$LOGS" "$WORKDIR"
	drop_legacy_web
	for s in "${SERVICES[@]}"; do
		IFS=: read -r label kind port profile <<<"$s"
		render "$label" "$kind" "$port" "$profile" >"${AGENTS}/${label}.plist"
		launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
		launchctl bootstrap "gui/$(id -u)" "${AGENTS}/${label}.plist"
		note "loaded ${label}"
	done
	cmd_status
}

cmd_uninstall() {
	for s in "${SERVICES[@]}"; do
		IFS=: read -r label _ _ _ <<<"$s"
		launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
		rm -f "${AGENTS}/${label}.plist"
		note "removed ${label}"
	done
}

cmd_restart() {
	for s in "${SERVICES[@]}"; do
		IFS=: read -r label _ _ _ <<<"$s"
		launchctl kickstart -k "gui/$(id -u)/${label}" 2>/dev/null &&
			note "kicked ${label}" ||
			note "не загружен: ${label}"
	done
}

# ── status ──────────────────────────────────────────────────────────────────
probe_mcp() {
	# initialize — самый дешёвый запрос, который доказывает живость MCP.
	curl -fsS -m 5 -X POST "http://127.0.0.1:${1}/mcp" \
		-H 'Content-Type: application/json' \
		-H 'Accept: application/json, text/event-stream' \
		-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"healthcheck","version":"1"}}}' \
		>/dev/null 2>&1
}

probe_web() { curl -fsS -m 5 "http://127.0.0.1:${1}/api/health" >/dev/null 2>&1; }

cmd_status() {
	local wport
	wport="$(web_port)"
	for s in "${SERVICES[@]}"; do
		IFS=: read -r label kind port profile <<<"$s"
		[ "$kind" = "web" ] && port="$wport" && profile="-"
		printf '%-22s port=%-5s profile=%-9s ' "$label" "$port" "$profile"
		if ! launchctl print "gui/$(id -u)/${label}" >/dev/null 2>&1; then
			echo "NOT LOADED"
			continue
		fi
		if [ "$kind" = "mcp" ] && probe_mcp "$port"; then
			echo "OK"
		elif [ "$kind" = "web" ] && probe_web "$port"; then
			echo "OK"
		else
			echo "LOADED but not answering — see ${LOGS}/${label}.log"
		fi
	done
	echo
	cmd_version
}

# ── version ─────────────────────────────────────────────────────────────────
report_version() {
	local name="$1" bin="$2" py="${2%/*}/python"
	printf '%-28s ' "$name"
	if [ ! -x "$bin" ]; then
		echo "не установлен ($bin)"
		return
	fi
	# `--version` появился в ADO-189; на установке старше него спрашиваем
	# версию импортом, иначе строка про старую сборку — самая важная в выводе —
	# была бы пустой ровно тогда, когда она и нужна.
	"$bin" --version 2>/dev/null ||
		"$py" -P -c 'import cod_doc; print("cod-doc, version", cod_doc.__version__)' 2>/dev/null ||
		echo "версия не определяется"
}

cmd_version() {
	echo "установки cod-doc на машине:"
	report_version "рантайм сервисов" "$RUNTIME/bin/cod-doc"
	report_version "PATH (uv tool)" "$HOME/.local/bin/cod-doc"
	report_version "репозиторий (editable)" "$REPO/.venv/bin/cod-doc"
}

# ── upgrade ─────────────────────────────────────────────────────────────────
resolve_ref() {
	local ref="$1" sha
	sha="$(git -C "$REPO" rev-parse --verify "${ref}^{commit}")" ||
		die "не разрешается ревизия '${ref}'"
	# Катим только то, что прошло CI и смержено. Локальная ветка, грязное
	# дерево и незапушенный коммит физически не могут попасть в релиз.
	git -C "$REPO" merge-base --is-ancestor "$sha" origin/main ||
		die "ревизия ${ref} (${sha:0:7}) не является предком origin/main — катится только смерженное"
	echo "$sha"
}

#: Заполняются `build_staged`, читаются вызывающим.
BUILT_SHA=""
BUILT_VERSION=""
BUILT_SRC=""

# Экспорт ревизии и сборка рантайма в `${RUNTIME}.staged`. Ничего не
# подменяет и не перезапускает — отделено ровно затем, чтобы сборку можно
# было прогнать, не трогая живые сервисы.
build_staged() {
	local ref="$1"
	command -v uv >/dev/null 2>&1 || die "нужен uv (brew install uv)"
	[ -e "$REPO/.git" ] || die "не репозиторий: $REPO (задай COD_DOC_REPO)"

	note "git fetch origin"
	git -C "$REPO" fetch origin --tags --prune --quiet

	BUILT_SHA="$(resolve_ref "$ref")"
	note "ревизия ${BUILT_SHA:0:12}"

	# Временный git-worktree, а не `git archive`: в нём лежит ровно содержимое
	# коммита (незакоммиченное физически не может уехать в релиз), но при этом
	# есть `.git` — и setuptools-scm выводит версию сам.
	#
	# Экспорт архивом требовал бы подставлять версию руками через
	# SETUPTOOLS_SCM_PRETEND_VERSION, а вывод `git describe`
	# (`v1.1.0-456-g53d5fec`) — не версия PEP 440, и конвертировать его здесь
	# значило бы держать вторую реализацию схемы версий рядом с настоящей.
	BUILT_SRC="$(mktemp -d)/src"
	local staged="${RUNTIME}.staged"
	git -C "$REPO" worktree add --detach --quiet "$BUILT_SRC" "$BUILT_SHA"

	note "сборка рантайма в ${staged}"
	rm -rf "$staged"
	uv venv --python "$PYTHON_VERSION" "$staged" --quiet
	# Свежий венв, а не установка поверх: апгрейд «поверх» не удаляет файлы,
	# исчезнувшие из пакета. Так в рантайме годами жили модули, которых нет в
	# репозитории (services/story_service.py рядом с пакетом story_service/).
	uv pip install --python "$staged/bin/python" "$BUILT_SRC" --quiet

	# Smoke-тест из двух частей: пакет импортируется и называет свою версию,
	# console-script зарегистрирован и запускается. Проверяется именно
	# импортом, а не `cod-doc --version`: флаг появился только в ADO-189, и
	# опираться на него значит уметь собирать лишь ревизии новее самого себя —
	# то есть потерять откат на старый релиз.
	BUILT_VERSION="$("$staged/bin/python" -P -c 'import cod_doc; print(cod_doc.__version__)' 2>&1)" ||
		die "собранный рантайм не импортируется: $BUILT_VERSION"
	"$staged/bin/cod-doc" --help >/dev/null 2>&1 ||
		die "собранный рантайм не запускается: console-script cod-doc падает"
	note "собрано: ${BUILT_VERSION}"
}

# Снять временный worktree. Отдельной функцией — зовут и `build`, и `upgrade`.
drop_build_src() {
	[ -n "$BUILT_SRC" ] || return 0
	git -C "$REPO" worktree remove --force "$BUILT_SRC" 2>/dev/null || rm -rf "$BUILT_SRC"
	rmdir "$(dirname "$BUILT_SRC")" 2>/dev/null || true
	BUILT_SRC=""
}

cmd_build() {
	local ref="${1:-origin/main}"
	trap drop_build_src EXIT
	build_staged "$ref"
	drop_build_src
	note "готово: ${RUNTIME}.staged — сервисы не тронуты, свап не делался"
	note "поставить это: ./cod-doc-services.sh upgrade ${ref}"
}

cmd_upgrade() {
	local ref="${1:-origin/main}"
	trap drop_build_src EXIT
	build_staged "$ref"

	# Свап и только потом рестарт: процессы держат старый inode до kickstart.
	rm -rf "${RUNTIME}.previous"
	[ -d "$RUNTIME" ] && mv "$RUNTIME" "${RUNTIME}.previous"
	mv "${RUNTIME}.staged" "$RUNTIME"

	note "перезапуск сервисов"
	cmd_restart
	sleep 3
	cmd_status
	note "предыдущий рантайм сохранён в ${RUNTIME}.previous (rollback вернёт его)"

	# CLI в PATH — та же ревизия, что у сервисов: разъехавшиеся `cod-doc` в
	# терминале и в демоне дают разное поведение на одной и той же БД.
	note "обновление uv-tool в PATH"
	uv tool install --force --from "$BUILT_SRC" cod-doc --quiet
	report_version "PATH (uv tool)" "$HOME/.local/bin/cod-doc"
	drop_build_src

	note "накатить миграции во всех проектах: cod-doc project migrate --all"
}

cmd_rollback() {
	[ -d "${RUNTIME}.previous" ] || die "нечего откатывать: нет ${RUNTIME}.previous"
	rm -rf "${RUNTIME}.rollback-tmp"
	mv "$RUNTIME" "${RUNTIME}.rollback-tmp"
	mv "${RUNTIME}.previous" "$RUNTIME"
	mv "${RUNTIME}.rollback-tmp" "${RUNTIME}.previous"
	note "рантайм откачен; повторный rollback вернёт обратно"
	cmd_restart
	sleep 3
	cmd_status
}

case "${1:-status}" in
install) cmd_install ;;
uninstall) cmd_uninstall ;;
restart) cmd_restart ;;
status) cmd_status ;;
render) cmd_render ;;
version) cmd_version ;;
build) cmd_build "${2:-origin/main}" ;;
upgrade) cmd_upgrade "${2:-origin/main}" ;;
rollback) cmd_rollback ;;
*) die "неизвестная команда '${1}'; ожидается build|upgrade|rollback|install|uninstall|restart|status|version|render" ;;
esac
