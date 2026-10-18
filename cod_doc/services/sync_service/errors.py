"""Исключения синхронизации с кодами RFC 29 §10.10.

Транспортные (``SYNC-101``, ``102``, ``110``, ``201``/``202``, ``204``)
определены в ``cod_doc.infra.remote.errors`` — их бросает транспорт, а ``infra``
не импортирует ``services``; здесь они реэкспортируются теми же объектами.

``SYNC-108`` (``CheckoutLeaseHeldError``) — подкласс и ``SyncError``, и
``CheckoutConflictError`` из листового ``task_errors`` (CLO-031): поверхности,
ловящие занятый замок, ловят и занятую аренду, не зная о remote.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.infra.remote.errors import (
    ObjectMissingError,
    RemoteAuthError,
    RemoteConfigError,
    RemoteRateLimitedError,
    RemoteUnavailableError,
    SyncError,
)
from cod_doc.services.task_errors import CheckoutConflictError

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "AuthorMismatchError",
    "CheckoutLeaseHeldError",
    "CheckoutLeaseUnavailableError",
    "ConflictNotFoundError",
    "ConflictResolveError",
    "EpochPassedError",
    "MergeRequiredError",
    "ObjectMissingError",
    "PendingOpsError",
    "ProvisionalIdWarning",
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


class ProvisionalIdWarning(UserWarning):
    """SYNC-107: аренда номеров пуста — выдан провизорный ID (не ошибка, §10.10).

    Предупреждение, а не исключение: провизорный ``PREFIX-~xxxx`` рабочий, на
    него можно ссылаться, а ближайший push закрепит за ним номер (CLO-030).
    Текст начинается с кода, как у исключений ``SyncError``.
    """

    code = "SYNC-107"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class CheckoutLeaseHeldError(SyncError, CheckoutConflictError):
    """SYNC-108: аренда ``leases/checkout/<task_uid>`` на remote занята (CLO-031).

    Отказ сразу и без повтора, как у ``CheckoutConflictError``: «взята
    dakh@macbook в 14:02» — сообщение называет держателя, его реплику и время.
    """

    code = "SYNC-108"

    def __init__(
        self, *, task_id: str, locked_by: str, replica: str, acquired_at: datetime
    ) -> None:
        when = acquired_at.strftime("%Y-%m-%d %H:%M UTC")
        Exception.__init__(
            self,
            f"{self.code}: задача {task_id} взята {locked_by} (реплика {replica}) "
            f"в {when}; never retry on conflict",
        )
        self.task_id = task_id
        self.locked_by = locked_by
        self.replica = replica
        self.acquired_at = acquired_at


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
    """Расхождение, которое разрешает механика ещё не построенной задачи секции C.

    Не код §10.10, а граница реализации. Pull сливает расхождение по
    стратегиям ``merge`` (LWW по полю — CLO-018); случай, стратегии которого
    ещё нет, называет свою задачу (``task``): 3-way merge текстов — CLO-019,
    ``task.status`` — CLO-020, рёбра add-wins — CLO-021, удаление против
    правки — CLO-022, склейка натуральных ключей — CLO-023. Исключение уйдёт
    вместе с последней из них, поэтому кода у него нет (DoD §11.1 п. 6: новый
    код сначала вписывается в RFC). ``NotImplementedError``, а не
    ``SyncError``: ловить его как штатную ошибку синхронизации незачем.
    """

    def __init__(self, message: str, *, task: str | None = None) -> None:
        self.task = task
        where = task if task is not None else "CLO-018…CLO-024"
        super().__init__(f"{message} — слияние этого случая: секция C ({where})")
