"""WEB-042 (STB-004): `cod-doc audit --web-routes` route-drift audit."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli.cmd_audit import (
    _audit_web_routes,
    _documented_web_routes,
    _normalize_route,
    _real_web_routes,
    audit,
)
from cod_doc.config import Config

if TYPE_CHECKING:
    from pathlib import Path


def test_normalize_route_strips_path_converter() -> None:
    assert _normalize_route("/p/{slug}/docs/{doc_key:path}") == "/p/{slug}/docs/{doc_key}"
    assert _normalize_route("/p/{slug}") == "/p/{slug}"


def test_real_web_routes_includes_known_pages() -> None:
    real = _real_web_routes()
    assert ("GET", "/p/{slug}/tasks") in real
    assert ("GET", "/") in real
    # /api and /ws routes are excluded (only pages + fragments routers).
    assert not any(path.startswith("/api") for _, path in real)
    assert not any(path.startswith("/ws") for _, path in real)


def test_documented_web_routes_parses_table(tmp_path: Path) -> None:
    cap = tmp_path / "web-frontend.md"
    cap.write_text(
        "## Routes\n\n"
        "| Route | Desc | Service | St | ID |\n"
        "|---|---|---|---|---|\n"
        "| `GET /` | list | x | ✅ | WEB-001 |\n"
        "| `POST /p/{slug}/docs/import` | upload | y | ✅ | WEB-081 |\n"
        "| `GET /p/{slug}/docs/{doc_key:path}` | view | z | ✅ | WEB-003 |\n",
        encoding="utf-8",
    )
    documented = _documented_web_routes(cap)
    assert ("GET", "/") in documented
    assert ("POST", "/p/{slug}/docs/import") in documented
    # converter normalized
    assert ("GET", "/p/{slug}/docs/{doc_key}") in documented


def test_audit_web_routes_flags_both_directions(tmp_path: Path) -> None:
    cap = tmp_path / "web-frontend.md"
    # Document one real route + one fake route that does not exist in the app.
    cap.write_text(
        "| `GET /` | real | x | ✅ | WEB-001 |\n"
        "| `GET /totally/made/up` | fake | y | ✅ | WEB-999 |\n",
        encoding="utf-8",
    )
    findings = _audit_web_routes(cap)
    codes = {f.code for f in findings}
    # WR-1: documented but not real (the made-up route).
    wr1 = [f for f in findings if f.code == "WR-1"]
    assert any("/totally/made/up" in f.subject for f in wr1)
    # WR-2: real routes missing from this tiny doc (e.g. tasks page).
    assert "WR-2" in codes
    assert any("/p/{slug}/tasks" in f.subject for f in findings if f.code == "WR-2")
    # All advisory.
    assert all(f.severity == "warning" for f in findings)


def test_audit_web_routes_missing_capability_doc(tmp_path: Path) -> None:
    findings = _audit_web_routes(tmp_path / "nope.md")
    assert len(findings) == 1
    assert findings[0].code == "WR-0"
    assert findings[0].severity == "warning"


def test_cli_web_routes_json_is_advisory_and_parseable() -> None:
    """`audit --web-routes --json` from the repo root: exit 0, clean JSON."""
    cfg = Config(api_key="sk-test", model="test/m", base_url="https://x")
    runner = CliRunner()
    result = runner.invoke(
        audit,
        ["--web-routes", "--json"],
        obj={"config": cfg},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "findings" in data
    assert data["total"] == data["missing_in_code"] + data["missing_in_docs"]


def test_cli_audit_requires_project_without_web_routes() -> None:
    cfg = Config(api_key="sk-test", model="test/m", base_url="https://x")
    runner = CliRunner()
    result = runner.invoke(audit, [], obj={"config": cfg})
    assert result.exit_code != 0
    assert "--project is required" in result.output
