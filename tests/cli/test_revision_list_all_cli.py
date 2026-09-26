"""AFT-008 (RFC 27 F9): CLI `revision list --all` — проектная лента ревизий.

`--kind`/`--ref` больше не обязательны: с `--all` команда отдаёт ленту проекта
newest-first с фильтрами `--since`/`--author`/`--entity-kind`; без `--all` —
прежний режим сущности, но хвост режется в SQL (`list_for_entity(limit=...)`).
Сид и литеральные эталоны совпадают с MCP-сюитой
tests/test_revision_list_feed_mcp.py: ожидания здесь — литералы, а не пересчёт
через revision_service или сравнение с выводом MCP revision_list.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result

from cod_doc.cli import main
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DocumentModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    SectionModel,
    TaskModel,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "rvla"

_AT = {
    "rev-a1": datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
    "rev-a2": datetime(2026, 9, 26, 9, 0, tzinfo=UTC),
    "rev-a3": datetime(2026, 9, 26, 11, 0, tzinfo=UTC),
    "rev-b1": datetime(2026, 9, 26, 8, 0, tzinfo=UTC),
    "rev-b2": datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
    "rev-c1": datetime(2026, 9, 26, 7, 30, tzinfo=UTC),
}


def _seed(tmp_path: Path) -> None:
    """Зарегистрированный проект с ревизиями task/section/document.

    Литеральные at/author/reason: две сутки, сегодня — 2026-09-26.
    Проект сидится напрямую (реестр в изолированном COD_DOC_HOME + alembic
    upgrade head), а не через `project add`: бутстрап пишет свои ревизии и
    засорял бы ленту.
    """
    root = tmp_path / _PROJECT
    (root / ".cod-doc").mkdir(parents=True)
    db_url = f"sqlite:///{root / '.cod-doc' / 'state.db'}"
    run_alembic("upgrade", "head", db_url=db_url)

    cfg = Config.load()
    cfg.projects = [ProjectEntry(name=_PROJECT, path=str(root)).model_dump()]
    cfg.save()

    factory = make_session_factory(make_engine(db_url))
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(
            slug=_PROJECT, title=_PROJECT.upper(), root_path=str(root), config_json={}
        )
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()
        proj = project.row_id
        plan = PlanModel(project_id=proj, scope="plan-x", created=now, last_updated=now)
        session.add(plan)
        session.flush()
        plan_sec = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
        )
        session.add(plan_sec)
        session.flush()
        task = TaskModel(
            project_id=proj,
            task_id="RVLA-001",
            plan_id=plan.row_id,
            section_id=plan_sec.row_id,
            title="Seed task",
            status="todo",
            type="feature",
            priority="medium",
            created=now,
            last_updated=now,
        )
        doc = DocumentModel(
            project_id=proj,
            doc_key="guide",
            path="docs/guide.md",
            type="guide",
            status="active",
            title="Guide",
            created=now,
            last_updated=now,
        )
        session.add_all([task, doc])
        session.flush()
        section = SectionModel(
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="intro body",
            content_hash="abc",
        )
        session.add(section)
        session.flush()

        def _rev(
            revision_id: str,
            kind: str,
            entity_id: int,
            author: str,
            reason: str,
            parent: str | None = None,
        ) -> RevisionModel:
            return RevisionModel(
                revision_id=revision_id,
                project_id=proj,
                entity_kind=kind,
                entity_id=entity_id,
                parent_revision_id=parent,
                author=author,
                at=_AT[revision_id],
                diff=f"diff-of-{revision_id}",
                reason=reason,
            )

        session.add_all(
            [
                _rev("rev-a1", "task", task.row_id, "alice", "create task"),
                _rev("rev-a2", "task", task.row_id, "bob", "status change", "rev-a1"),
                _rev("rev-a3", "task", task.row_id, "alice", "title edit", "rev-a2"),
                _rev("rev-b1", "section", section.row_id, "alice", "patch section"),
                _rev("rev-b2", "section", section.row_id, "carol", "add section"),
                _rev("rev-c1", "document", doc.row_id, "bob", "rename doc"),
            ]
        )
        session.flush()


def _run(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(
        main,
        ["revision", "list", "-p", _PROJECT, *args],
        env={"FORCE_COLOR": ""},
    )


def test_all_since_today_json(tmp_path: Path) -> None:
    """«Ревизии за сегодня по видам сущностей» — одна команда (критерий 1)."""
    _seed(tmp_path)
    result = _run(["--all", "--since", "2026-09-26", "--json"])
    assert result.exit_code == 0, result.output

    rows = json.loads(result.output)
    assert [r["revision_id"] for r in rows] == ["rev-a3", "rev-a2", "rev-b1", "rev-c1"]
    for row in rows:
        assert "entity_kind" in row
        assert "diff" not in row


def test_all_author_and_entity_kind(tmp_path: Path) -> None:
    """--author и --entity-kind дают литеральные списки newest-first."""
    _seed(tmp_path)

    result = _run(["--all", "--author", "alice", "--json"])
    assert result.exit_code == 0, result.output
    assert [r["revision_id"] for r in json.loads(result.output)] == [
        "rev-a3",
        "rev-b1",
        "rev-a1",
    ]

    result = _run(["--all", "--entity-kind", "section", "--json"])
    assert result.exit_code == 0, result.output
    assert [r["revision_id"] for r in json.loads(result.output)] == ["rev-b1", "rev-b2"]


def test_entity_mode_unchanged(tmp_path: Path) -> None:
    """--kind/--ref: прежний вывод, 2 последние ревизии oldest→newest (критерий 2)."""
    _seed(tmp_path)
    result = _run(["--kind", "task", "--ref", "RVLA-001", "--limit", "2", "--json"])
    assert result.exit_code == 0, result.output

    rows = json.loads(result.output)
    assert [r["revision_id"] for r in rows] == ["rev-a2", "rev-a3"]
    for row in rows:
        assert set(row) == {
            "revision_id",
            "author",
            "at",
            "reason",
            "parent_revision_id",
            "diff",
        }


def test_usage_errors(tmp_path: Path) -> None:
    """Невалидные сочетания опций — ненулевой код без трейсбека."""
    _seed(tmp_path)

    for args in (
        ["--all", "--kind", "task"],
        ["--kind", "task"],
        ["--since", "2026-09-26"],
    ):
        result = _run(list(args))
        assert result.exit_code != 0, args
        assert "Traceback" not in result.output, args
