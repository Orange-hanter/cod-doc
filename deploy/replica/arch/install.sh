#!/usr/bin/env bash
# Установка реплики cod-doc на arch (без sudo: всё в $HOME и systemd --user).
#
#   bash deploy/replica/arch/install.sh            # бинарь, ключ, юниты; таймеры выключены
#   bash deploy/replica/arch/install.sh --enable   # после настройки Гермеса: включить таймеры
#
# Повторный запуск безопасен: ключ и ssh-конфиг не перезаписываются.
set -euo pipefail

LS_VERSION=0.5.17
LS_SHA256=cfb371176d164437ae869f8351cfde49bd1804ae71c61923f75c9cba9c9c006d
HERMES_HOST=${HERMES_HOST:-78.17.96.218}
KEY=$HOME/.ssh/cod_doc_replica
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=$HOME/.local/bin
UNITS=$HOME/.config/systemd/user
TIMERS=(cod-doc-replica-pull.timer cod-doc-replica-snapshot.timer cod-doc-replica-check.timer)

for t in rsync sqlite3 zstd jq flock docker; do
    command -v "$t" >/dev/null || { echo "нет $t: sudo pacman -S $t" >&2; exit 1; }
done

if [[ ${1:-} == --enable ]]; then
    ssh -o BatchMode=yes cod-doc-hermes-replica true 2>/dev/null && {
        echo "rrsync на Гермесе не должен давать shell — проверь authorized_keys" >&2; exit 1; }
    "$BIN/cod-doc-replica" pull
    "$BIN/cod-doc-replica" snapshot
    systemctl --user enable --now "${TIMERS[@]}"
    "$BIN/cod-doc-replica" status
    exit 0
fi

# 1. Litestream — пиннованная версия, sha256 сверен с checksums.txt релиза.
if [[ $("$BIN/litestream" version 2>/dev/null) != *"$LS_VERSION"* ]]; then
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/ls.tgz" \
        "https://github.com/benbjohnson/litestream/releases/download/v$LS_VERSION/litestream-$LS_VERSION-linux-x86_64.tar.gz"
    echo "$LS_SHA256  $tmp/ls.tgz" | sha256sum -c --quiet
    tar xzf "$tmp/ls.tgz" -C "$tmp" litestream
    install -Dm755 "$tmp/litestream" "$BIN/litestream"
    rm -r "$tmp"
fi

# 2. Скрипт и юниты.
install -Dm755 "$HERE/cod-doc-replica" "$BIN/cod-doc-replica"
install -Dm644 "$HERE/standby-compose.yml" "$HOME/cod-doc-replica/standby/docker-compose.yml"
mkdir -p "$UNITS"
install -m644 "$HERE"/systemd/*.service "$HERE"/systemd/*.timer "$UNITS/"
systemctl --user daemon-reload

# 3. Отдельный ключ только для pull: на узле он ограничен rrsync -ro.
[[ -f $KEY ]] || ssh-keygen -q -t ed25519 -N '' -C "cod-doc-replica@arch" -f "$KEY"
if ! grep -q '^Host cod-doc-hermes-replica$' "$HOME/.ssh/config" 2>/dev/null; then
    cat >>"$HOME/.ssh/config" <<EOF

Host cod-doc-hermes-replica
    HostName $HERMES_HOST
    User root
    IdentityFile $KEY
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
    ServerAliveCountMax 3
EOF
fi

cat <<EOF

Готово на arch. Таймеры пока выключены.
На Гермесе добавить строку в /root/.ssh/authorized_keys (README §«Гермес», шаг 3):

command="/usr/bin/rrsync -ro /root/cod-doc",restrict $(cat "$KEY.pub")

затем: bash $0 --enable
EOF
