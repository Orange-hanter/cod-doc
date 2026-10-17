"""Исключения синхронизации с кодами RFC 29 §10.10.

Транспортные (``SYNC-101``, ``102``, ``110``, ``201``/``202``, ``204``)
определены в ``cod_doc.infra.remote.errors`` — их бросает транспорт, а ``infra``
не импортирует ``services``; здесь они реэкспортируются теми же объектами.

``SYNC-108`` (``CheckoutLeaseHeldError(CheckoutConflictError)``) появится с
арендой checkout: его основа живёт в ``checkout_service``, и тянуть её сюда
раньше — лишний импорт сервисного слоя в пакет, у которого пока нет
потребителя.
"""

from __future__ import annotations

from cod_doc.infra.remote.errors import (
    ObjectMissingError,
    RemoteAuthError,
    RemoteConfigError,
    RemoteRateLimitedError,
    RemoteUnavailableError,
    SyncError,
)

__all__ = [
    "AuthorMismatchError",
    "CheckoutLeaseUnavailableError",
    "ConflictNotFoundError",
    "ConflictResolveError",
    "EpochPassedError",
    "MergeRequiredError",
    "ObjectMissingError",
    "PendingOpsError",
    "PushRejectedError",
    "RemoteAuthError",
    "RemoteConfigError",
    "RemoteRateLimitedError",
    "RemoteUnavailableError",
    "SchemaMismatchError",
    "SyncError",
]


class PushRejectedError(SyncError, RuntimeError):
    """SYNC-103: push не прошёл после ``max_retries``."""

    code = "SYNC-103"


class SchemaMismatchError(SyncError, RuntimeError):
    """SYNC-104: схема changeset новее своей / push старше ``schema_min``."""

    code = "SYNC-104"


class EpochPassedError(SyncError, RuntimeError):
    """SYNC-105: эпоха сменилась при незапушенных операциях."""

    code = "SYNC-105"


class PendingOpsError(SyncError, RuntimeError):
    """SYNC-106: ``cod-doc update`` при незапушенных операциях."""

    code = "SYNC-106"


class CheckoutLeaseUnavailableError(RemoteUnavailableError):
    """SYNC-109: remote недоступен для checkout без ``--offline``."""

    code = "SYNC-109"


class AuthorMismatchError(SyncError, PermissionError):
    """SYNC-203: ``actor``/``author`` changeset'а не совпал с токеном."""

    code = "SYNC-203"


class ConflictResolveError(SyncError, ValueError):
    """SYNC-301: ``take`` не подходит к виду конфликта."""

    code = "SYNC-301"


class ConflictNotFoundError(SyncError, LookupError):
    """SYNC-302: конфликт не найден или уже разрешён."""

    code = "SYNC-302"


class MergeRequiredError(NotImplementedError):
    """Расхождение историй, которое разрешает только слияние — секция C RFC 29.

    Не код §10.10, а граница реализации: pull секции B умеет fast-forward
    (чужие changeset'ы поверх своей головы; свой неупакованный хвост не трогает
    тех же сущностей), а LWW по полю, 3-way merge текстов, tombstone против
    правки и склейка натуральных ключей — CLO-018…CLO-024. Исключение уйдёт
    вместе с этой границей, поэтому кода у него нет (DoD §11.1 п. 6: новый код
    сначала вписывается в RFC). ``NotImplementedError``, а не ``SyncError``:
    ловить его как штатную ошибку синхронизации незачем.
    """

    def __init__(self, message: str) -> None:
        super().__init__(f"{message} — слияние расходящейся истории: секция C (CLO-018…CLO-024)")
