#!/bin/sh
# Run alembic migrations for every registered project, then start the server.
# Runs from /app where alembic.ini and cod_doc/infra/migrations/ live.
set -e

python - <<'PYEOF'
import os
import subprocess
import sys

try:
    from cod_doc.config import Config
    from cod_doc.infra.db import resolve_db_url

    cfg = Config.load()
    projects = cfg.list_projects()

    if not projects:
        print("[migrate] no projects registered yet — skipping", flush=True)
    else:
        for entry in projects:
            db_url = resolve_db_url(entry.root)
            env = {**os.environ, "COD_DOC_DB_URL": db_url}
            result = subprocess.run(
                ["alembic", "upgrade", "head"],
                cwd="/app",
                env=env,
            )
            label = entry.name
            if result.returncode == 0:
                print(f"[migrate] ok: {label}", flush=True)
            else:
                print(f"[migrate] warning: {label} returned rc={result.returncode}", flush=True)
except Exception as exc:
    print(f"[migrate] error: {exc}", file=sys.stderr, flush=True)
    # Non-fatal: server may still work if schema already up-to-date.
PYEOF

exec cod-doc serve
