"""COD-051: cod-doc import CLI smoke tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import yaml
from click.testing import CliRunner

from cod_doc.cli.cmd_import import import_cmd
from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


def _migrate(db_path: Path) -> None:
    """Apply alembic migrations to a fresh sqlite file."""
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")


def _bootstrap(tmp_path: Path) -> tuple[Config, ProjectEntry]:
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _migrate(db_path)

    entry = ProjectEntry(name="restate", path=str(repo))
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.add_project(entry)

    from cod_doc.infra.db import make_engine

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="restate", title="Restate", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
    engine.dispose()
    return cfg, entry


def test_cli_import_docs_dry_run_does_not_persist(tmp_path: Path) -> None:
    cfg, _entry = _bootstrap(tmp_path)
    (tmp_path / "repo" / "README.md").write_text("# README")

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["docs", "restate", "--dry-run"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Imported: 1" in result.output
    assert "Dry-run" in result.output

    # Re-running without --dry-run actually persists.
    result2 = runner.invoke(
        import_cmd,
        ["docs", "restate"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert result2.exit_code == 0, result2.output
    assert "Imported: 1" in result2.output

    # Third call: idempotent — file is now skipped.
    result3 = runner.invoke(
        import_cmd,
        ["docs", "restate"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert "Imported: 0" in result3.output
    assert "Skipped (already in DB): 1" in result3.output


# ── SYM-004: --exclude ─────────────────────────────────────────────────


def _seed_stands(repo: Path) -> None:
    """Репозиторий со стендами: ровно та форма, ради которой заведён флаг."""
    (repo / "experiments" / "stand-01").mkdir(parents=True)
    (repo / "experiments" / "stand-02").mkdir(parents=True)
    (repo / "experiments" / "stand-01" / "notes.md").write_text("# stand 1")
    (repo / "experiments" / "stand-02" / "log.md").write_text("# stand 2")
    (repo / "README.md").write_text("# README")


def test_cli_import_docs_exclude_hides_stand_files_on_dry_run(tmp_path: Path) -> None:
    """Дословный критерий приёмки SYM-004.

    `import docs -p x --exclude 'experiments/stand*' --dry-run` не показывает
    файлы стендов — и именно показывает остальные, иначе «не показывает» было
    бы выполнено пустым выводом.
    """
    cfg, _entry = _bootstrap(tmp_path)
    _seed_stands(tmp_path / "repo")

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["docs", "-p", "restate", "--exclude", "experiments/stand*", "--dry-run"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "stand-01" not in result.output
    assert "stand-02" not in result.output
    assert "README.md" in result.output
    assert "Imported: 1" in result.output
    assert "Dry-run" in result.output


def test_cli_import_docs_dry_run_lists_files(tmp_path: Path) -> None:
    """Без --exclude тот же прогон показывает все три файла.

    Контрольный кейс: он доказывает, что предыдущий тест ловит фильтрацию, а
    не просто отсутствие листинга в выводе.
    """
    cfg, _entry = _bootstrap(tmp_path)
    _seed_stands(tmp_path / "repo")

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["docs", "restate", "--dry-run"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Imported: 3" in result.output
    assert "stand-01" in result.output
    assert "stand-02" in result.output
    assert "README.md" in result.output


def test_cli_import_docs_exclude_is_repeatable(tmp_path: Path) -> None:
    """multiple=True: два --exclude в одном вызове складываются."""
    cfg, _entry = _bootstrap(tmp_path)
    repo = tmp_path / "repo"
    _seed_stands(repo)
    (repo / "_archive").mkdir()
    (repo / "_archive" / "old.md").write_text("# old")

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        [
            "docs",
            "restate",
            "--exclude",
            "experiments/stand*",
            "--exclude",
            "_archive",
            "--dry-run",
        ],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "stand-01" not in result.output
    assert "old.md" not in result.output
    assert "README.md" in result.output
    assert "Imported: 1" in result.output


def test_cli_import_all_passes_exclude(tmp_path: Path) -> None:
    """`import all` обязан пробросить exclude в docs-пайплайн, а не потерять."""
    cfg, _entry = _bootstrap(tmp_path)
    _seed_stands(tmp_path / "repo")

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["all", "restate", "--exclude", "experiments/stand*", "--dry-run"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "stand-01" not in result.output
    assert "README.md" in result.output


def test_cli_import_docs_rejects_conflicting_project(tmp_path: Path) -> None:
    """Позиционный проект и -p, указывающие в разное, — ошибка, не тихий выбор."""
    cfg, _entry = _bootstrap(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["docs", "restate", "-p", "other", "--dry-run"],
        obj={"config": cfg},
    )

    assert result.exit_code != 0
    assert "дважды" in result.output


def test_cli_import_docs_requires_a_project(tmp_path: Path) -> None:
    cfg, _entry = _bootstrap(tmp_path)

    runner = CliRunner()
    result = runner.invoke(import_cmd, ["docs", "--dry-run"], obj={"config": cfg})

    assert result.exit_code != 0
    assert "Не указан проект" in result.output


def test_cli_import_docs_reports_coerced_frontmatter(tmp_path: Path) -> None:
    """ADO-015: a bulk import of a foreign corpus says what it had to bend.

    This is where the coercion hid best — one command, hundreds of files, a
    single "Imported: N" line. `capability` now stores as authored (no
    warning); `kickoff-brief` is not a cod-doc type and gets reported.
    """
    cfg, _entry = _bootstrap(tmp_path)
    repo = tmp_path / "repo"
    (repo / "clean.md").write_text("---\ntype: capability\n---\n\n# Clean\n", encoding="utf-8")
    (repo / "alien.md").write_text(
        "---\ntype: kickoff-brief\nstatus: living\n---\n\n# Alien\n", encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["docs", "restate"],
        obj={"config": cfg},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Imported: 2" in result.output
    assert "Warnings: 2" in result.output
    assert "alien.md: type: 'kickoff-brief' → 'module-spec' (unknown value)" in result.output
    assert "alien.md: status: 'living' → 'active' (foreign spelling)" in result.output
    assert "clean.md" not in result.output


def test_cli_import_legacy_tasks_runs_end_to_end(tmp_path: Path) -> None:
    cfg, _entry = _bootstrap(tmp_path)
    yaml_path = tmp_path / "repo" / ".cod-doc" / "tasks.yaml"
    yaml_path.write_text(
        yaml.dump(
            {
                "tasks": [
                    {"id": "x1", "title": "First", "priority": 2, "status": "pending"},
                    {"id": "x2", "title": "Second", "priority": 5, "status": "done"},
                ]
            },
            allow_unicode=True,
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["legacy-tasks", "restate"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Imported: 2" in result.output
    assert "Plan scope:" in result.output


def test_cli_link_backfill_syncs_sections(tmp_path: Path) -> None:
    """COD-079: `cod-doc link backfill` walks every section and calls
    sync_section so existing imports gain link rows."""
    cfg, entry = _bootstrap(tmp_path)

    from cod_doc.cli.link import link as link_group
    from cod_doc.domain.entities import (
        DocumentStatus,
        DocumentType,
        Sensitivity,
    )
    from cod_doc.infra.db import make_engine, make_session_factory, transactional
    from cod_doc.infra.repositories import LinkRepository
    from cod_doc.services import doc_service

    db_path = tmp_path / "repo" / ".cod-doc" / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    section_id: int | None = None
    with transactional(factory) as session:
        from cod_doc.infra.repositories import ProjectRepository

        proj = ProjectRepository(session).get_by_slug(entry.name)
        # Two docs; doc-a links to doc-b.
        doc_a = doc_service.create(
            session,
            project_id=proj.row_id,
            doc_key="doc-a",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="A",
            author="human:test",
            owner="human:test",
            sensitivity=Sensitivity.INTERNAL,
        )
        doc_service.create(
            session,
            project_id=proj.row_id,
            doc_key="doc-b",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="B",
            author="human:test",
            owner="human:test",
            sensitivity=Sensitivity.INTERNAL,
        )
        sec = doc_service.add_section(
            session,
            document_id=doc_a.row_id,
            anchor="ref",
            heading="Ref",
            level=2,
            position=0,
            body="See [B](doc-b).",
            author="human:test",
        )
        section_id = sec.row_id
        # Wipe link rows to simulate pre-COD-079 state.
        LinkRepository(session).delete_for_section(section_id)
    engine.dispose()

    runner = CliRunner()
    result = runner.invoke(
        link_group,
        ["backfill", "-p", "restate"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "backfilled 1 link" in result.output

    # Verify the row landed.
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        rows = LinkRepository(session).list_for_section(section_id)
    engine.dispose()
    assert len(rows) == 1


def test_cli_link_backfill_dry_run_does_not_persist(tmp_path: Path) -> None:
    cfg, entry = _bootstrap(tmp_path)

    from cod_doc.cli.link import link as link_group
    from cod_doc.domain.entities import (
        DocumentStatus,
        DocumentType,
        Sensitivity,
    )
    from cod_doc.infra.db import make_engine, make_session_factory, transactional
    from cod_doc.infra.repositories import LinkRepository, ProjectRepository
    from cod_doc.services import doc_service

    db_path = tmp_path / "repo" / ".cod-doc" / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    section_id: int | None = None
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(entry.name)
        doc = doc_service.create(
            session,
            project_id=proj.row_id,
            doc_key="d",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="D",
            author="human:test",
            owner="human:test",
            sensitivity=Sensitivity.INTERNAL,
        )
        doc_service.create(
            session,
            project_id=proj.row_id,
            doc_key="other",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="O",
            author="human:test",
            owner="human:test",
            sensitivity=Sensitivity.INTERNAL,
        )
        sec = doc_service.add_section(
            session,
            document_id=doc.row_id,
            anchor="x",
            heading="X",
            level=2,
            position=0,
            body="See [O](other).",
            author="human:test",
        )
        section_id = sec.row_id
        LinkRepository(session).delete_for_section(section_id)
    engine.dispose()

    runner = CliRunner()
    result = runner.invoke(
        link_group,
        ["backfill", "-p", "restate", "--dry-run"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        rows = LinkRepository(session).list_for_section(section_id)
    engine.dispose()
    # Dry-run rolled back — section still has zero links.
    assert rows == []


def test_cli_import_unknown_project_errors() -> None:
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    runner = CliRunner()
    result = runner.invoke(
        import_cmd,
        ["docs", "missing"],
        obj={"config": cfg},
    )
    assert result.exit_code != 0
    assert "не найден" in result.output.lower()
