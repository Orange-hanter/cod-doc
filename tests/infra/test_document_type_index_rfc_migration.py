"""0044: `index` / `rfc` возвращаются из `frontmatter_json` туда, где импорт сделал `module-spec`.

Как и в 0040, интересно не «UPDATE отработал», а **чего миграция не трогает**:
строку с типом, выбранным уже в БД (не `module-spec`), даже если в её старом
frontmatter написано `type: rfc`; строку без frontmatter; и чужое написание
жанра (`ux-proposal`) — его ADO-238 лечит правкой файла, а не миграцией.

Строки наливаются сырым SQL на 0043: после ADO-238 импорт пишет `rfc` как есть,
и через сервисный слой «сведённую в module-spec» базу не собрать.
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

BEFORE = "0043_plan_id_prefix"

#: doc_key -> (stored type, frontmatter type or None)
SEEDED: dict[str, tuple[str, str | None]] = {
    "master": ("module-spec", "index"),
    "rfc-01": ("module-spec", "rfc"),
    "spec": ("module-spec", "module-spec"),
    "no-frontmatter": ("module-spec", None),
    "chosen-in-db": ("design", "rfc"),
    "foreign-spelling": ("module-spec", "ux-proposal"),
}


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'doctype.db'}"


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
                fm = {"type": authored} if authored is not None else {}
                conn.execute(
                    text(
                        "INSERT INTO document (project_id, doc_key, path, type, status, title, "
                        "frontmatter_json, created, last_updated) "
                        "VALUES (:pid, :key, :path, :type, 'draft', :key, :fm, :now, :now)"
                    ),
                    {
                        "pid": project_id,
                        "key": key,
                        "path": f"{key}.md",
                        "type": stored,
                        "fm": json.dumps(fm),
                        "now": now,
                    },
                )
    finally:
        engine.dispose()


def _types(db_url: str) -> dict[str, str]:
    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            return {
                str(k): str(v) for k, v in conn.execute(text("SELECT doc_key, type FROM document"))
            }
    finally:
        engine.dispose()


@pytest.fixture
def seeded_pre_0044(db_url: str) -> str:
    run_alembic("upgrade", BEFORE, db_url=db_url)
    _seed(db_url)
    return db_url


def test_upgrade_restores_only_rows_the_fallback_folded(seeded_pre_0044: str) -> None:
    run_alembic("upgrade", "head", db_url=seeded_pre_0044)
    assert _types(seeded_pre_0044) == {
        "master": "index",
        "rfc-01": "rfc",
        "spec": "module-spec",
        "no-frontmatter": "module-spec",
        "chosen-in-db": "design",
        "foreign-spelling": "module-spec",
    }


def test_downgrade_folds_both_values_back_into_module_spec(seeded_pre_0044: str) -> None:
    run_alembic("upgrade", "head", db_url=seeded_pre_0044)
    run_alembic("downgrade", BEFORE, db_url=seeded_pre_0044)
    assert _types(seeded_pre_0044) == {k: v[0] for k, v in SEEDED.items()}
