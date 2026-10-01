"""ACU-002: `cod-doc hash update` оставляет след, когда файл принадлежит проекту.

Проект находится по пути файла, без `--project`; файл вне зарегистрированных
проектов пересчитывается, как раньше, но команда говорит, что события нет.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.services import hash_service

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "hashcli"
_WRONG_HASH = "0123456789ab"


def _master(root: Path) -> Path:
    (root / "beta.md").write_text("# Beta\n", encoding="utf-8")
    master = root / "MASTER.md"
    master.write_text(
        f"# MASTER\n\n- **Ссылка:** 📁 /beta.md | 🗃️ doc:beta_md | 🔑 sha:{_WRONG_HASH}\n",
        encoding="utf-8",
    )
    return master


def _event_authors(slug: str) -> list[str]:
    entry = Config.load().get_project(slug)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory, commit=False) as session:
            return list(
                session.execute(
                    select(ActivityEventModel.actor_id).where(
                        ActivityEventModel.kind == hash_service.EVENT_KIND
                    )
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()


def test_hash_update_finds_the_project_by_path_and_records_the_event(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    root = tmp_path / _PROJECT
    root.mkdir()
    added = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert added.exit_code == 0, added.output
    master = _master(root)

    result = CliRunner().invoke(main, ["hash", "update", str(master), "--author", "human:test"])

    assert result.exit_code == 0, result.output
    assert _WRONG_HASH not in master.read_text(encoding="utf-8")
    assert _event_authors(_PROJECT) == ["human:test"]


def test_hash_update_outside_projects_still_rewrites_and_says_there_is_no_trace(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    master = _master(tmp_path)

    result = CliRunner().invoke(main, ["hash", "update", str(master)])

    assert result.exit_code == 0, result.output
    assert _WRONG_HASH not in master.read_text(encoding="utf-8")
    assert "событие в журнал не записано" in result.output


def test_hash_update_with_an_unknown_project_fails(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    master = _master(tmp_path)

    result = CliRunner().invoke(main, ["hash", "update", str(master), "-p", "nope"])

    assert result.exit_code == 1
    assert _WRONG_HASH in master.read_text(encoding="utf-8")
