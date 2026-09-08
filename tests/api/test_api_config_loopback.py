"""ADO-035: PATCH /api/config — loopback-гварда (симметрия с POST /settings).

Finding C1 контракт-аудита ADO-034: update_config писал LLM-ключ в
config.yaml без ensure_loopback_client — при COD_DOC_BIND=0.0.0.0 ключ
перезаписывался удалённо. Гварда живёт в cod_doc.api.deps (общий слой),
web-форма POST /settings использует её же.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from cod_doc.api import routes
from cod_doc.api.schemas import ConfigUpdate


def _request_from(client_host: str) -> Request:
    scope = {
        "type": "http",
        "method": "PATCH",
        "path": "/api/config",
        "headers": [],
        "query_string": b"",
        "client": (client_host, 54321),
    }
    return Request(scope)


class _StubConfig:
    def __init__(self) -> None:
        self.saved = False
        self.model = None

    def save(self) -> None:
        self.saved = True


def test_patch_config_rejects_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubConfig()
    monkeypatch.setattr(routes, "get_config", lambda: stub)

    with pytest.raises(HTTPException) as exc_info:
        routes.update_config(ConfigUpdate(model="gpt-test"), _request_from("203.0.113.10"))

    assert exc_info.value.status_code == 403
    assert stub.saved is False


def test_patch_config_allows_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubConfig()
    monkeypatch.setattr(routes, "get_config", lambda: stub)

    result = routes.update_config(ConfigUpdate(model="gpt-test"), _request_from("127.0.0.1"))

    assert result == {"updated": True}
    assert stub.saved is True
    assert stub.model == "gpt-test"


def test_read_config_never_leaks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADO-096: GET /api/config отдаёт конфиг целиком — секреты вырезаются все.

    Раньше вырезался только api_key, поэтому anthropic_api_key утекал; с
    появлением второго ключа (эмбеддер) список стал единым (SECRET_FIELDS).
    """
    from cod_doc.config import SECRET_FIELDS, Config

    cfg = Config(
        api_key="sk-llm-secret",
        anthropic_api_key="sk-ant-secret",
        embedding_api_key="sk-or-secret",
    )
    monkeypatch.setattr(routes, "get_config", lambda: cfg)

    data = routes.read_config()

    for field in SECRET_FIELDS:
        assert field not in data
    assert "secret" not in str(data)
