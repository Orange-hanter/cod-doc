"""MCP tools: ``sync_status``, ``sync_pull``, ``sync_push`` (RFC 29 §10.7; CLO-016).

Профили ``standard`` / ``full``; в ``agent`` нет — куратор узнаёт об
отставании от remote через ``curator_next`` (§3.9). ``agent`` и ``minimal``
— явные allowlist'ы в ``cod_doc/mcp/profiles.py``, имена туда не протекают.

Ошибки — ``ValueError`` с сообщением, начинающимся с кода §10.10
(``"SYNC-110: remote 'origin' у проекта … не настроен"``). Граница
секции B — расхождение историй, которое разрешает только слияние, — тоже
``ValueError``, но без кода: у неё кода нет (``MergeRequiredError``).

``remote add``, ``clone``, ``member``, ``token`` в MCP нет намеренно (§10.7);
``sync_conflicts`` / ``sync_resolve`` появятся с секцией C (CLO-026).
Активность (ADO-040) пишут сами ``push``/``pull`` — ``sync.pushed`` /
``sync.pulled``; обёртки второго события не пишут.
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from cod_doc.infra.sync_capture import SyncCaptureError
from cod_doc.mcp.tools._db import session_factory
from cod_doc.services.sync_service import pull as pull_mod
from cod_doc.services.sync_service import push as push_mod
from cod_doc.services.sync_service import status as status_mod
from cod_doc.services.sync_service.errors import MergeRequiredError, SyncError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mcp.server.fastmcp import FastMCP


@contextmanager
def _as_value_error() -> Iterator[None]:
    """Ошибки синхронизации → ``ValueError`` с тем же текстом (код — его префикс)."""
    try:
        yield
    except (SyncError, SyncCaptureError, MergeRequiredError) as exc:
        raise ValueError(str(exc)) from exc


def _to_dict(obj: object) -> dict[str, Any]:
    assert dataclasses.is_dataclass(obj) and not isinstance(obj, type)
    out = dataclasses.asdict(obj)
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in out.items()}


def register(mcp: FastMCP) -> None:
    """Register sync tools on the given FastMCP instance."""

    @mcp.tool(name="sync_status")
    def sync_status(project: str, remote: str = "origin", verify: bool = False) -> dict[str, Any]:
        """RFC 29 §3.9: где реплика проекта относительно remote.

        С настроенным remote делает fetch (скачивает и проверяет новые
        changeset'ы, ничего не применяя) и запоминает увиденную голову remote.

        Args:
            project: cod-doc project slug.
            remote: имя remote из ``cod-doc remote add`` (по умолчанию origin).
            verify: сверить дайджест реплики с дайджестом changeset'а головы
                remote (``cod-doc sync verify``).

        Returns:
            ``SyncStatus``: ``{remote, head_local, head_remote, ahead, behind,
            pending_ops, open_conflicts, epoch_local, epoch_remote,
            restricted_local, leases, drift_hint, verify}``. ``remote`` —
            ``null``, если такого remote у проекта нет; ``verify`` —
            ``{local, remote, equal}`` или ``null``.
        """
        sf, _entry = session_factory(project)
        with _as_value_error():
            return _to_dict(status_mod.status(sf, project, remote=remote, verify=verify))

    @mcp.tool(name="sync_pull")
    def sync_pull(
        project: str, remote: str = "origin", author: str = "mcp", dry_run: bool = False
    ) -> dict[str, Any]:
        """RFC 29 §3.6: fetch + fast-forward — применить чужие changeset'ы.

        Секция B умеет только fast-forward: свой неотправленный хвост на тех
        же сущностях — ошибка «слияние: секция C», ничего не применяется.

        Args:
            project: cod-doc project slug.
            remote: имя remote.
            author: автор события ``sync.pulled``.
            dry_run: показать, что применилось бы, ничего не записывая.

        Returns:
            ``PullResult``: ``{applied, ops, fast_forward, conflicts, aliases,
            head}``.
        """
        sf, _entry = session_factory(project)
        with _as_value_error():
            result = pull_mod.pull(sf, project, remote=remote, author=author, dry_run=dry_run)
        return _to_dict(result)

    @mcp.tool(name="sync_push")
    def sync_push(project: str, remote: str = "origin", author: str = "mcp") -> dict[str, Any]:
        """RFC 29 §3.6: упаковать неотправленные операции и сдвинуть refs/main.

        Remote ушёл вперёд — pull и повтор (до трёх раз), дальше ``SYNC-103``.

        Args:
            project: cod-doc project slug.
            remote: имя remote.
            author: ``actor`` changeset'а и автор события ``sync.pushed``.

        Returns:
            ``PushResult``: ``{changesets, ops, head, retries, pinned}``.
        """
        sf, _entry = session_factory(project)
        with _as_value_error():
            result = push_mod.push(sf, project, remote=remote, author=author)
        return _to_dict(result)
