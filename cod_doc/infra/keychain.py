"""Токены https-remote в Keychain macOS (RFC 29 §3.8, §10.12; CLO-038).

``cod-doc remote login`` на macOS кладёт токен сюда, а не в файл кредов:
Keychain шифрует его ключом входа пользователя. Запись — generic password с
сервисом :data:`SERVICE` и аккаунтом «URL remote» (без хвостового ``/``).
Работа — через системный ``security``, без новых зависимостей. Цена: на время
записи токен виден в argv процесса ``security`` (``ps`` того же пользователя);
читается он через stdout, не через argv.

Не macOS, нет ``security`` или ``COD_DOC_KEYCHAIN=0`` (так его выключают
тесты) — :func:`available` ложно, чтение отдаёт ``None``, запись — ошибка:
вызывающий выбирает файл кредов.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Final

__all__ = ["SERVICE", "available", "read_token", "store_token"]

SERVICE: Final = "cod-doc-remote"
_DISABLE_ENV: Final = "COD_DOC_KEYCHAIN"
_TIMEOUT_S: Final = 10


def available() -> bool:
    if sys.platform != "darwin" or os.environ.get(_DISABLE_ENV, "").strip() == "0":
        return False
    return shutil.which("security") is not None


def _account(url: str) -> str:
    return url.rstrip("/")


def read_token(url: str) -> str | None:
    """Токен remote ``url`` (или его префикса по сегменту — как у файла кредов); нет — ``None``."""
    if not available():
        return None
    account = _account(url)
    candidates = [account]
    while "/" in account.split("://", 1)[-1]:
        account = account.rsplit("/", 1)[0]
        candidates.append(account)
    for candidate in candidates:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", candidate, "-w"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def store_token(url: str, token: str) -> None:
    """Записать (или заменить) токен remote ``url``; не вышло — ``OSError``."""
    if not available():
        msg = "Keychain недоступен (не macOS или COD_DOC_KEYCHAIN=0)"
        raise OSError(msg)
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            SERVICE,
            "-a",
            _account(url),
            "-w",
            token,
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        check=False,
    )
    if result.returncode != 0:
        # stderr security токена не содержит; код возврата — для диагностики.
        msg = f"security add-generic-password: код {result.returncode}"
        raise OSError(msg)
