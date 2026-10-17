"""Ошибки транспорта remote (RFC 29 §10.5, коды — §10.10).

RFC селит исключения синхронизации в ``cod_doc/services/sync_service/errors.py``,
но транспортные бросает сам транспорт, а ``infra`` не смотрит в ``services``
(слои однонаправленные). Поэтому классы, которые бросает ``infra/remote``,
определены здесь, а ``sync_service.errors`` реэкспортирует те же объекты:
``except ObjectMissingError`` ловит одно и то же, откуда бы ни пришла ошибка.

Код — атрибут ``code``; он же префикс текста, чтобы сообщение в логе и в MCP
было самодостаточным.
"""

from __future__ import annotations


class SyncError(Exception):
    """Основа ошибок синхронизации: несёт код из таблицы §10.10."""

    code: str = "SYNC-000"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(f"{self.code}: {message}")


class RemoteUnavailableError(SyncError, ConnectionError):
    """SYNC-101: remote недоступен (сеть, 5xx, нечитаемый каталог)."""

    code = "SYNC-101"


class ObjectMissingError(SyncError, LookupError):
    """SYNC-102: объекта нет, или его содержимое не совпало с адресом."""

    code = "SYNC-102"


class RemoteConfigError(SyncError, ValueError):
    """SYNC-110: неизвестная схема URL или remote не настроен."""

    code = "SYNC-110"


class RemoteAuthError(SyncError, PermissionError):
    """SYNC-201 (нет или неверный токен) / SYNC-202 (роль не позволяет)."""

    code = "SYNC-201"


class RemoteRateLimitedError(RemoteUnavailableError):
    """SYNC-204: превышена частота запросов."""

    code = "SYNC-204"
