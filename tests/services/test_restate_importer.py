"""COD-051: bulk-import pipelines (docs + legacy tasks)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import yaml

from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import (
    DocumentRepository,
    ProjectRepository,
)
from cod_doc.services import plan_service, projection_service, restate_importer, task_service
from cod_doc.services.projection_service import DriftStatus

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str, root: Path) -> int:
    """Insert a Project row, return its row_id."""
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug=slug, title=slug, root_path=str(root), config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


# ── docs walker ────────────────────────────────────────────────────────


def test_walk_doc_files_picks_md_skips_dotdirs(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# r")
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "arch.md").write_text("# a")
    (tmp_path / "Docs" / "image.png").write_text("png")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD.md").write_text("ignored")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.md").write_text("ignored")
    (tmp_path / "cod_doc.egg-info").mkdir()
    (tmp_path / "cod_doc.egg-info" / "PKG-INFO.md").write_text("ignored")

    out = restate_importer._walk_doc_files(tmp_path)
    rels = {p.relative_to(tmp_path).as_posix() for p in out}
    assert "README.md" in rels
    assert "Docs/arch.md" in rels
    assert ".git/HEAD.md" not in rels
    assert "node_modules/x.md" not in rels
    assert "cod_doc.egg-info/PKG-INFO.md" not in rels
    assert "Docs/image.png" not in rels


# ── SYM-004: exclude-паттерны ──────────────────────────────────────────


def _seed_exclude_tree(root: Path) -> None:
    """Дерево из критерия приёмки: стенды, аналитика, docs и архив."""
    (root / "experiments" / "stand-01").mkdir(parents=True)
    (root / "experiments" / "stand-02").mkdir(parents=True)
    (root / "experiments" / "stand-01" / "notes.md").write_text("# stand 1")
    (root / "experiments" / "stand-02" / "README.md").write_text("# stand 2")
    (root / "experiments" / "analysis.md").write_text("# analysis")
    (root / "docs" / "_archive" / "deep").mkdir(parents=True)
    (root / "docs" / "arch.md").write_text("# arch")
    (root / "docs" / "_archive" / "old.md").write_text("# old")
    (root / "docs" / "_archive" / "deep" / "older.md").write_text("# older")


def _rels(root: Path, files: list[Path]) -> set[str]:
    return {p.relative_to(root).as_posix() for p in files}


def test_walk_doc_files_excludes_by_pattern(tmp_path: Path) -> None:
    """Дословная семантика критерия: 'experiments/stand*' убирает стенды."""
    _seed_exclude_tree(tmp_path)

    rels = _rels(
        tmp_path, restate_importer._walk_doc_files(tmp_path, exclude=["experiments/stand*"])
    )

    assert "experiments/stand-01/notes.md" not in rels
    assert "experiments/stand-02/README.md" not in rels
    # Соседи по каталогу не пострадали.
    assert "experiments/analysis.md" in rels
    assert "docs/arch.md" in rels


def test_walk_doc_files_exclude_accepts_multiple_patterns(tmp_path: Path) -> None:
    _seed_exclude_tree(tmp_path)

    rels = _rels(
        tmp_path,
        restate_importer._walk_doc_files(tmp_path, exclude=["experiments/stand*", "*/_archive"]),
    )

    assert not any(r.startswith("experiments/stand") for r in rels)
    assert not any("_archive" in r for r in rels)
    assert rels == {"experiments/analysis.md", "docs/arch.md"}


def test_walk_doc_files_exclude_matches_directory_subtree(tmp_path: Path) -> None:
    """Паттерн без wildcard'ов — это каталог, а не только файл ровно по пути."""
    _seed_exclude_tree(tmp_path)

    rels = _rels(tmp_path, restate_importer._walk_doc_files(tmp_path, exclude=["docs/_archive"]))

    assert "docs/_archive/old.md" not in rels
    assert "docs/_archive/deep/older.md" not in rels
    assert "docs/arch.md" in rels


