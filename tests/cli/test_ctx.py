"""CLI tests for ``cod-doc ctx docs|drift|search`` (SYM-008 / ADO-057)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _import_corpus(root: Path, name: str = "p") -> None:
    runner = CliRunner()
    (root / "alpha.md").write_text(
        "---\ntype: standard\nstatus: active\n---\n# Alpha\n\n## Details\n\nSee [[doc:missing]].\n"
    )
    (root / "beta.md").write_text(
        "---\ntype: standard\nstatus: active\n---\n# Beta\n\nBeta body content.\n"
    )
    result = runner.invoke(main, ["import", "docs", name])
    assert result.exit_code == 0, result.output


def test_ctx_docs_json_valid(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert "docs" in data
    assert "links_at_risk" in data
    assert "token_estimate" in data
    assert isinstance(data["token_estimate"], int)
    assert data["token_estimate"] > 0
    assert len(data["docs"]) >= 2
    doc_keys = {d["doc_key"] for d in data["docs"]}
    assert "alpha" in doc_keys
    assert "beta" in doc_keys
    # Сломанная ссылка [[doc:missing]] должна быть найдена.
    broken = [link for link in data["links_at_risk"] if link["source_doc_key"] == "alpha"]
    assert any("missing" in (link["broken_reason"] or "") for link in broken)


def test_ctx_docs_paths_filter(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--paths", "*alpha*", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert len(data["docs"]) == 1
    assert data["docs"][0]["doc_key"] == "alpha"


def test_ctx_docs_budget_tokens(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--budget-tokens", "0", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["docs"] == []
    assert data["token_estimate"] == 0


def test_ctx_drift_json_valid(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "drift", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["project"] == "p"
    assert "total_docs" in data
    assert "problem_count" in data
    assert "counts" in data
    assert "issues" in data
    assert data["total_docs"] >= 2


def test_ctx_search_json_valid(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "search", "-p", "p", "alpha", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["query"] == "alpha"
    assert "total" in data
    assert "by_kind" in data
    assert data["total"] > 0
    assert any(data["by_kind"][kind] for kind in data["by_kind"])


def test_ctx_search_scope_and_limit(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """CUR-011: ``ctx search --scope --limit`` reach ``search_service.search``.

    Four tasks match the term, one doc does not; ``--scope task`` excludes
    the doc kind entirely and ``--limit 2`` caps the task hits at 2.
    """
    from datetime import UTC, datetime

    from cod_doc.config import Config
    from cod_doc.domain.entities import Priority, TaskType
    from cod_doc.infra.db import db_for_entry, transactional
    from cod_doc.infra.models import PlanModel, PlanSectionModel
    from cod_doc.infra.repositories import ProjectRepository
    from cod_doc.services import search_service, task_service

    _init_project(tmp_path, "sc")

    entry = Config.load().get_project("sc")
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            project = ProjectRepository(session).get_by_slug("sc")
            assert project is not None and project.row_id is not None
            pid = project.row_id
            now = datetime.now(UTC)
            plan = PlanModel(project_id=pid, scope="sc-plan", created=now, last_updated=now)
            session.add(plan)
            session.flush()
            section = PlanSectionModel(
                plan_id=plan.row_id, letter="A", title="A", slug="A", position=0
            )
            session.add(section)
            session.flush()
            for i in range(4):
                task_service.create(
                    session,
                    project_id=pid,
                    plan_id=plan.row_id,
                    section_id=section.row_id,
                    title=f"widget task {i}",
                    type=TaskType.FEATURE,
                    priority=Priority.LOW,
                    id_prefix="SCP",
                    author="test",
                )
        with transactional(factory) as session:
            search_service.reindex_all(session, project_id=pid)
    finally:
        engine.dispose()

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ctx", "search", "-p", "sc", "widget", "--scope", "task", "--limit", "2", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data["by_kind"]["task"]) == 2
    assert data["total"] == 2
    assert data["by_kind"]["doc"] == []


def test_ctx_search_missing_index_table_exits_clean(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    """CUR-010: a DB that predates migration 0023 has no ``db_search_idx``.

    ``ctx search`` must fail with a clean ``click.ClickException`` (exit 1,
    no traceback) instead of a raw ``sqlalchemy.exc.OperationalError``.
    """
    from sqlalchemy import text

    from cod_doc.config import Config
    from cod_doc.infra.db import db_for_entry, transactional

    root = _init_project(tmp_path)
    _import_corpus(root)

    entry = Config.load().get_project("p")
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            session.execute(text("DROP TABLE db_search_idx"))
    finally:
        engine.dispose()

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "search", "-p", "p", "alpha"])
    assert result.exit_code == 1
    assert "db_search_idx" in result.output
    assert "Traceback" not in result.output


def test_ctx_dry_read_writes_nothing(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    for args in [
        ["ctx", "docs", "-p", "p", "--json"],
        ["ctx", "drift", "-p", "p", "--json"],
        ["ctx", "search", "-p", "p", "beta", "--json"],
        ["ctx", "next", "-p", "p", "--json"],
    ]:
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output

    # Повторный импорт должен пропустить все документы: ctx ничего не изменил.
    result = runner.invoke(main, ["import", "docs", "p", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Skipped (already in DB):" in result.output


def test_ctx_help_in_russian(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "--help"])
    assert result.exit_code == 0, result.output
    assert "Контекст" in result.output
    assert "docs" in result.output
    assert "drift" in result.output
    assert "search" in result.output
    assert "next" in result.output


def test_ctx_next_json_valid(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """CUR-016: `ctx next --json` — зеркало MCP `curator_next`."""
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "next", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["project"] == "p"
    # ADO-116 добавил пятый источник: неразложенные документы. Набор ключей
    # здесь — контракт карточки, поэтому проверяется точным равенством.
    assert set(data["card"]) == {"drift", "links", "master", "findings", "unplaced"}
    assert data["card"]["drift"]["project"] == "p"
    assert set(data["meta"]) == {"generated_at", "truncated", "counts"}
    assert data["navigation"]["applicable_skills"][0]["name"] == "orchestrator"

    # alpha.md содержит [[doc:missing]] — битая ссылка обязана попасть в очередь.
    links = [item for item in data["priority"] if item["kind"] == "link"]
    assert links, "нерезолвящаяся ссылка обязана быть в очереди"
    assert "link_verify(" in links[0]["suggested_action"]


def test_ctx_next_limit_truncates(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)
    # Правка мимо БД: даёт edited_in_place вдобавок к битой ссылке, чтобы в
    # очереди гарантированно было больше одного пункта.
    with (root / "alpha.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка на диске.\n")

    runner = CliRunner()
    full = json.loads(runner.invoke(main, ["ctx", "next", "-p", "p", "--json"]).output)
    assert full["meta"]["counts"]["priority_total"] >= 2
    assert full["meta"]["truncated"] is False

    result = runner.invoke(main, ["ctx", "next", "-p", "p", "--limit", "1", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data["priority"]) == 1
    assert data["meta"]["truncated"] is True


def test_ctx_next_human_output_lists_the_queue(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    result = CliRunner().invoke(main, ["ctx", "next", "-p", "p"])
    assert result.exit_code == 0, result.output
    assert "очередь куратора" in result.output


def test_ctx_docs_include_body(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--include-body", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    bodies = {d["doc_key"]: d.get("body") for d in data["docs"]}
    assert bodies.get("alpha"), "include-body обязан вернуть отрендеренное тело"
    assert "See [[doc:missing]]" in bodies["alpha"]


def test_ctx_docs_without_include_body_has_no_body(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert all("body" not in d for d in data["docs"])
