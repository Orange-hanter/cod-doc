"""``~/.cod-doc/credentials`` (RFC 29 §10.12, CLO-051): чтение, права, утечки."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.credentials import (
    MAX_CREDENTIALS_BYTES,
    CredentialsFileError,
    credentials_file,
    credentials_for,
    load_credentials,
)
from cod_doc.infra.remote.errors import RemoteConfigError

if TYPE_CHECKING:
    from pathlib import Path

ACCESS = "AKIA-FILE-ACCESS-7f3c"
SECRET = "s3cr3t-from-file-91ab"
TOKEN = "cdt_never_print_me_42"

BODY = f"""\
# ~/.cod-doc/credentials (chmod 600)
https://sync.example.dev:
  token: {TOKEN}
s3://cod-doc-remotes:
  access_key: {ACCESS}
  secret_key: {SECRET}
s3://cod-doc-remotes/team-b:
  access_key: AKIA-TEAM-B
  secret_key: team-b-secret
"""


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    return home


def _write(home: Path, body: str = BODY, mode: int = 0o600) -> Path:
    path = home / "credentials"
    path.write_text(body, encoding="utf-8")
    path.chmod(mode)
    return path


def _no_secret(text: str) -> None:
    for secret in (ACCESS, SECRET, TOKEN, "team-b-secret"):
        assert secret not in text


def test_file_lives_in_cod_doc_home(home: Path) -> None:
    assert credentials_file() == home / "credentials"


def test_reads_rfc_format_keyed_by_remote_url(home: Path) -> None:
    _write(home)
    entries = load_credentials()
    assert entries["https://sync.example.dev"] == {"token": TOKEN}
    assert entries["s3://cod-doc-remotes"] == {"access_key": ACCESS, "secret_key": SECRET}


def test_remote_url_matches_longest_key_on_segment_boundary(home: Path) -> None:
    _write(home)
    assert credentials_for("s3://cod-doc-remotes/team") == {
        "access_key": ACCESS,
        "secret_key": SECRET,
    }
    assert credentials_for("s3://cod-doc-remotes") == {"access_key": ACCESS, "secret_key": SECRET}
    assert credentials_for("s3://cod-doc-remotes/team-b/x")["access_key"] == "AKIA-TEAM-B"  # type: ignore[index]
    # Не граница сегмента: «cod-doc-remotes-2» — другой бакет.
    assert credentials_for("s3://cod-doc-remotes-2/team") is None
    assert credentials_for("s3://other-bucket") is None


def test_missing_file_means_no_credentials(home: Path) -> None:
    assert load_credentials() == {}
    assert credentials_for("s3://cod-doc-remotes/team") is None


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o606])
def test_permissions_wider_than_0600_are_refused(home: Path, mode: int) -> None:
    path = _write(home, mode=mode)
    with pytest.raises(CredentialsFileError) as err:
        load_credentials()
    assert isinstance(err.value, RemoteConfigError)
    assert err.value.code == "SYNC-110"
    assert err.value.path == path
    text = str(err.value)
    assert str(path) in text
    assert oct(mode)[2:] in text
    assert "chmod 600" in text
    _no_secret(text)


def test_owner_only_narrower_modes_are_accepted(home: Path) -> None:
    _write(home, mode=0o400)
    assert credentials_for("https://sync.example.dev") == {"token": TOKEN}


def test_symlink_is_refused(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_text(BODY, encoding="utf-8")
    target.chmod(0o600)
    (home / "credentials").symlink_to(target)
    with pytest.raises(CredentialsFileError, match="символическ") as err:
        load_credentials()
    _no_secret(str(err.value))


def test_non_regular_file_is_refused(home: Path) -> None:
    (home / "credentials").mkdir(mode=0o700)
    with pytest.raises(CredentialsFileError, match="не обычный файл"):
        load_credentials()


def test_foreign_owner_is_refused(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(home)
    monkeypatch.setattr(os, "getuid", lambda: home.stat().st_uid + 1)
    with pytest.raises(CredentialsFileError, match="владелец"):
        load_credentials()


def test_oversized_file_is_refused(home: Path) -> None:
    _write(home, body="#" * (MAX_CREDENTIALS_BYTES + 1))
    with pytest.raises(CredentialsFileError, match="больше"):
        load_credentials()


@pytest.mark.parametrize(
    "body",
    [
        f"s3://cod-doc-remotes:\n  access_key: {ACCESS}\n  secret_key: [{SECRET}\n",  # синтаксис
        f"s3://cod-doc-remotes:\n  access_key: {ACCESS}\n  secret_key: {SECRET}\n  : x\n",
        f"- {SECRET}\n",  # не словарь
        f"s3://cod-doc-remotes: {SECRET}\n",  # запись не словарь
        "s3://cod-doc-remotes:\n  access_key: 12345\n  secret_key: 0x1f\n",  # не строка
        f"{SECRET}:\n  token: x\n",  # ключ не URL remote
        f"https://user:{SECRET}@sync.example.dev:\n  token: x\n",  # креды в ключе
        f"s3://cod-doc-remotes:\n  {SECRET}: x\n",  # имя поля — мусор
    ],
)
def test_malformed_file_errors_never_echo_contents(home: Path, body: str) -> None:
    path = _write(home, body=body)
    with pytest.raises(CredentialsFileError) as err:
        load_credentials()
    text = str(err.value)
    assert str(path) in text
    _no_secret(text)
    _no_secret(repr(err.value))
    # Исключение разбора (с цитатой строки файла) не цепляется к трейсбеку.
    assert err.value.__cause__ is None
    assert err.value.__context__ is None or err.value.__suppress_context__


def test_secrets_absent_from_logs(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _write(home)
    credentials_for("s3://cod-doc-remotes/team")
    _write(home, mode=0o644)
    with pytest.raises(CredentialsFileError):
        credentials_for("s3://cod-doc-remotes/team")
    _no_secret(caplog.text)


def test_json_is_valid_yaml_too(home: Path) -> None:
    """``remote login`` (CLO-038) может писать тот же файл любым YAML-дампером."""
    _write(home, body=json.dumps({"https://sync.example.dev/": {"token": TOKEN}}))
    assert credentials_for("https://sync.example.dev") == {"token": TOKEN}