def test_walk_doc_files_exclude_normalises_pattern(tmp_path: Path) -> None:
    """'./experiments/stand*/' == 'experiments/stand*'."""
    _seed_exclude_tree(tmp_path)

    rels = _rels(
        tmp_path, restate_importer._walk_doc_files(tmp_path, exclude=["./experiments/stand*/"])
    )

    assert not any(r.startswith("experiments/stand") for r in rels)
    assert "experiments/analysis.md" in rels


def test_walk_doc_files_exclude_empty_is_noop(tmp_path: Path) -> None:
    """None, () и пустая строка не должны ничего вычищать.

    Пустой паттерн особенно опасен: '' заматчил бы корень репозитория, если
    его не отбросить, и импорт молча стал бы нулевым.
    """
    _seed_exclude_tree(tmp_path)
    baseline = _rels(tmp_path, restate_importer._walk_doc_files(tmp_path))

    assert _rels(tmp_path, restate_importer._walk_doc_files(tmp_path, exclude=None)) == baseline
    assert _rels(tmp_path, restate_importer._walk_doc_files(tmp_path, exclude=())) == baseline
    assert (
        _rels(tmp_path, restate_importer._walk_doc_files(tmp_path, exclude=["", "  "])) == baseline
    )
    assert baseline == {
        "experiments/stand-01/notes.md",
        "experiments/stand-02/README.md",
        "experiments/analysis.md",
        "docs/arch.md",
        "docs/_archive/old.md",
        "docs/_archive/deep/older.md",
    }


def test_walk_doc_files_exclude_does_not_consume_max_files(tmp_path: Path) -> None:
    """Исключённые файлы не расходуют cap: фильтр стоит до append."""
    (tmp_path / "experiments").mkdir()
    for i in range(3):
        (tmp_path / "experiments" / f"stand-{i}.md").write_text("# stand")
    (tmp_path / "a-keep.md").write_text("# a")
    (tmp_path / "z-keep.md").write_text("# z")

    rels = _rels(
        tmp_path,
        restate_importer._walk_doc_files(tmp_path, max_files=2, exclude=["experiments/stand*"]),
    )

    assert rels == {"a-keep.md", "z-keep.md"}


def test_import_docs_honours_exclude(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: exclude долетает из import_docs в walker."""
    _seed_exclude_tree(tmp_path)

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "excl", tmp_path)
        summary = restate_importer.import_docs(
            session,
            repo_root=tmp_path,
            project_id=project_id,
            exclude=["experiments/stand*", "docs/_archive"],
        )

    assert summary.errors == []
    assert summary.imported == 2
    assert {f.replace("\\", "/") for f in summary.files} == {
        "experiments/analysis.md",
        "docs/arch.md",
    }


def test_import_docs_creates_documents(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "README.md").write_text("# Hello\n\nIntro paragraph.")
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "arch.md").write_text("# Arch\n\n## Modules\n\nFoo bar.")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "demo", tmp_path)
        summary = restate_importer.import_docs(session, repo_root=tmp_path, project_id=project_id)
    assert summary.imported == 2
    assert summary.skipped == 0
    assert summary.errors == []

    with transactional(factory) as session:
        repo = DocumentRepository(session)
        docs = repo.list_for_project(project_id)
        keys = {d.doc_key for d in docs}
    assert "README" in keys
    assert "Docs/arch" in keys


def test_import_docs_reports_in_sync_drift(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-023: import must persist projection_hash/content_sha256_head, so a
    freshly imported corpus reports in_sync — not stale_export — in drift."""
    (tmp_path / "README.md").write_text("# Hello\n\nIntro paragraph.")
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "arch.md").write_text("# Arch\n\n## Modules\n\nFoo bar.")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "demo", tmp_path)
        summary = restate_importer.import_docs(session, repo_root=tmp_path, project_id=project_id)
    assert summary.imported == 2
    assert summary.errors == []

    with transactional(factory) as session:
        report = projection_service.detect_project_drift(session, project_id, root_path=tmp_path)
    assert report.counts[DriftStatus.STALE_EXPORT.value] == 0
    assert report.counts[DriftStatus.IN_SYNC.value] == 2
    assert report.issues == []


