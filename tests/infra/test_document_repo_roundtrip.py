"""ADO-036: DocumentRepository round-trip сохраняет поля миграции 0025.

Finding C2 контракт-аудита ADO-034: `Document` dataclass и маппинги
`_to_domain`/`_to_model` не знали про `frontmatter_raw` / `title_in_body` /
`content_sha256_head` — обновление документа через репозиторий зануляло
verbatim-frontmatter и accepted-hash, что ломало drift/export-контракт
проекций (см. projection_service/_frontmatter.py, drift.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import Document, DocumentStatus, DocumentType
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.infra.repositories.document_repo import DocumentRepository

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'doc_repo.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


_FRONTMATTER_RAW = "type: guide\nstatus: active\ncreated: 2026-08-29\n"
_SHA_HEAD = "a" * 64


def _make_doc(project_id: int) -> Document:
    return Document(
        project_id=project_id,
        doc_key="guide/round-trip",
        path="docs/guide/round-trip.md",
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        title="Round-trip",
        frontmatter_raw=_FRONTMATTER_RAW,
        title_in_body=False,
        content_sha256_head=_SHA_HEAD,
    )


def test_document_repo_roundtrip_preserves_projection_fields(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with factory() as session:
        session.add(ProjectModel(slug="rt", title="RT", root_path="/tmp/rt"))
        session.flush()
        project_id = session.execute(select(ProjectModel.row_id)).scalar_one()

        repo = DocumentRepository(session)
        created = repo.add(_make_doc(project_id))
        session.commit()

        # Читаем через репозиторий (domain view)…
        loaded = repo.get_by_key(project_id, "guide/round-trip")
        assert loaded is not None
        assert loaded.frontmatter_raw == _FRONTMATTER_RAW
        assert loaded.title_in_body is False
        assert loaded.content_sha256_head == _SHA_HEAD

        # …и обновляем через репозиторий, как это делают сервисы: domain →
        # _to_model → merge. Поля проекционной фиделности не должны
        # занулиться.
        loaded.title = "Round-trip v2"
        session.merge(repo._to_model(loaded))
        session.commit()

        # Строку БД проверяем напрямую, не через dataclass.
        row = session.execute(
            select(DocumentModel).where(DocumentModel.row_id == created.row_id)
        ).scalar_one()
        assert row.title == "Round-trip v2"
        assert row.frontmatter_raw == _FRONTMATTER_RAW
        assert row.title_in_body is False
        assert row.content_sha256_head == _SHA_HEAD
