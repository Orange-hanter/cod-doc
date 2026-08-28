"""SYM-003: loopback-bind по умолчанию + loopback-гейт POST /settings.

До фикса `api_host` дефолтил в `0.0.0.0` при полном отсутствии auth, а
`POST /settings` писал LLM-ключ в config.yaml от любого клиента. Теперь:
дефолт — 127.0.0.1, opt-out через `COD_DOC_BIND` / `--host`, а settings-save
принимает запросы только с loopback-пира.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from fastapi import HTTPException, Request

from cod_doc.api.web.pages._helpers import ensure_loopback_client
from cod_doc.cli.cmd_serve import serve
from cod_doc.config import Config


def _request_with_client(host: str | None) -> Request:
    scope: dict[str, object] = {"type": "http", "headers": []}
    if host is not None:
        scope["client"] = (host, 50000)
    return Request(scope)


# ── bind по умолчанию и opt-out ──────────────────────────────────────────


def test_config_api_host_defaults_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COD_DOC_API_HOST", raising=False)
    assert Config().api_host == "127.0.0.1"


def _serve_host(
    monkeypatch: pytest.MonkeyPatch,
    cli_args: list[str],
    bind_env: str | None,
) -> str:
    """Прогон `cod-doc serve` с подменённым uvicorn.run → фактический host."""
    captured: dict[str, str] = {}

    def fake_run(app: str, *, host: str, **kwargs: object) -> None:
        captured["host"] = host

    monkeypatch.setattr("uvicorn.run", fake_run)
    if bind_env is None:
        monkeypatch.delenv("COD_DOC_BIND", raising=False)
        monkeypatch.delenv("COD_DOC_API_HOST", raising=False)
    else:
        monkeypatch.setenv("COD_DOC_BIND", bind_env)

    result = CliRunner().invoke(serve, cli_args, obj={"config": Config()})
    assert result.exit_code == 0, result.output
    return captured["host"]


def test_serve_binds_loopback_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _serve_host(monkeypatch, [], None) == "127.0.0.1"


def test_serve_cod_doc_bind_restores_old_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _serve_host(monkeypatch, [], "0.0.0.0") == "0.0.0.0"


def test_serve_cli_host_flag_beats_bind_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _serve_host(monkeypatch, ["--host", "192.0.2.1"], "0.0.0.0") == "192.0.2.1"


# ── loopback-гейт ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "testclient"])
def test_gate_allows_loopback_and_testclient(host: str) -> None:
    ensure_loopback_client(_request_with_client(host))  # не бросает


def test_gate_forbids_remote_client() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ensure_loopback_client(_request_with_client("203.0.113.10"))
    assert exc_info.value.status_code == 403


def test_gate_forbids_unknown_client() -> None:
    """Нет информации о пире — считаем удалённым (fail-closed)."""
    with pytest.raises(HTTPException) as exc_info:
        ensure_loopback_client(_request_with_client(None))
    assert exc_info.value.status_code == 403
