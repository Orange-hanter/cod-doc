"""MCP tools: ``sync_status``, ``sync_pull``, ``sync_push``, ``sync_conflicts``,
``sync_resolve`` (RFC 29 §10.7; CLO-016, CLO-026).

Профили ``standard`` / ``full``; в ``agent`` нет — куратор узнаёт об
отставании от remote через ``curator_next`` (§3.9). ``agent`` и ``minimal``
— явные allowlist'ы в ``cod_doc/mcp/profiles.py``, имена туда не протекают.

Ошибки — ``ValueError`` с сообщением, начинающимся с кода §10.10
(``"SYNC-110: remote 'origin' у проекта … не настроен"``). Граница
секции B — расхождение историй, которое разрешает только слияние, — тоже
``ValueError``, но без кода: у неё кода нет (``MergeRequiredError``).

``remote add``, ``clone``, ``member``, ``token`` в MCP нет намеренно (§10.7).
Активность (ADO-040) пишут сами сервисы — ``sync.pushed`` / ``sync.pulled``
/ ``sync.conflict_resolved`` (с ревизией сущности); обёртки второго события
не пишут. Резолв проекта (``session_factory``) тоже идёт под
``_as_value_error`` (находка F7 аудита секции B).
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from cod_doc.infra.db import transactional
from cod_doc.infra.sync_capture import SyncCaptureError
from cod_doc.mcp.tools._db import require_project_id, session_factory
from cod_doc.services.sync_service import pull as pull_mod
from cod_doc.services.sync_service import push as push_mod
from cod_doc.services.sync_service import status as status_mod
from cod_doc.services.sync_service.conflicts import list_conflicts, resolve_conflict
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
            restricted_local, restricted_rows, leases, drift_hint, verify,
            clone_url}``; ``restricted_local`` — restricted-документы,
            ``restricted_rows`` — их строки на реплике (документы и тело), ни
            одна не уезжает (CLO-033). ``remote`` —
            ``null``, если такого remote у проекта нет; ``verify`` —
            ``{local, remote, equal}`` или ``null``.
        """
        with _as_value_error():
            sf, _entry = session_factory(project)
            return _to_dict(status_mod.status(sf, project, remote=remote, verify=verify))

    @mcp.tool(name="sync_pull")
    def sync_pull(
        project: str, remote: str = "origin", author: str = "mcp", dry_run: bool = False
    ) -> dict[str, Any]:
        """RFC 29 §3.6: fetch + fast-forward — применить чужие changeset'ы.

        Эпоха remote новее эпохи реплики — ``SYNC-105`` до скачивания:
        сначала ``cod-doc update`` (CLO-032). Схема уже обновлена, а голова
        в прошлой эпохе — реплика встаёт на снапшот эпохи, ``rebased`` — его
        голова (CLO-050); changeset прошлой эпохи — ``SYNC-105``.

        Args:
            project: cod-doc project slug.
            remote: имя remote.
            author: автор события ``sync.pulled``.
            dry_run: показать, что применилось бы, ничего не записывая.

        Returns:
            ``PullResult``: ``{applied, ops, fast_forward, conflicts, aliases,
            head, rebased}``.
        """
        with _as_value_error():
            sf, _entry = session_factory(project)
            result = pull_mod.pull(sf, project, remote=remote, author=author, dry_run=dry_run)
        return _to_dict(result)

    @mcp.tool(name="sync_push")
    def sync_push(project: str, remote: str = "origin", author: str = "mcp") -> dict[str, Any]:
        """RFC 29 §3.6: упаковать неотправленные операции и сдвинуть refs/main.

        Remote ушёл вперёд — pull и повтор (до трёх раз), дальше ``SYNC-103``.
        Эпоха remote новее эпохи реплики — ``SYNC-105`` (выгрузка —
        ``cod-doc sync export-pending``, затем ``cod-doc update``); эпоха
        реплики новее — push открывает её на remote (CLO-032).

        Args:
            project: cod-doc project slug.
            remote: имя remote.
            author: ``actor`` changeset'а и автор события ``sync.pushed``.

        Returns:
            ``PushResult``: ``{changesets, ops, head, retries, pinned, snapshot,
            epoch_opened}``.
        """
        with _as_value_error():
            sf, _entry = session_factory(project)
            result = push_mod.push(sf, project, remote=remote, author=author)
        return _to_dict(result)

    @mcp.tool(name="sync_conflicts")
    def sync_conflicts(project: str, status: str = "open") -> list[dict[str, Any]]:
        """RFC 29 §3.7: конфликты слияния проекта — в БД «наше», «их» лежит здесь.

        Args:
            project: cod-doc project slug.
            status: ``open`` (по умолчанию), ``resolved`` или ``all``.

        Returns:
            ``[{uid, created, entity_kind, entity_uid, kind, field, ours,
            theirs, base, theirs_op, status, resolved_by, resolved_at}]`` —
            старые раньше. Значения — в форме changeset'а (FK — uid конца).
        """
        with _as_value_error():
            sf, _entry = session_factory(project)
            with transactional(sf, commit=False) as session:
                return list_conflicts(session, require_project_id(session, project), status=status)

    @mcp.tool(name="sync_resolve")
    def sync_resolve(
        project: str,
        conflict_uid: str,
        take: str,
        author: str = "mcp",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """RFC 29 §3.7: разрешить конфликт — значение, операция, ревизия, событие.

        Запись уезжает следующим push'ем. ``SYNC-302`` — конфликта нет или он
        уже разрешён; ``SYNC-301`` — свой текст у конфликта не ``text``.

        Args:
            project: cod-doc project slug.
            conflict_uid: uid из ``sync_conflicts``.
            take: ``ours``, ``theirs`` или свой текст (только конфликт ``text``).
            author: автор ревизии и события ``sync.conflict_resolved``.
            reason: причина в ревизии; по умолчанию ``sync resolve --take …``.

        Returns:
            Конфликт после разрешения (поля ``sync_conflicts``) плюс
            ``revision_id`` ревизии сущности (``null`` у таблиц без ревизий) и
            ``marker`` — uid маркера разрешения: уедет следующим push и закроет
            конфликт той же подписи на других репликах (``null`` — подпись local).
        """
        with _as_value_error():
            sf, _entry = session_factory(project)
            with transactional(sf) as session:
                return resolve_conflict(
                    session,
                    require_project_id(session, project),
                    conflict_uid,
                    take=take,
                    author=author,
                    reason=reason,
                )
