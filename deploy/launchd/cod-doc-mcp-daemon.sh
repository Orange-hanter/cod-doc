#!/usr/bin/env bash
# Управление постоянными MCP-демонами cod-doc (macOS launchd).
#
# Один процесс на профиль обслуживает все харнессы сразу по streamable-http,
# вместо того чтобы каждый клиент порождал собственный stdio-субпроцесс.
#
#   ./cod-doc-mcp-daemon.sh install     # отрендерить plist'ы и загрузить
#   ./cod-doc-mcp-daemon.sh status      # что загружено и отвечает ли порт
#   ./cod-doc-mcp-daemon.sh restart     # перечитать бинарь после апгрейда
#   ./cod-doc-mcp-daemon.sh uninstall   # выгрузить и удалить plist'ы
#   ./cod-doc-mcp-daemon.sh render      # только показать plist'ы, ничего не трогая
#
# Бинарь берётся из COD_DOC_MCP_BIN, иначе из ~/.cod-doc/runtime/bin/cod-doc-mcp.
# Демон обязан смотреть на пиннованную non-editable сборку: editable-инстал
# рабочего дерева роняет все харнессы на машине одновременно при любой правке.
set -euo pipefail

BIN="${COD_DOC_MCP_BIN:-$HOME/.cod-doc/runtime/bin/cod-doc-mcp}"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs"
# Нейтральный cwd: у долгоживущего процесса рабочий каталог заморожен, и
# discovery проектов по дереву вверх от cwd в нём смысла не имеет.
WORKDIR="$HOME/.cod-doc"

# label:port:profile
DAEMONS=(
	"com.cod-doc.mcp:8801:standard"
	"com.cod-doc.mcp-agent:8802:agent"
)

die() {
	echo "cod-doc-mcp-daemon: $*" >&2
	exit 1
}

render() {
	local label="$1" port="$2" profile="$3"
	cat <<-PLIST
		<?xml version="1.0" encoding="UTF-8"?>
		<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
		<plist version="1.0">
		<dict>
		  <key>Label</key>
		  <string>${label}</string>
		  <key>ProgramArguments</key>
		  <array>
		    <string>${BIN}</string>
		    <string>--transport</string>
		    <string>streamable-http</string>
		    <string>--host</string>
		    <string>127.0.0.1</string>
		    <string>--port</string>
		    <string>${port}</string>
		    <string>--profile</string>
		    <string>${profile}</string>
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
	for d in "${DAEMONS[@]}"; do
		IFS=: read -r label port profile <<<"$d"
		echo "=== ${AGENTS}/${label}.plist"
		render "$label" "$port" "$profile"
	done
}

cmd_install() {
	[ -x "$BIN" ] || die "не найден исполняемый $BIN (задай COD_DOC_MCP_BIN)"
	mkdir -p "$AGENTS" "$LOGS" "$WORKDIR"
	for d in "${DAEMONS[@]}"; do
		IFS=: read -r label port profile <<<"$d"
		render "$label" "$port" "$profile" >"${AGENTS}/${label}.plist"
		launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
		launchctl bootstrap "gui/$(id -u)" "${AGENTS}/${label}.plist"
		echo "loaded ${label} → 127.0.0.1:${port} (${profile})"
	done
	cmd_status
}

cmd_uninstall() {
	for d in "${DAEMONS[@]}"; do
		IFS=: read -r label _ _ <<<"$d"
		launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
		rm -f "${AGENTS}/${label}.plist"
		echo "removed ${label}"
	done
}

cmd_restart() {
	for d in "${DAEMONS[@]}"; do
		IFS=: read -r label _ _ <<<"$d"
		launchctl kickstart -k "gui/$(id -u)/${label}"
		echo "kicked ${label}"
	done
}

cmd_status() {
	for d in "${DAEMONS[@]}"; do
		IFS=: read -r label port profile <<<"$d"
		printf '%-22s port=%-5s profile=%-9s ' "$label" "$port" "$profile"
		if ! launchctl print "gui/$(id -u)/${label}" >/dev/null 2>&1; then
			echo "NOT LOADED"
			continue
		fi
		# initialize — самый дешёвый запрос, который доказывает живость MCP.
		if curl -fsS -m 5 -X POST "http://127.0.0.1:${port}/mcp" \
			-H 'Content-Type: application/json' \
			-H 'Accept: application/json, text/event-stream' \
			-d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"healthcheck","version":"1"}}}' \
			>/dev/null 2>&1; then
			echo "OK"
		else
			echo "LOADED but not answering — see ${LOGS}/${label}.log"
		fi
	done
}

case "${1:-status}" in
install) cmd_install ;;
uninstall) cmd_uninstall ;;
restart) cmd_restart ;;
status) cmd_status ;;
render) cmd_render ;;
*) die "неизвестная команда '${1}'; ожидается install|uninstall|restart|status|render" ;;
esac
