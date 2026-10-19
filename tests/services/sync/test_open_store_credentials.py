"""``open_store`` отдаёт S3-remote ключи из ``~/.cod-doc/credentials`` (CLO-051)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.remote.s3 import S3Remote
from cod_doc.services.sync_service import _local
from cod_doc.services.sync_service.errors import RemoteConfigError

if TYPE_CHECKING:
    from pathlib import Path

ACCESS = "AKIA-FILE-ACCESS-7f3c"
SECRET = "s3cr3t-from-file-91ab"
URL = "s3://cod-doc-remotes/team"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    # Цепочка AWS не должна подмешать ключи окружения разработчика.
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-aws-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-aws-config"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    entry = {"name": "demo", "path": str(tmp_path), "remotes": {"origin": {"url": URL}}}
    (home / "config.yaml").write_text(json.dumps({"projects": [entry]}))
    return home


def _credentials(home: Path, mode: int = 0o600) -> Path:
    path = home / "credentials"
    path.write_text(
        f"s3://cod-doc-remotes:\n  access_key: {ACCESS}\n  secret_key: {SECRET}\n",
        encoding="utf-8",
    )
    path.chmod(mode)
    return path


def test_s3_remote_takes_keys_from_credentials_file(home: Path) -> None:
    _credentials(home)
    store = _local.open_store("demo", "origin")
    assert isinstance(store, S3Remote)
    creds = store.client._request_signer._credentials  # type: ignore[attr-defined]
    assert creds.access_key == ACCESS
    assert creds.secret_key == SECRET
    assert SECRET not in (home / "config.yaml").read_text()


def test_without_entry_falls_back_to_aws_chain(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-FROM-ENV")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "env-secret")
    store = _local.open_store("demo", "origin")
    assert isinstance(store, S3Remote)
    creds = store.client._request_signer._credentials  # type: ignore[attr-defined]
    assert creds.access_key == "AKIA-FROM-ENV"


def test_wide_permissions_refuse_open_without_leaking(
    home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    path = _credentials(home, mode=0o644)
    with pytest.raises(RemoteConfigError) as err:
        _local.open_store("demo", "origin")
    assert err.value.code == "SYNC-110"
    assert str(path) in str(err.value)
    for text in (str(err.value), caplog.text):
        assert SECRET not in text
        assert ACCESS not in text


def test_file_remote_ignores_credentials_file(home: Path, tmp_path: Path) -> None:
    """``file://`` кредов не читает: битый файл кредов ему не мешает."""
    entry = {
        "name": "demo",
        "path": str(tmp_path),
        "remotes": {"origin": {"url": f"file://{tmp_path / 'remote'}"}},
    }
    (home / "config.yaml").write_text(json.dumps({"projects": [entry]}))
    _credentials(home, mode=0o644)
    assert _local.open_store("demo", "origin") is not None
