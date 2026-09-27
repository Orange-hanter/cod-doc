"""ADO-228 (RFC 27 F9, N7): CLI `revision summary` — агрегат ревизий GROUP BY.

Один вызов вместо выгрузки ленты и группировки на клиенте: `--group-by
day/entity_kind/author`, сумма n равна длине `revision list --all` за тот же
период. Эталоны — литералы из сида, а не пересчёт через revision_service или
сравнение с выводом MCP revision_summary. Исключение —
test_summary_sum_matches_list_all: сравнение двух команд CLI и есть предмет
критерия 2.
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

_PROJECT = "rvsum"

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

    Литеральные at/author/entity_kind: две сутки, сегодня — 2026-09-26.
    Проект сидится напрямую (реестр в изолированном COD_DOC_HOME + alembic
    upgrade head), а не через `project add`: бутстрап пишет свои ревизии и
    засорял бы агрегат.
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
            task_id="RVSUM-001",
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
        ["revision", "summary", "-p", _PROJECT, *args],
        env={"FORCE_COLOR": ""},
    )


def test_summary_today_by_entity_kind_json(tmp_path: Path) -> None:
    """«Ревизии за сегодня по видам сущностей» — одна команда (критерий 1)."""
    _seed(tmp_path)
    result = _run(["--since", "2026-09-26", "--json"])
    assert result.exit_code == 0, result.output

    assert json.loads(result.output) == [
        {"entity_kind": "document", "n": 1},
        {"entity_kind": "section", "n": 1},
        {"entity_kind": "task", "n": 2},
    ]


def test_summary_sum_matches_list_all(tmp_path: Path) -> None:
    """Сумма n равна длине `revision list --all` за тот же период (критерий 2)."""
    _seed(tmp_path)
    result = _run(["--since", "2026-09-26", "--json"])
    assert result.exit_code == 0, result.output
    total = sum(row["n"] for row in json.loads(result.output))

    feed = CliRunner().invoke(
        main,
        [
            "revision",
            "list",
            "-p",
            _PROJECT,
            "--all",
            "--since",
            "2026-09-26",
            "--limit",
            "1000",
            "--json",
        ],
        env={"FORCE_COLOR": ""},
    )
    assert feed.exit_code == 0, feed.output

    assert total == len(json.loads(feed.output)) == 4


def test_summary_group_by_day_author(tmp_path: Path) -> None:
    """--group-by day --group-by author: литеральный список по обоим ключам."""
    _seed(tmp_path)
    result = _run(["--since", "2026-09-25", "--group-by", "day", "--group-by", "author", "--json"])
    assert result.exit_code == 0, result.output

    rows = json.loads(result.output)
    for row in rows:
        assert set(row) == {"day", "author", "n"}
    assert rows == [
        {"day": "2026-09-25", "author": "alice", "n": 1},
        {"day": "2026-09-25", "author": "carol", "n": 1},
        {"day": "2026-09-26", "author": "alice", "n": 2},
        {"day": "2026-09-26", "author": "bob", "n": 2},
    ]


def test_summary_bad_since_is_usage_error(tmp_path: Path) -> None:
    """Невалидный --since — ненулевой код без трейсбека."""
    _seed(tmp_path)
    result = _run(["--since", "yesterday"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_summary_invalid_group_by_rejected(tmp_path: Path) -> None:
    """--group-by вне Choice отклоняется click с перечнем допустимых значений."""
    _seed(tmp_path)
    result = _run(["--since", "2026-09-26", "--group-by", "week"])
    assert result.exit_code != 0
    for allowed in ("day", "entity_kind", "author"):
        assert allowed in result.output


def test_summary_table_mode(tmp_path: Path) -> None:
    """Без --json — rich-таблица с литеральными видами сущностей из сида."""
    _seed(tmp_path)
    result = _run(["--since", "2026-09-26"])
    assert result.exit_code == 0, result.output
    for kind in ("document", "section", "task"):
        assert kind in result.output