def test_import_docs_is_idempotent(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Re-running over the same files counts them as skipped, not errors."""
    (tmp_path / "README.md").write_text("# Hello")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "demo", tmp_path)
        first = restate_importer.import_docs(session, repo_root=tmp_path, project_id=project_id)
    assert first.imported == 1

    with transactional(factory) as session:
        again = restate_importer.import_docs(session, repo_root=tmp_path, project_id=project_id)
    assert again.imported == 0
    assert again.skipped == 1
    assert again.errors == []


def test_import_docs_handles_empty_repo(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "empty", tmp_path)
        summary = restate_importer.import_docs(session, repo_root=tmp_path, project_id=project_id)
    assert summary.imported == 0
    assert summary.skipped == 0


def test_import_docs_dry_run_via_rollback(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Caller rolling back the transaction means the DB is untouched."""
    (tmp_path / "a.md").write_text("# a")
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "demo", tmp_path)
        summary = restate_importer.import_docs(session, repo_root=tmp_path, project_id=project_id)
        assert summary.imported == 1
        session.rollback()

    # New transaction → no documents persisted because rollback also dropped
    # the project insert above. Re-seed and check from scratch.
    with transactional(factory) as session:
        project_id = _seed_project(session, "demo", tmp_path)
        repo = DocumentRepository(session)
        assert repo.list_for_project(project_id) == []


# ── legacy tasks ───────────────────────────────────────────────────────


def _write_legacy_yaml(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"tasks": entries}, allow_unicode=True))


def test_import_legacy_tasks_creates_plan_and_tasks(
    tmp_path: Path,
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    yaml_path = tmp_path / ".cod-doc" / "tasks.yaml"
    _write_legacy_yaml(
        yaml_path,
        [
            {
                "id": "abc12345",
                "title": "Document API auth",
                "description": "Cover the JWT flow",
                "priority": 2,
                "status": "in_progress",
                "result": None,
            },
            {
                "id": "def67890",
                "title": "Fix flaky test",
                "description": "",
                "priority": 5,
                "status": "done",
                "result": "merged",
            },
            {
                "id": "ghi11111",
                "title": "Investigate spike",
                "priority": 1,
                "status": "blocked",
            },
        ],
    )
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "legacy", tmp_path)
        summary = restate_importer.import_legacy_tasks(
            session, yaml_path=yaml_path, project_id=project_id
        )
    assert summary.imported == 3
    assert summary.errors == []
    assert summary.plan_scope == "imported-legacy"

    with transactional(factory) as session:
        plans = plan_service.list_for_project(session, project_id)
        assert any(p.scope == "imported-legacy" for p in plans)
        all_tasks = task_service.list_for_project(session, project_id)
        titles = {t.title for t in all_tasks}
        assert {"Document API auth", "Fix flaky test", "Investigate spike"} == titles
        # Status mapping: legacy 'in_progress' → DB 'in-progress'.
        in_progress = [t for t in all_tasks if t.status.value == "in-progress"]
        assert len(in_progress) == 1
        # Legacy 'blocked' → PENDING + blocked_reason set.
        blocked = next(t for t in all_tasks if t.title == "Investigate spike")
        assert blocked.status.value == "pending"
        assert blocked.blocked_reason and "blocked" in blocked.blocked_reason
        # Description carries the legacy id + result.
        flaky = next(t for t in all_tasks if t.title == "Fix flaky test")
        assert flaky.description and "def67890" in flaky.description
        assert "merged" in flaky.description


