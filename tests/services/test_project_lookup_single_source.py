"""SVC-008: проект по слагу читается одной сервисной функцией.

До SVC-008 поверхности резолвили проект сами: 15 локальных копий в CLI с
четырьмя разными текстами ошибки (в трёх подсказка печатала буквальный
``{name}``), ещё пять inline-поисков в командах и четыре в web-слое. Тот же
класс дефекта, что пять резолверов ``db_url`` в hub-режиме: копии расходятся,
и чинить приходится в каждой.

Теперь чтение одно — ``services.project_lookup.find_project_id`` (и
``require_project_id`` поверх него); поверхности различаются только тем, во что
превращают отсутствие проекта. Тест ловит возврат копии: вызов ``get_by_slug``
в ``cod_doc/{cli,api,mcp}``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SURFACES = ("cli", "api", "mcp")


def test_surfaces_do_not_look_up_projects_by_slug_themselves() -> None:
    offenders: list[str] = []
    for surface in SURFACES:
        for path in (REPO_ROOT / "cod_doc" / surface).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get_by_slug"
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], (
        "поверхность ищет проект по слагу сама — зови "
        "services.project_lookup.find_project_id / require_project_id "
        "(в CLI — cod_doc.cli._common.require_project_id[_click]): "
        f"{offenders}"
    )
