#!/usr/bin/env bash
# Смоук cod-doc-replica без сети и без Litestream: фальшивый «узел» в tmp,
# заглушка litestream копирует файл. Проверяет фильтр pull, snapshot,
# манифест, отказ на пустой реплике и ротацию снапшотов.
#
#   bash deploy/replica/arch/selftest.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -r "$T"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

# --- фальшивый /root/cod-doc --------------------------------------------
N=$T/node
mkdir -p "$N/projects/alpha/.cod-doc" "$N/home" "$N/src" "$N/backups/deploy" "$N/tokens" \
    "$N/replica/projects/alpha/.cod-doc/state.db" "$N/replica/home/access.db"
sqlite3 "$N/projects/alpha/.cod-doc/state.db" \
    "create table alembic_version(version_num text); insert into alembic_version values('0057');"
cp "$N/projects/alpha/.cod-doc/state.db" "$N/replica/projects/alpha/.cod-doc/state.db/src.db"
sqlite3 "$N/replica/home/access.db/src.db" "create table actor(id int);"
: >"$N/projects/alpha/.cod-doc/state.db-wal"
echo doc >"$N/projects/alpha/README.md"
echo 'projects: {}' >"$N/home/config.yaml"
: >"$N/home/access.db"
echo big >"$N/backups/deploy/state.db"
echo FROM >"$N/src/Dockerfile"
printf 'COD_DOC_TAG=abc123\n' >"$N/.env"
echo tok >"$N/tokens/x.token"

# --- заглушка litestream ---------------------------------------------------
cat >"$T/litestream" <<'EOF'
#!/usr/bin/env bash
set -e
cmd=$1; shift
out='' url=''
while (($#)); do
    case $1 in
        -o) out=$2; shift 2 ;;
        -integrity-check) shift 2 ;;
        -json) shift ;;
        *) url=$1; shift ;;
    esac
done
dir=${url#file://}
case $cmd in
    restore) cp "$dir/src.db" "$out" ;;
    ltx) echo '[{"level":0,"max_txid":"0000000000000007","timestamp":"2026-11-21T10:00:00Z"}]' ;;
esac
EOF
chmod +x "$T/litestream"

export COD_DOC_REPLICA_ROOT=$T/root COD_DOC_REPLICA_SOURCE=$N/ LITESTREAM=$T/litestream
R=$HERE/cod-doc-replica
# docker в смоуке не нужен: maybe_build падает мягко, но не должен звать docker
docker() { return 1; }
export -f docker

# --- pull: фильтр ----------------------------------------------------------
"$R" pull
M=$T/root/mirror
[[ -f $M/replica/projects/alpha/.cod-doc/state.db/src.db ]] || fail "реплика не скопирована"
[[ -f $M/replica/home/access.db/src.db ]] || fail "реплика home не скопирована"
[[ -f $M/projects/alpha/README.md ]] || fail "проекция не скопирована"
[[ -f $M/home/config.yaml && -f $M/.env && -f $M/src/Dockerfile && -f $M/tokens/x.token ]] \
    || fail "конфиги/исходники не скопированы"
[[ ! -e $M/projects/alpha/.cod-doc/state.db ]] || fail "живая state.db попала в зеркало"
[[ ! -e $M/projects/alpha/.cod-doc/state.db-wal ]] || fail "WAL попал в зеркало"
[[ ! -e $M/home/access.db ]] || fail "живая access.db попала в зеркало"
[[ ! -e $M/backups ]] || fail "backups/ попал в зеркало"

# --- snapshot --------------------------------------------------------------
"$R" snapshot 2>/dev/null
S=$T/root/snapshots
[[ -L $S/latest ]] || fail "нет latest"
[[ -f $S/latest/projects/alpha/.cod-doc/state.db.zst ]] || fail "нет сжатой state.db"
[[ -f $S/latest/home/access.db.zst ]] || fail "нет сжатой access.db"
grep -q $'projects/alpha/.cod-doc/state.db\t[0-9]*\t0057\t0000000000000007' "$S/latest/manifest.tsv" \
    || fail "манифест: $(cat "$S/latest/manifest.tsv")"
"$R" check

# --- ротация: 48 часовых + первый за день, 30 дней ------------------------
# Старые снапшоты: 1..31 октября, по два в день. Плюс два сегодняшних
# (уже снятый и следующий). KEEP_HOURLY=5 оставляет: оба сегодняшних,
# 31-е 12:00 и 00:00, 30-е 12:00; KEEP_DAILY=3 добавляет первый за сегодня,
# за 31-е и за 30-е (30-е 00:00) — итого 6.
first=$(readlink "$S/latest")
for day in $(seq -w 1 31); do
    mkdir -p "$S/202610${day}T000000Z" "$S/202610${day}T120000Z"
done
sleep 1
KEEP_HOURLY=5 KEEP_DAILY=3 "$R" snapshot 2>/dev/null
mapfile -t left < <(find "$S" -mindepth 1 -maxdepth 1 -type d -name '2*' -printf '%f\n' | sort)
want=(20261030T000000Z 20261030T120000Z 20261031T000000Z 20261031T120000Z "$first" "$(readlink "$S/latest")")
[[ "${left[*]}" == "${want[*]}" ]] || fail "ротация: осталось ${left[*]}, ждали ${want[*]}"

# --- пустая реплика — отказ ------------------------------------------------
mv "$M/replica" "$T/replica.off"
if "$R" snapshot 2>/dev/null; then fail "snapshot без БД прошёл"; fi

# --- протухший pull ловится check -----------------------------------------
echo $(( $(date -u +%s) - 3600 )) >"$T/root/state/last_pull"
if "$R" check 2>/dev/null; then fail "check не заметил протухший pull"; fi

echo "selftest ok"
