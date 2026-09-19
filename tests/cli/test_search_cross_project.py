"""CUR-013 / RFC 22 §3.6: ``--projects`` у ``cod-doc search`` и ``ctx search``.

Обе поверхности резолвят слаги одним и тем же сервисом, поэтому и happy path,
и отказ «разные db_url» проверяются на обеих.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ADRModel, ProjectModel
from cod_doc.services import search_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

#: Слаг → adr_id: по одному ADR на проект, оба про «widget».
_HUB_PROJECTS = {"alpha": "ADR-101", "beta": "ADR-202"}


def _hub_registry(tmp_path: Path) -> None:
    """alpha и beta — в одной hub-БД (с ADR и заполненным индексом), solo — сам по себе."""
    hub_url = f"sqlite:///{tmp_path / 'hub.db'}"
    run_alembic("upgrade", "head", db_url=hub_url)

    cfg = Config()
    for name in _HUB_PROJECTS:
        (tmp_path / name).mkdir()
        cfg.add_project(ProjectEntry(name=name, path=str(tmp_path / name), db_url=hub_url))
    (tmp_path / "solo").mkdir()
    cfg.add_project(ProjectEntry(name="solo", path=str(tmp_path / "solo")))
    cfg.save()

    engine = make_engine(hub_url)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)
    project_ids: list[int] = []
    with transactional(factory) as session:
        for slug, adr_id in _HUB_PROJECTS.items():
            proj = ProjectModel(
                slug=slug, title=slug, root_path=str(tmp_path / slug), config_json={}
            )
            proj.created = now
            proj.updated = now
            session.add(proj)
            session.flush()
            project_ids.append(proj.row_id)
            adr = ADRModel(
                project_id=proj.row_id,
                adr_id=adr_id,
                title=f"{slug} widget decision",
                status="accepted",
                context="ctx",
                decision="widget stays",
            )
            adr.created = now
            adr.last_updated = now
            session.add(adr)
        session.flush()
    with transactional(factory) as session:
        for pid in project_ids:
            search_service.reindex_all(session, pid)
    engine.dispose()


def test_ctx_search_projects_returns_both(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _hub_registry(tmp_path)

    result = CliRunner().invoke(
        main, ["ctx", "search", "-p", "alpha", "widget", "--projects", "beta", "--json"]
    )
    assert result.exit_code == 0, result.output

    # Именно stdout: проверка схемы hub-БД логирует alembic-плагины в stderr,
    # и `result.output` (склейка потоков) перестаёт быть валидным JSON.
    data = json.loads(result.stdout)
    assert {h["ref"]: h["project"] for h in data["by_kind"]["adr"]} == {
        "ADR-101": "alpha",
        "ADR-202": "beta",
    }


def test_search_projects_returns_both(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """``--reindex`` при этом перестраивает индекс каждого проекта набора."""
    _hub_registry(tmp_path)

    result = CliRunner().invoke(
        main, ["search", "widget", "-p", "alpha", "--projects", "beta", "--reindex"]
    )
    assert result.exit_code == 0, result.output
    assert "ADR-101" in result.output
    assert "ADR-202" in result.output
    assert "beta" in result.output


def test_ctx_search_rejects_project_from_another_db(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _hub_registry(tmp_path)

    result = CliRunner().invoke(
        main, ["ctx", "search", "-p", "alpha", "widget", "--projects", "solo", "--json"]
    )
    assert result.exit_code != 0
    assert "shared db_url (hub mode): solo resolves to" in result.output


def test_search_rejects_project_from_another_db(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _hub_registry(tmp_path)

    result = CliRunner().invoke(main, ["search", "widget", "-p", "alpha", "--projects", "solo"])
    assert result.exit_code != 0
    assert "shared db_url (hub mode): solo resolves to" in result.output