def test_import_legacy_tasks_missing_yaml_returns_error(
    tmp_path: Path,
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "no-yaml", tmp_path)
        summary = restate_importer.import_legacy_tasks(
            session,
            yaml_path=tmp_path / ".cod-doc" / "tasks.yaml",
            project_id=project_id,
        )
    assert summary.imported == 0
    assert any("not found" in e for e in summary.errors)


def test_import_legacy_tasks_skips_entries_without_title(
    tmp_path: Path,
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    yaml_path = tmp_path / ".cod-doc" / "tasks.yaml"
    _write_legacy_yaml(
        yaml_path,
        [
            {"id": "1", "title": ""},
            {"id": "2"},
            {"id": "3", "title": "Real task", "priority": 3},
        ],
    )
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "demo", tmp_path)
        summary = restate_importer.import_legacy_tasks(
            session, yaml_path=yaml_path, project_id=project_id
        )
    assert summary.imported == 1
    assert summary.skipped == 2


def test_import_legacy_tasks_reuses_existing_plan(
    tmp_path: Path,
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    """A second import run does not create another 'imported-legacy' plan."""
    yaml1 = tmp_path / ".cod-doc" / "tasks.yaml"
    _write_legacy_yaml(yaml1, [{"id": "a", "title": "First"}])
    yaml2 = tmp_path / ".cod-doc" / "tasks2.yaml"
    _write_legacy_yaml(yaml2, [{"id": "b", "title": "Second"}])

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "demo", tmp_path)
        restate_importer.import_legacy_tasks(session, yaml_path=yaml1, project_id=project_id)
    with transactional(factory) as session:
        restate_importer.import_legacy_tasks(session, yaml_path=yaml2, project_id=project_id)
    with transactional(factory) as session:
        plans = [
            p
            for p in plan_service.list_for_project(session, project_id)
            if p.scope == "imported-legacy"
        ]
    assert len(plans) == 1


def test_import_legacy_tasks_invokes_progress_callback(
    tmp_path: Path,
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    """WEB-031: progress fires once per entry with (done, total, label),
    including for skipped (title-less) rows, and never breaks the import."""
    yaml_path = tmp_path / ".cod-doc" / "tasks.yaml"
    _write_legacy_yaml(
        yaml_path,
        [
            {"id": "a1", "title": "First", "priority": 2, "status": "pending"},
            {"id": "a2", "priority": 3, "status": "pending"},  # no title → skipped
            {"id": "a3", "title": "Third", "priority": 1, "status": "done"},
        ],
    )
    calls: list[tuple[int, int, str]] = []
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "prog", tmp_path)
        summary = restate_importer.import_legacy_tasks(
            session,
            yaml_path=yaml_path,
            project_id=project_id,
            progress=lambda done, total, label: calls.append((done, total, label)),
        )

    assert summary.imported == 2
    assert summary.skipped == 1
    # One callback per entry, monotonically increasing done, constant total.
    assert [done for done, _, _ in calls] == [1, 2, 3]
    assert {total for _, total, _ in calls} == {3}
    labels = [label for _, _, label in calls]
    assert labels[0] == "First"
    assert labels[1] == "a2"  # title-less row falls back to legacy id
    assert labels[2] == "Third"


def test_import_legacy_tasks_progress_exception_is_swallowed(
    tmp_path: Path,
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    """A throwing progress callback must never break the migration."""
    yaml_path = tmp_path / ".cod-doc" / "tasks.yaml"
    _write_legacy_yaml(
        yaml_path,
        [{"id": "x1", "title": "Only", "priority": 2, "status": "pending"}],
    )

    def _boom(done: int, total: int, label: str) -> None:
        raise RuntimeError("progress sink down")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "boom", tmp_path)
        summary = restate_importer.import_legacy_tasks(
            session, yaml_path=yaml_path, project_id=project_id, progress=_boom
        )
    assert summary.imported == 1
    assert summary.errors == []
