"""OBI-030: repo_index_service — scan + AST + .gitignore + queries."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ProjectModel,
    RepoFileModel,
    RepoSymbolModel,
)
from cod_doc.services import repo_index_service


def _seed(session) -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="rip", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


# ----------------------------------------------------------------- #
# Language detection                                                  #
# ----------------------------------------------------------------- #


def test_language_detection_python() -> None:
    assert repo_index_service._detect_language(Path("a.py")) == "python"


def test_language_detection_misc() -> None:
    assert repo_index_service._detect_language(Path("a.ts")) == "typescript"
    assert repo_index_service._detect_language(Path("a.go")) == "go"
    assert repo_index_service._detect_language(Path("a.unknown")) is None


# ----------------------------------------------------------------- #
# AST extraction                                                      #
# ----------------------------------------------------------------- #


def test_extract_python_finds_function_class_method_constant() -> None:
    source = (
        "MAGIC = 42\n"
        "def hello():\n    pass\n"
        "class Foo:\n    def bar(self):\n        pass\n"
        "import os\n"
        "from sqlalchemy import select\n"
    )
    symbols, imports = repo_index_service._extract_python(source)
    names_kinds = {(s[0], s[1]) for s in symbols}
    assert ("MAGIC", "constant") in names_kinds
    assert ("hello", "function") in names_kinds
    assert ("Foo", "class") in names_kinds
    assert ("bar", "method") in names_kinds

    modules = {m for m, _ in imports}
    assert "os" in modules
    assert "sqlalchemy" in modules


def test_extract_python_tolerates_syntax_errors() -> None:
    symbols, imports = repo_index_service._extract_python("def broken(:\n    pass")
    assert symbols == []
    assert imports == []


# ----------------------------------------------------------------- #
# scan_project — end-to-end                                          #
# ----------------------------------------------------------------- #


def _write_demo_repo(repo: Path) -> None:
    """Create a small heterogeneous repo for scanning."""
    (repo / ".gitignore").write_text("ignored_dir/\n*.log\n")
    (repo / "a.py").write_text("def alpha(): pass\nclass Beta:\n    def gamma(self): pass\n")
    (repo / "b.py").write_text("from a import alpha\n")
    (repo / "c.ts").write_text("export function delta() {}\n")
    (repo / "data.json").write_text("{}\n")
    (repo / "ignored_dir").mkdir()
    (repo / "ignored_dir" / "x.py").write_text("def ignored(): pass\n")
    (repo / "trace.log").write_text("noise\n")


def test_scan_project_indexes_files_and_skips_gitignored(
    engine_with_schema,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    repo = tmp_path / "demo"
    repo.mkdir()
    _write_demo_repo(repo)

    with transactional(factory) as session:
        result = repo_index_service.scan_project(
            session,
            project_id=1,
            repo_path=repo,
        )
    assert result["files"] >= 4  # a.py + b.py + c.ts + data.json + .gitignore
    assert result["skipped_gitignore"] >= 2  # ignored_dir/x.py + trace.log

    with transactional(factory) as session:
        paths = sorted(
            r.path
            for r in session.execute(
                __import__("sqlalchemy").select(RepoFileModel).where(RepoFileModel.project_id == 1)
            ).scalars()
        )
    assert "a.py" in paths
    assert "b.py" in paths
    assert "c.ts" in paths
    assert "ignored_dir/x.py" not in paths
    assert "trace.log" not in paths


def test_scan_extracts_python_symbols_and_imports(
    engine_with_schema,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    repo = tmp_path / "demo2"
    repo.mkdir()
    _write_demo_repo(repo)
    with transactional(factory) as session:
        repo_index_service.scan_project(session, project_id=1, repo_path=repo)

    with transactional(factory) as session:
        hits = repo_index_service.find_symbol(session, 1, "alpha")
        importers = repo_index_service.find_importers(session, 1, "a")
    assert any(f.path == "a.py" for f, _ in hits)
    assert any(f.path == "b.py" for f in importers)


def test_scan_is_idempotent(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Re-running scan replaces previous rows without piling up duplicates."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    repo = tmp_path / "demo3"
    repo.mkdir()
    _write_demo_repo(repo)

    with transactional(factory) as session:
        repo_index_service.scan_project(session, project_id=1, repo_path=repo)
    with transactional(factory) as session:
        repo_index_service.scan_project(session, project_id=1, repo_path=repo)
    with transactional(factory) as session:
        from sqlalchemy import func

        total_files = session.execute(
            __import__("sqlalchemy")
            .select(func.count())
            .select_from(RepoFileModel)
            .where(RepoFileModel.project_id == 1)
        ).scalar_one()
        total_syms = session.execute(
            __import__("sqlalchemy").select(func.count()).select_from(RepoSymbolModel)
        ).scalar_one()
    # Only one set of rows — no duplicates.
    # a.py + b.py + c.ts + data.json + .gitignore (5 files in non-ignored set).
    assert int(total_files) == 5
    # a.py has alpha + Beta + gamma → 3 symbols; b.py none.
    assert int(total_syms) == 3


def test_scan_rejects_non_directory(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    import pytest as _pytest

    with _pytest.raises(ValueError, match="not a directory"):
        with transactional(factory) as session:
            repo_index_service.scan_project(
                session,
                project_id=1,
                repo_path=tmp_path / "no_such",
            )


# ----------------------------------------------------------------- #
# Performance budget                                                  #
# ----------------------------------------------------------------- #


def test_scan_under_5s_on_synthetic_thousand_files(
    engine_with_schema,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    """Acceptance asks ≤5s on 1000 files. Synthesise that many tiny files."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    repo = tmp_path / "synthetic"
    repo.mkdir()
    # 1000 Python files with one function each.
    for i in range(1000):
        sub = repo / f"pkg_{i // 100:02d}"
        sub.mkdir(exist_ok=True)
        (sub / f"mod_{i:04d}.py").write_text(f"def fn_{i}(): pass\n")
    start = time.monotonic()
    with transactional(factory) as session:
        result = repo_index_service.scan_project(
            session,
            project_id=1,
            repo_path=repo,
            max_files=2000,
        )
    elapsed = time.monotonic() - start
    assert result["files"] == 1000
    assert elapsed < 5.0, f"scan took {elapsed:.2f}s, must be <5s for 1000 files"
