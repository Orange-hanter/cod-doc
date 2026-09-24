"""AFT-003 (RFC 27 F4): `limit` caps the `issues` rows, not the drift scan.

`detect_project_drift(limit=N)` used to cut the document list before scanning,
so `ctx_drift(limit=5)` checked the first five documents and reported "clean"
while the sixth drifted. The corpus here is five documents ordered by
`doc_key` (the `list_for_project` order); drift sits at the end of it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import import_service, projection_service
from tests._alembic import run_alembic
from tests.test_drift_metadata_mismatch_json import _mcp_tool

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

SLUG = "trunc-proj"
DOC_KEYS = ("doc-a", "doc-b", "doc-c", "doc-d", "doc-e")


def _markdown(doc_key: str) -> str:
    return f"""---
title: {doc_key}
type: guide
status: active
owner: human:dakh
---
# {doc_key}

Preamble.

## Body

Text of {doc_key}.
"""


def _bootstrap_repo(tmp_path: Path, edited: tuple[str, ...]) -> Path:
    """Five imported documents; files of `edited` are rewritten after import."""
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(slug=SLUG, title="Trunc", root_path=str(repo), config_json={})
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()

        for doc_key in DOC_KEYS:
            raw = _markdown(doc_key)
            path = f"{doc_key}.md"
            report = import_service.import_or_update_markdown(
                session,
                project_id=project.row_id,
                doc_key=doc_key,
                raw_markdown=raw,
                author="human:test",
                source_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                path=path,
            )
            model = session.get(DocumentModel, report.document.row_id)
            assert model is not None
            model.path = path
            (repo / path).write_text(raw, encoding="utf-8")
    engine.dispose()

    for doc_key in edited:
        (repo / f"{doc_key}.md").write_text(
            _markdown(doc_key) + "\n## Added on disk\n\nEdited in place.\n", encoding="utf-8"
        )
    return repo


def test_ctx_drift_limit_scans_whole_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _bootstrap_repo(tmp_path, edited=("doc-e",))

    payload = _mcp_tool(monkeypatch, repo, "ctx_drift")(project=SLUG, limit=1)

    assert [row["doc_key"] for row in payload["issues"]] == ["doc-e"]
    assert payload["issues"][0]["status"] == "edited_in_place"
    assert payload["truncated"] is False
    assert payload["total_docs"] == 5
    assert payload["problem_count"] == 1


def test_ctx_drift_limit_truncates_issues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _bootstrap_repo(tmp_path, edited=("doc-d", "doc-e"))
    ctx_drift = _mcp_tool(monkeypatch, repo, "ctx_drift")

    limited = ctx_drift(project=SLUG, limit=1)
    assert len(limited["issues"]) == 1
    assert limited["truncated"] is True
    assert limited["problem_count"] == 2

    full = ctx_drift(project=SLUG)
    assert len(full["issues"]) == 2
    assert full["truncated"] is False


def test_counts_do_not_depend_on_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _bootstrap_repo(tmp_path, edited=("doc-d", "doc-e"))
    ctx_drift = _mcp_tool(monkeypatch, repo, "ctx_drift")

    for payload in (ctx_drift(project=SLUG, limit=1), ctx_drift(project=SLUG)):
        assert payload["problem_count"] == 2
        assert payload["total_docs"] == 5
        assert payload["counts"]["edited_in_place"] == 2
        assert payload["counts"]["in_sync"] == 3


def test_detect_project_drift_limit_caps_issues_only(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path, edited=("doc-d", "doc-e"))
    engine = make_engine(f"sqlite:///{repo}/.cod-doc/state.db")
    try:
        with transactional(make_session_factory(engine)) as session:
            report = projection_service.detect_project_drift(
                session, 1, root_path=repo.resolve(), limit=1
            )
    finally:
        engine.dispose()

    assert report.total_docs == 5
    assert len(report.issues) == 1
    assert report.problem_count == 2
    assert report.truncated is True
