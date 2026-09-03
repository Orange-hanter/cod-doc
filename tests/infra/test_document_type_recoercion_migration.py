"""ADO-015 migration 0026: give back the document types the importer flattened.

The interesting case is a database written *before* the enum grew: the row says
`module-spec` while the frontmatter it was imported from says `capability`.
Adding the enum member alone would make the next export rewrite that file to
`module-spec` — ADO-010's protection only holds while the value is unstorable.
The migration closes that window by restoring the authored value.
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

BEFORE = "0025_projection_fidelity"


def _alembic(db_url: str, *args: str) -> None:
    run_alembic(*args, db_url=db_url)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'recoercion.db'}"


def _insert_document(
    db_url: str,
    *,
    doc_key: str,
    stored_type: str,
    frontmatter: dict[str, str],
    frontmatter_raw: str | None,
) -> None:
    """Write one pre-0026 document row straight through SQL."""
    now = datetime.now(UTC).isoformat()
    engine = make_engine(db_url)
    try:
        with engine.begin() as conn:
            project_id = conn.execute(
                text("SELECT row_id FROM project WHERE slug = 'p'")
            ).scalar_one_or_none()
            if project_id is None:
                conn.execute(
                    text(
                        "INSERT INTO project (slug, title, root_path, config_json, "
                        "created, updated) VALUES ('p', 'P', '/tmp/p', '{}', :now, :now)"
                    ),
                    {"now": now},
                )
                project_id = conn.execute(
                    text("SELECT row_id FROM project WHERE slug = 'p'")
                ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO document (project_id, doc_key, path, type, status, title, "
                    "frontmatter_json, frontmatter_raw, created, last_updated) "
                    "VALUES (:pid, :key, :path, :type, 'active', 'T', :fm, :raw, :now, :now)"
                ),
                {
                    "pid": project_id,
                    "key": doc_key,
                    "path": f"{doc_key}.md",
                    "type": stored_type,
                    "fm": json.dumps(frontmatter),
                    "raw": frontmatter_raw,
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
                key: doc_type
                for key, doc_type in conn.execute(text("SELECT doc_key, type FROM document"))
            }
    finally:
        engine.dispose()


@pytest.fixture
def seeded_pre_0026(db_url: str) -> str:
    """A 0025-era database holding one coerced row per interesting shape."""
    _alembic(db_url, "upgrade", BEFORE)
    _insert_document(
        db_url,
        doc_key="coerced",
        stored_type="module-spec",
        frontmatter={"type": "capability"},
        frontmatter_raw="type: capability",
    )
    _insert_document(
        db_url,
        doc_key="report",
        stored_type="module-spec",
        frontmatter={"type": "audit-report"},
        frontmatter_raw="type: audit-report",
    )
    _insert_document(
        db_url,
        doc_key="unstorable",
        stored_type="module-spec",
        frontmatter={"type": "kickoff-brief"},
        frontmatter_raw="type: kickoff-brief",
    )
    _insert_document(
        db_url,
        doc_key="authored-in-db",
        stored_type="guide",
        frontmatter={},
        frontmatter_raw=None,
    )
    return db_url


def test_upgrade_restores_the_authored_type(seeded_pre_0026: str) -> None:
    _alembic(seeded_pre_0026, "upgrade", "head")

    types = _types(seeded_pre_0026)
    assert types["coerced"] == "capability"
    assert types["report"] == "audit-report"


def test_upgrade_leaves_rows_it_has_no_authority_over(seeded_pre_0026: str) -> None:
    """Only the eight newly-storable types move.

    `kickoff-brief` is still not a `DocumentType`, so its row keeps the
    fallback; a DB-authored `guide` never had a frontmatter type to restore.
    """
    _alembic(seeded_pre_0026, "upgrade", "head")

    types = _types(seeded_pre_0026)
    assert types["unstorable"] == "module-spec"
    assert types["authored-in-db"] == "guide"


def test_upgrade_is_idempotent(seeded_pre_0026: str) -> None:
    _alembic(seeded_pre_0026, "upgrade", "head")
    _alembic(seeded_pre_0026, "downgrade", "-1")
    _alembic(seeded_pre_0026, "upgrade", "head")

    assert _types(seeded_pre_0026)["coerced"] == "capability"


def test_downgrade_runs_and_keeps_the_repaired_data(seeded_pre_0026: str) -> None:
    """Downgrade is a deliberate no-op: re-coercing would destroy it again."""
    _alembic(seeded_pre_0026, "upgrade", "head")
    _alembic(seeded_pre_0026, "downgrade", "-1")

    assert _types(seeded_pre_0026)["coerced"] == "capability"
