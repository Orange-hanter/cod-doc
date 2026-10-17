"""Исключения синхронизации несут код и stdlib-основу из RFC 29 §10.10."""

from __future__ import annotations

import pytest

from cod_doc.infra.remote import errors as transport
from cod_doc.services.sync_service import errors

TABLE: list[tuple[type[errors.SyncError], str, type[Exception]]] = [
    (errors.RemoteUnavailableError, "SYNC-101", ConnectionError),
    (errors.ObjectMissingError, "SYNC-102", LookupError),
    (errors.PushRejectedError, "SYNC-103", RuntimeError),
    (errors.SchemaMismatchError, "SYNC-104", RuntimeError),
    (errors.EpochPassedError, "SYNC-105", RuntimeError),
    (errors.PendingOpsError, "SYNC-106", RuntimeError),
    (errors.CheckoutLeaseUnavailableError, "SYNC-109", errors.RemoteUnavailableError),
    (errors.RemoteConfigError, "SYNC-110", ValueError),
    (errors.RemoteAuthError, "SYNC-201", PermissionError),
    (errors.AuthorMismatchError, "SYNC-203", PermissionError),
    (errors.RemoteRateLimitedError, "SYNC-204", errors.RemoteUnavailableError),
    (errors.ConflictResolveError, "SYNC-301", ValueError),
    (errors.ConflictNotFoundError, "SYNC-302", LookupError),
]


@pytest.mark.parametrize(("cls", "code", "base"), TABLE, ids=[c for _, c, _ in TABLE])
def test_code_and_base(cls: type[errors.SyncError], code: str, base: type[Exception]) -> None:
    exc = cls("сообщение")
    assert exc.code == code
    assert isinstance(exc, base)
    assert str(exc) == f"{code}: сообщение"


def test_auth_error_carries_role_code() -> None:
    exc = errors.RemoteAuthError("роль viewer", code="SYNC-202")
    assert exc.code == "SYNC-202"
    assert errors.RemoteAuthError.code == "SYNC-201"


def test_transport_errors_are_the_same_objects() -> None:
    assert errors.ObjectMissingError is transport.ObjectMissingError
    assert errors.RemoteUnavailableError is transport.RemoteUnavailableError
