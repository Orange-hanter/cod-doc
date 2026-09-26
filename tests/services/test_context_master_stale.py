"""AFT-010 (RFC 27 F11): ``core.master_stale`` рядом с ``master_excerpt``.

Выдержка MASTER читается с диска, а не из БД. Когда документ ``MASTER.md``
дрейфует (``stale_export``/``edited_in_place``), ``context_get`` обязан это
сказать: иначе агент доверяет устаревшим цифрам («126 MCP-тулов, 1639 тестов»).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import context_service, doc_service, import_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

MASTER = """---
title: Master
type: guide
status: active
owner: human:dakh
---
# Master

Preamble.

## Summary

126 MCP-тулов, 1639 тестов.
"""

TARGET = "modules/x"


def _project(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    project = ProjectModel(slug="ms", title="MS", root_path=str(root), config_json={})
    project.created = now
    project.updated = now
    session.add(project)
    session.flush()
    doc_service.create(
        session,
        project_id=project.row_id,
        doc_key=TARGET,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="X",
        author="human:test",
        owner="human:test",
        sensitivity=Sensitivity.INTERNAL,
        preamble="Intro.",
    )
    return project.row_id


def _import_master(session: Session, project_id: int, root: Path) -> int:
    report = import_service.import_or_update_markdown(
        session,
        project_id=project_id,
        doc_key="master",
        raw_markdown=MASTER,
        author="human:test",
        source_sha256=hashlib.sha256(MASTER.encode("utf-8")).hexdigest(),
        path="MASTER.md",
    )
    model = session.get(DocumentModel, report.document.row_id)
    assert model is not None
    model.path = "MASTER.md"
    (root / "MASTER.md").write_text(MASTER, encoding="utf-8")
    return model.row_id


def _get(session: Session, project_id: int, token_budget: int = 8000) -> dict[str, Any]:
    return context_service.context_get(
        session,
        project_id,
        "document",
        TARGET,
        depth="L0",
        token_budget=token_budget,
        master_content=MASTER,
    )


def test_master_in_sync_is_not_stale(engine_with_schema: Engine, tmp_path: Path) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        project_id = _project(session, tmp_path)
        _import_master(session, project_id, tmp_path)

        core = _get(session, project_id)["core"]

    assert core["master_stale"] is False
    assert core["master_excerpt"] == MASTER


def test_master_edited_in_place_is_stale(engine_with_schema: Engine, tmp_path: Path) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        project_id = _project(session, tmp_path)
        _import_master(session, project_id, tmp_path)
        (tmp_path / "MASTER.md").write_text(MASTER + "\nПравка руками.\n", encoding="utf-8")

        core = _get(session, project_id)["core"]

    assert core["master_stale"] is True


def test_master_stale_export_is_stale(engine_with_schema: Engine, tmp_path: Path) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        project_id = _project(session, tmp_path)
        master_id = _import_master(session, project_id, tmp_path)
        doc_service.patch_section(
            session,
            document_id=master_id,
            anchor="summary",
            new_body="149 MCP-тулов.",
            author="human:test",
            reindex=False,
        )

        core = _get(session, project_id)["core"]

    assert core["master_stale"] is True


def test_no_master_document_is_not_stale(engine_with_schema: Engine, tmp_path: Path) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        project_id = _project(session, tmp_path)

        core = _get(session, project_id)["core"]

    assert core["master_stale"] is False
    assert core["master_excerpt"] == MASTER


def test_no_master_content_no_key(engine_with_schema: Engine, tmp_path: Path) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        project_id = _project(session, tmp_path)
        _import_master(session, project_id, tmp_path)

        core = context_service.context_get(session, project_id, "document", TARGET, depth="L0")[
            "core"
        ]

    assert "master_excerpt" not in core
    assert "master_stale" not in core


def test_budget_respected_with_master_stale(engine_with_schema: Engine, tmp_path: Path) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        project_id = _project(session, tmp_path)
        _import_master(session, project_id, tmp_path)
        (tmp_path / "MASTER.md").write_text("drifted\n", encoding="utf-8")

        small = _get(session, project_id, token_budget=50)
        large = _get(session, project_id, token_budget=8000)

    assert small["meta"]["tokens_used"] <= 50
    assert large["meta"]["tokens_used"] <= 8000
    assert large["core"]["master_stale"] is True
