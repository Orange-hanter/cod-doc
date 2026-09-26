"""0040: `resolved` / `done` возвращаются из `frontmatter_json` туда, где алиас сделал `active`.

Интересный случай — не «UPDATE отработал», а **чего миграция не трогает**.
Строка, чей статус выбран уже в БД (`doc accept`, смена статуса в вебе), может
держать старую копию frontmatter со `status: resolved` — её переписывать нельзя:
это было бы решение из устаревшего файла поверх решения человека. Поэтому
сидинг содержит строку в `deprecated` с тем же frontmatter и строку без него.

Строки наливаются сырым SQL на 0039: сервисный слой после ADO-218 пишет
`resolved` как есть, и через него «сведённую в active» базу не собрать.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from cod_doc.infra.db import make_engine
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

BEFORE = "0039_totals_cancelled"

#: doc_key -> (stored status, frontmatter status or None)
SEEDED: dict[str, tuple[str, str | None]] = {
    "audit-closed": ("active", "resolved"),
    "plan-closed": ("active", "done"),
    "audit-live": ("active", "active"),
    "no-frontmatter": ("active", None),
    "chosen-in-db": ("deprecated", "resolved"),
    "authored": ("authoritative", "authoritative"),
}


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'docstatus.db'}"


def _seed(db_url: str) -> None:
    now = datetime.now(UTC).isoformat()
    engine = make_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO project (slug, title, root_path, config_json, created, updated) "
                    "VALUES ('p', 'P', '/tmp/p', '{}', :now, :now)"
                ),
                {"now": now},
            )
            project_id = conn.execute(
                text("SELECT row_id FROM project WHERE slug = 'p'")
            ).scalar_one()
            for key, (stored, authored) in SEEDED.items():
                fm = {"status": authored} if authored is not None else {}
                conn.execute(
                    text(
                        "INSERT INTO document (project_id, doc_key, path, type, status, title, "
                        "frontmatter_json, created, last_updated) "
                        "VALUES (:pid, :key, :path, 'audit-report', :status, :key, :fm, "
                        ":now, :now)"
                    ),
                    {
                        "pid": project_id,
                        "key": key,
                        "path": f"{key}.md",
                        "status": stored,
                        "fm": json.dumps(fm),
                        "now": now,
                    },
                )
    finally:
        engine.dispose()


def _statuses(db_url: str) -> dict[str, str]:
    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            return {
                str(k): str(v)
                for k, v in conn.execute(text("SELECT doc_key, status FROM document"))
            }
    finally:
        engine.dispose()


@pytest.fixture
def seeded_pre_0040(db_url: str) -> str:
    run_alembic("upgrade", BEFORE, db_url=db_url)
    _seed(db_url)
    return db_url


def test_upgrade_restores_only_rows_the_alias_folded(seeded_pre_0040: str) -> None:
    run_alembic("upgrade", "head", db_url=seeded_pre_0040)
    assert _statuses(seeded_pre_0040) == {
        "audit-closed": "resolved",
        "plan-closed": "done",
        "audit-live": "active",
        "no-frontmatter": "active",
        "chosen-in-db": "deprecated",
        "authored": "authoritative",
    }


def test_downgrade_folds_both_values_back_into_active(seeded_pre_0040: str) -> None:
    run_alembic("upgrade", "head", db_url=seeded_pre_0040)
    run_alembic("downgrade", BEFORE, db_url=seeded_pre_0040)
    assert _statuses(seeded_pre_0040) == {k: v[0] for k, v in SEEDED.items()}
