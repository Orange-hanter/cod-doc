"""ADO-192: MCP-обёртка фазы D `cod-doc update` — ``project_repair``.

Тесты MCP-семейств в этом репозитории лежат плоско в ``tests/``
(``test_scenario_tools.py``, ``test_tool_search.py``, …), каталога
``tests/mcp/`` нет — файл положен рядом с ними.

Проверяется не «функция вызвалась»: дефолт ``dry_run=True`` доказывается
диском и БД (``MASTER.md`` байт-в-байт, дрейф остался), а форма ответа —
сравнением с ``RepairResult.as_dict()`` того же прогона плюс ``json.dumps``:
тул ходит по транспорту, и любой ``Path``/``datetime`` в полезной нагрузке
убил бы вызов у клиента, а не в тесте.

Фикстура строится через CLI (``project add`` + ``import docs``), как и тесты
``repair_service``: только так на диске оказываются настоящие файлы, по
которым считается дрейф.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.mcp.profiles import keep_tool
from cod_doc.mcp.server import mcp
from cod_doc.services import doc_service, projection_service, repair_service
from cod_doc.services.projection_service import DriftStatus

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_TOOL = "project_repair"
_PROJECT = "reptool"

#: Заведомо неверный хэш в реестре MASTER.md: 12 hex-символов, которых не
#: даст ни один реальный файл.
_WRONG_HASH = "0123456789ab"

_ALPHA = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n# Alpha\n\n## Details\n\nBody.\n"

#: PCA-939: имя тула — snake_case, без точек и заглавных.
_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _tool() -> Any:
    return mcp._tool_manager._tools[_TOOL].fn


def _registered_names() -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[sessionmaker, Path]]:
    """(фабрика сессий, корень проекта) для пустого зарегистрированного проекта."""
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output

    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        yield factory, root
    finally:
        engine.dispose()


def _import_docs() -> None:
    result = CliRunner().invoke(main, ["import", "docs", _PROJECT])
    assert result.exit_code == 0, result.output


def _seed_edited_in_place(root: Path) -> None:
    """Документ в БД, потом правка файла мимо неё — классический дрейф."""
    (root / "alpha.md").write_text(_ALPHA, encoding="utf-8")
    (root / "MASTER.md").write_text(
        f"# MASTER\n\n- **Ссылка:** 📁 /alpha.md | 🗃️ doc:alpha_md | 🔑 sha:{_WRONG_HASH}\n",
        encoding="utf-8",
    )
    _import_docs()
    with (root / "alpha.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка мимо БД — ровно то, что ловит edited_in_place.\n")


def _alpha_drift(session: Session, root: Path) -> DriftStatus:
    doc = next(
        d
        for d in doc_service.list_for_project(session, _project_id(session))
        if d.doc_key == "alpha"
    )
    assert doc.row_id is not None
    return projection_service.detect_drift(session, doc.row_id, root_path=root).status


def _project_id(session: Session) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


# --------------------------------------------------------------------------- #
# Регистрация и профили                                                        #
# --------------------------------------------------------------------------- #


def test_project_repair_is_registered() -> None:
    assert _TOOL in _registered_names()


def test_project_repair_is_standard_and_full_only() -> None:
    """`agent` и `minimal` — явные allowlist'ы: ковровая починка туда не едет.

    Ценность куратора в том, что он судит по каждому пункту
    `curator_next.priority` отдельно, и готовая команда на каждый у него уже
    есть в `suggested_action`.
    """
    assert keep_tool(_TOOL, "full") is True
    assert keep_tool(_TOOL, "standard") is True
    assert keep_tool(_TOOL, "minimal") is False
    assert keep_tool(_TOOL, "agent") is False


def test_tool_name_is_snake_case() -> None:
    assert _SNAKE_CASE_RE.match(_TOOL), _TOOL


def test_signature_defaults() -> None:
    """`dry_run=True` по умолчанию, TTL не разъезжается с сервисом."""
    params = inspect.signature(_tool()).parameters
    assert params["dry_run"].default is True
    assert params["ttl_minutes"].default == repair_service.DEFAULT_TTL_MINUTES
    assert params["project"].default is inspect.Parameter.empty


# --------------------------------------------------------------------------- #
# dry_run                                                                      #
# --------------------------------------------------------------------------- #


def test_dry_run_default_writes_nothing(project) -> None:  # type: ignore[no-untyped-def]
    """Вызов без аргументов, кроме `project`, не трогает ни диск, ни БД."""
    factory, root = project
    _seed_edited_in_place(root)
    master_before = (root / "MASTER.md").read_bytes()
    alpha_before = (root / "alpha.md").read_bytes()

    payload = _tool()(project=_PROJECT)

    assert payload["dry_run"] is True
    assert payload["applied"] == 0
    assert payload["actions"], "нечего чинить — фикстура не создала дрейф"
    assert all(action["applied"] is False for action in payload["actions"])

    assert (root / "MASTER.md").read_bytes() == master_before
    assert (root / "alpha.md").read_bytes() == alpha_before
    with transactional(factory, commit=False) as session:
        assert _alpha_drift(session, root) is DriftStatus.EDITED_IN_PLACE


def test_explicit_dry_run_false_repairs(project) -> None:  # type: ignore[no-untyped-def]
    """Починка происходит только по явному `dry_run=false`."""
    factory, root = project
    _seed_edited_in_place(root)

    payload = _tool()(project=_PROJECT, dry_run=False)

    assert payload["ok"] is True, payload["errors"]
    assert payload["dry_run"] is False
    assert payload["applied"] >= 1
    assert _WRONG_HASH not in (root / "MASTER.md").read_text(encoding="utf-8")
    with transactional(factory, commit=False) as session:
        assert _alpha_drift(session, root) is DriftStatus.IN_SYNC


# --------------------------------------------------------------------------- #
# Форма ответа                                                                 #
# --------------------------------------------------------------------------- #


def test_payload_matches_repair_result_as_dict(project) -> None:  # type: ignore[no-untyped-def]
    """Тул не изобретает свою форму — отдаёт `RepairResult.as_dict()`."""
    factory, root = project
    _seed_edited_in_place(root)

    payload = _tool()(project=_PROJECT)

    with transactional(factory, commit=False) as session:
        expected = repair_service.apply(
            session,
            project_id=_project_id(session),
            root_path=root,
            master_path=root / "MASTER.md",
            slug=_PROJECT,
            dry_run=True,
        ).as_dict()

    assert payload == expected


def test_payload_survives_json_dumps(project) -> None:  # type: ignore[no-untyped-def]
    """Ни Path, ни datetime наружу: иначе вызов падал бы на транспорте."""
    _seed_edited_in_place(root=project[1])

    payload = _tool()(project=_PROJECT)

    assert json.loads(json.dumps(payload)) == payload
