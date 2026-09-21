#!/usr/bin/env bash
# Bootstrap cod-doc на машине, где его ещё нет.
#
#   ./cod-doc-services.sh [<флаги cod-doc update>]
#
# Ставит cod-doc в PATH из этого чекаута (uv tool) и отдаёт ему управление:
# `cod-doc update --install-services` соберёт пиннованный рантайм, догонит
# схему, отрендерит plist'ы и загрузит три демона.
#
# Всё остальное переехало внутрь cod-doc и здесь больше не дублируется:
#
#   upgrade                                  → cod-doc update
#   build | status | version | restart       → cod-doc runtime <то же имя>
#   rollback | install | uninstall | render  → cod-doc runtime <то же имя>
#
# Скрипт оставлен ровно ради курицы и яйца: `cod-doc runtime install` требует
# уже собранного рантайма, а на чистой машине cod-doc нет вовсе.
#
# Обоснования сборки и загрузки сервисов (`--relocatable` против `bad
# interpreter`, свежий venv вместо install поверх, асинхронный bootout,
# smoke-тест после свапа, `python -P` при опросе версии) переехали в докстринги
# `cod_doc/services/runtime_service.py` и `cod_doc/services/launchd_service.py`.
# Второй копии здесь нет намеренно: разъехавшиеся копии хуже одной.
set -euo pipefail

# Чекаут, из которого ставим. По умолчанию — тот, в котором лежит сам скрипт:
# на чистой машине он и есть единственное, что у человека уже есть.
REPO="${COD_DOC_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BIN="${COD_DOC_BIN:-$HOME/.local/bin/cod-doc}"

die() {
	echo "cod-doc-services: $*" >&2
	exit 1
}

usage() {
	cat <<-USAGE
		bootstrap cod-doc: поставить в PATH из ${REPO} и запустить
		  cod-doc update --install-services

		  ./cod-doc-services.sh [<флаги cod-doc update>]

		Управление уже установленным cod-doc — им самим:
		  cod-doc update            # рантайм → схема → сервисы → починка
		  cod-doc runtime --help    # build status version restart rollback
		                            # install uninstall render
	USAGE
}

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
upgrade)
	die "переехало в 'cod-doc update' (алиас — 'cod-doc upgrade')"
	;;
build | status | version | restart | rollback | install | uninstall | render)
	die "переехало в 'cod-doc runtime ${1}'"
	;;
esac

command -v uv >/dev/null 2>&1 || die "нужен uv (brew install uv)"
[ -e "$REPO/.git" ] || die "не репозиторий: ${REPO} (задай COD_DOC_REPO)"

echo "cod-doc-services: ставлю cod-doc в PATH из ${REPO}"
uv tool install --force --from "$REPO" cod-doc
[ -x "$BIN" ] || die "uv поставил cod-doc, но по пути ${BIN} его нет (задай COD_DOC_BIN)"

exec "$BIN" update --install-services "$@"
