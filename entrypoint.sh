#!/bin/sh
# Run alembic migrations for every registered project, then exec CMD
# (default `cod-doc serve`; the MCP container passes `cod-doc-mcp …`).
# Runs from /app where alembic.ini and cod_doc/infra/migrations/ live.
set -e

# STO-009: миграции идут через `project migrate --all`, а не через инлайновый
# python с `resolve_db_url(entry.root)`. Тот резолвил БД всегда по embedded-пути
# `<root>/.cod-doc/state.db` и на hub-проекте (`db_url` в реестре) молча
# мигрировал не ту базу, печатая `[migrate] ok`.
#
# Не фатально: сервер поднимается и на неудачной миграции (схема может быть уже
# накатана снаружи), но причина каждой неудачи уходит в лог отдельной строкой,
# а команда возвращает ненулевой код.
if cod-doc project migrate --all; then
    echo "[migrate] ok" >&2
else
    echo "[migrate] FAILED — см. строки выше; сервер стартует со схемой как есть" >&2
fi

# CLO-034: схема хранилища доступа догоняется вместе с проектами; его самого
# entrypoint не создаёт — гейт включает только явный `cod-doc access init`.
# На узле с COD_DOC_REQUIRE_AUTH=1 непрогнанная схема хранилища — отказ старта:
# гейт со старой схемой падал бы на каждом запросе.
if ! cod-doc access migrate; then
    echo "[migrate] access store FAILED" >&2
    case "${COD_DOC_REQUIRE_AUTH:-}" in 1|true|yes) exit 1 ;; esac
fi

# Без команды — прежний дефолт.
[ "$#" -eq 0 ] && set -- cod-doc serve
exec "$@"
