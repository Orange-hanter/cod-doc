"""CLO-038: CLI ``access``, ``member``, ``token``, ``actor`` (RFC 29 §10.8)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import transactional
from cod_doc.services import access_service

if TYPE_CHECKING:
    from pathlib import Path

SLUG = "demo"


def _run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    return result.exit_code, result.stdout, result.stderr


def _ok(*args: str) -> str:
    code, out, err = _run(*args)
    assert code == 0, (out, err)
    return out


def _project(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _ok("project", "add", str(root), "--name", SLUG)
    Config.clear_cache()


def test_commands_refuse_without_store(tmp_path: Path) -> None:
    _project(tmp_path)
    code, out, err = _run("member", "add", "alice", "-p", SLUG, "--role", "writer")
    assert code == 1
    assert "cod-doc access init" in out + err
    assert json.loads(_ok("access", "status", "--json"))["gate"] is False


def test_full_cycle(tmp_path: Path) -> None:
    _project(tmp_path)
    _ok("access", "init")
    _ok("member", "add", "alice", "-p", SLUG, "--role", "writer")
    _ok("actor", "agent", "alice", "claude-code")

    out = _ok("token", "create", "alice", "--label", "macbook")
    token = out.strip().splitlines()[0]
    assert token.startswith(access_service.TOKEN_PREFIX)

    factory = access_service.open_store()
    assert factory is not None
    with transactional(factory) as s:
        ident = access_service.authenticate(s, token)
    assert ident is not None
    assert set(ident.roles.values()) == {"writer"}

    members = json.loads(_ok("member", "list", "-p", SLUG, "--json"))
    assert [(m["handle"], m["project"], m["role"]) for m in members] == [("alice", SLUG, "writer")]

    tokens = json.loads(_ok("token", "list", "--json"))
    assert [t["label"] for t in tokens] == ["macbook"]
    assert token not in json.dumps(tokens)

    _ok("token", "revoke", tokens[0]["uid"][:16])
    with transactional(factory) as s:
        assert access_service.authenticate(s, token) is None

    status = json.loads(_ok("access", "status", "--json"))
    assert status == {**status, "gate": True, "members": 1, "active_tokens": 0}


def test_token_create_json_shows_token_once(tmp_path: Path) -> None:
    _project(tmp_path)
    _ok("access", "init")
    _ok("member", "add", "bob", "-p", SLUG, "--role", "reader")
    issued = json.loads(_ok("token", "create", "bob", "--label", "ci", "--json"))
    assert issued["token"].startswith(access_service.TOKEN_PREFIX)
    assert issued["handle"] == "bob"


def test_bad_input_is_click_error(tmp_path: Path) -> None:
    _project(tmp_path)
    _ok("access", "init")
    code, out, err = _run("token", "create", "ghost", "--label", "x")
    assert code == 1
    assert "не найден" in out + err
