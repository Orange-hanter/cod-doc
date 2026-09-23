"""ADO-213 at the CLI: orphaned sections are named by `doc drift` and removable by `doc import --replace`.

Both halves of the fix have to be reachable from the terminal, because both
halves of the bug were: a human running `doc import` after editing markdown is
how the orphans were created, and `doc drift` is where they should have shown
up and never did.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli.doc import doc
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import doc_service, import_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

TWO_SECTIONS = """---
title: Master
type: guide
status: active
owner: human:dakh
---
# Master

Preamble.

## Executive Summary

Summary text.

## Context Map

Stale map.
"""

ONE_SECTION = """---
title: Master
type: guide
status: active
owner: human:dakh
---
# Master

Preamble.

## Executive Summary

Summary text.
"""


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bootstrap_repo(tmp_path: Path) -> Config:
    """A repo whose DB holds two sections while `master.md` on disk has one.

    The file hash is accepted into `content_sha256_head`, which is the state
    the bug hid behind: no content-hash comparison can object to it.
    """
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name="orphan-proj", path=str(repo)).model_dump()]

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(
            slug="orphan-proj", title="Orphan", root_path=str(repo), config_json={}
        )
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()

        report = import_service.import_or_update_markdown(
            session,
            project_id=project.row_id,
            doc_key="master",
            raw_markdown=TWO_SECTIONS,
            author="human:test",
            source_sha256=_sha(TWO_SECTIONS),
            path="master.md",
        )
        model = session.get(DocumentModel, report.document.row_id)
        assert model is not None
        model.path = "master.md"
        model.content_sha256_head = _sha(ONE_SECTION)
    engine.dispose()

    (repo / "master.md").write_text(ONE_SECTION, encoding="utf-8")
    return cfg


def _anchors(cfg: Config) -> list[str]:
    entry = cfg.get_project("orphan-proj")
    assert entry is not None
    engine = make_engine(f"sqlite:///{entry.path}/.cod-doc/state.db")
    factory = make_session_factory(engine)
    try:
        with transactional(factory) as session:
            d = doc_service.get(session, 1, "master")
            assert d is not None and d.row_id is not None
            return [s.anchor for s in doc_service.get_sections(session, d.row_id)]
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# doc drift                                                                    #
# --------------------------------------------------------------------------- #


def test_drift_names_the_orphan_although_the_document_is_in_sync(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(
        doc,
        ["drift", "master", "--project", "orphan-proj"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "in_sync" in result.output
    assert "Orphan sections (1)" in result.output
    assert "context-map" in result.output


def test_drift_json_carries_the_orphan_anchors(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(
        doc,
        ["drift", "master", "--project", "orphan-proj", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "in_sync"
    assert payload["orphan_sections"] == ["context-map"]


def test_drift_all_counts_orphans_and_lists_an_in_sync_document(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(
        doc,
        ["drift", "--project", "orphan-proj", "--all", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["counts"]["orphan_sections"] == 1
    assert payload["counts"]["in_sync"] == 1
    assert payload["problem_count"] == 1
    assert payload["issues"][0]["doc_key"] == "master"
    assert payload["issues"][0]["orphan_sections"] == ["context-map"]


# --------------------------------------------------------------------------- #
# doc import                                                                   #
# --------------------------------------------------------------------------- #


def test_import_without_replace_keeps_the_orphan_and_warns(tmp_path: Path) -> None:
    """The default stays additive — and stops being silent."""
    cfg = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(
        doc,
        ["import", str(tmp_path / "repo" / "master.md"), "--project", "orphan-proj"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "context-map" in result.output
    assert "--replace" in result.output
    assert _anchors(cfg) == ["executive-summary", "context-map"]


def test_import_with_replace_removes_the_orphan(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(
        doc,
        [
            "import",
            str(tmp_path / "repo" / "master.md"),
            "--project",
            "orphan-proj",
            "--replace",
            "--yes",
        ],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "removed section 'context-map'" in result.output
    assert _anchors(cfg) == ["executive-summary"]


def test_import_with_replace_then_drift_is_quiet(tmp_path: Path) -> None:
    """End to end: the cure `doc drift` recommends clears the signal."""
    cfg = _bootstrap_repo(tmp_path)
    runner = CliRunner()

    runner.invoke(
        doc,
        [
            "import",
            str(tmp_path / "repo" / "master.md"),
            "--project",
            "orphan-proj",
            "--replace",
            "--yes",
        ],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    result = runner.invoke(
        doc,
        ["drift", "master", "--project", "orphan-proj", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["orphan_sections"] == []


# --------------------------------------------------------------------------- #
# ADO-213: `--replace` looks before it leaps                                   #
# --------------------------------------------------------------------------- #

FRONTMATTER_ONLY = """---
title: Master
type: guide
status: active
owner: human:dakh
---
"""


def _import_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "import",
        str(tmp_path / "repo" / "master.md"),
        "--project",
        "orphan-proj",
        *extra,
    ]


def test_replace_asks_before_deleting_and_a_no_changes_nothing(tmp_path: Path) -> None:
    """`doc delete-section` asks for one section; `--replace` asks for N."""
    cfg = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(
        doc,
        _import_argv(tmp_path, "--replace"),
        input="n\n",
        obj={"config": cfg},
    )

    assert result.exit_code != 0
    assert "Delete 1 section(s)" in result.output
    assert "context-map" in result.output
    assert _anchors(cfg) == ["executive-summary", "context-map"]


def test_replace_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(
        doc,
        _import_argv(tmp_path, "--replace", "--dry-run"),
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "would remove section 'context-map'" in result.output
    assert _anchors(cfg) == ["executive-summary", "context-map"]


def test_replace_refuses_a_file_that_parses_to_no_sections(tmp_path: Path) -> None:
    """A truncated write must not be read as "the document has no body"."""
    cfg = _bootstrap_repo(tmp_path)
    (tmp_path / "repo" / "master.md").write_text(FRONTMATTER_ONLY, encoding="utf-8")

    result = CliRunner().invoke(
        doc,
        _import_argv(tmp_path, "--replace", "--yes"),
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "no sections at all" in result.output
    assert _anchors(cfg) == ["executive-summary", "context-map"]


def test_replace_with_force_goes_through_the_refusal(tmp_path: Path) -> None:
    cfg = _bootstrap_repo(tmp_path)
    (tmp_path / "repo" / "master.md").write_text(FRONTMATTER_ONLY, encoding="utf-8")

    result = CliRunner().invoke(
        doc,
        _import_argv(tmp_path, "--replace", "--yes", "--force"),
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert _anchors(cfg) == []


def test_zero_byte_file_is_refused_too(tmp_path: Path) -> None:
    """`click.Path(exists=True)` accepts 0 bytes; the service is the guard."""
    cfg = _bootstrap_repo(tmp_path)
    (tmp_path / "repo" / "master.md").write_text("", encoding="utf-8")

    result = CliRunner().invoke(
        doc,
        _import_argv(tmp_path, "--replace", "--yes"),
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert _anchors(cfg) == ["executive-summary", "context-map"]
