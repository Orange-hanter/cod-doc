"""CLO-038: ``remote login`` и ``member``/``token`` через https-remote (RFC 29 §10.8).

Сервер — настоящий ``/api/sync/v1`` за Bearer-гейтом (``tests/infra/remote/_https_server``);
клиент и сервер делят ``COD_DOC_HOME`` теста, но клиент ходит только по HTTP.
"""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config, config_dir
from cod_doc.services import access_service
from tests.infra.remote._https_server import base_url, grant

if TYPE_CHECKING:
    from pathlib import Path

SLUG = "demo"


def _run(*args: str, stdin: str | None = None) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args), input=stdin, catch_exceptions=False)
    return result.exit_code, result.stdout + result.stderr


def _ok(*args: str, stdin: str | None = None) -> str:
    code, out = _run(*args, stdin=stdin)
    assert code == 0, out
    return out


def _setup(tmp_path: Path) -> str:
    """Проект demo с remote origin на сервере; токен root (admin) через remote login."""
    root = tmp_path / "repo"
    root.mkdir()
    _ok("project", "add", str(root), "--name", SLUG)
    Config.clear_cache()
    entry = Config.load().get_project(SLUG)
    assert entry is not None
    uid = access_service.project_uid_for(entry)
    url = base_url()
    access_service.init_store()
    grant(uid)  # dakh — admin проекта
    with access_service.store_session() as s:
        token = access_service.create_token(s, handle="dakh", label="cli", author="human:root")
    _ok("remote", "add", "origin", url, "-p", SLUG)
    out = _ok(
        "remote",
        "login",
        "origin",
        "-p",
        SLUG,
        "--token-stdin",
        "--store",
        "file",
        stdin=token.token + "\n",
    )
    assert token.token not in out
    return uid


def test_remote_login_writes_private_credentials(tmp_path: Path) -> None:
    _setup(tmp_path)
    creds = config_dir() / "credentials"
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    assert base_url() in creds.read_text()


def test_login_refuses_non_https_remote(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _ok("project", "add", str(root), "--name", SLUG)
    _ok("remote", "add", "files", (tmp_path / "r").as_uri(), "-p", SLUG)
    code, out = _run("remote", "login", "files", "-p", SLUG, "--token-stdin", stdin="cdt_x\n")
    assert code == 1
    assert "только https" in out


def test_member_and_token_over_remote(tmp_path: Path) -> None:
    _setup(tmp_path)
    remote = ("-p", SLUG, "--remote", "origin")
    _ok("member", "add", "bob", "--role", "writer", *remote)
    members = json.loads(_ok("member", "list", *remote, "--json"))
    assert {(m["handle"], m["role"]) for m in members} >= {("bob", "writer"), ("dakh", "admin")}

    issued = json.loads(_ok("token", "create", "dakh", "--label", "laptop", *remote, "--json"))
    assert issued["token"].startswith(access_service.TOKEN_PREFIX)
    code, out = _run("token", "create", "bob", "--label", "x", *remote)
    assert code == 1
    assert "только себе" in out

    tokens = json.loads(_ok("token", "list", *remote, "--json"))
    assert issued["uid"] in {t["uid"] for t in tokens}
    assert issued["token"] not in json.dumps(tokens)
    _ok("token", "revoke", issued["uid"], *remote)
    tokens = json.loads(_ok("token", "list", *remote, "--json"))
    assert next(t for t in tokens if t["uid"] == issued["uid"])["revoked_at"] is not None

    _ok("member", "remove", "bob", *remote)
    members = json.loads(_ok("member", "list", *remote, "--json"))
    assert "bob" not in {m["handle"] for m in members}


def test_remote_mode_needs_project(tmp_path: Path) -> None:
    _setup(tmp_path)
    code, out = _run("token", "list", "--remote", "origin")
    assert code == 1
    assert "--project" in out
