"""MCP tools: ``project_repair`` — автопочинка состояния проекта (ADO-192).

Это фаза D команды ``cod-doc update`` и единственная её фаза, которую вообще
можно выставить на MCP: фазы A–C (своп рантайма, миграции, верификация)
подменяют тот самый процесс, который исполняет тул. Разбор — в
``docs/mcp-integration.md``, §«Почему на MCP выставлена только фаза D
``cod-doc update``».

Профили: ``standard`` / ``full``. ``agent`` и ``minimal`` — явные allowlist'ы
в ``cod_doc/mcp/profiles.py``, так что имя туда не протекает.

Activity events (ADO-040): сводное ``project.repaired`` эмитит сам
``repair_service.apply`` внутри транзакции мутации — обёртка второго события
не пишет.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from cod_doc.infra.db import transactional
from cod_doc.mcp.tools._db import require_project_id, session_factory
from cod_doc.services import repair_service

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register repair tools on the given FastMCP instance."""

    @mcp.tool(name="project_repair")
    def project_repair(
        project: str,
        dry_run: bool = True,
        ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES,
    ) -> dict[str, Any]:
        """ADO-192 фаза D: починить состояние проекта по карточке куратора.

        Диагноз берётся у ``curator_next`` — второго диагноста в системе нет.
        Чинятся ровно четыре класса находок: ``edited_in_place`` →
        ``doc import``; протухшие записи реестра хэшей ``MASTER.md`` →
        пересчёт; нерезолвящиеся derived-ссылки → resync секций; замки
        ``checked_out_at`` старше ``ttl_minutes`` → снятие. Всё остальное
        (``stale_export``, ``missing``, hash ``BROKEN``, ``unplaced``,
        внешние findings) уходит счётчиками в ``reported_only`` и не
        трогается.

        Args:
            project: cod-doc project slug (обязателен — у HTTP-демона нет
                дефолтного проекта вовсе).
            dry_run: **по умолчанию ``True`` — и это осознанно.** Тул, который
                на спекулятивном вызове молча переписывает десяток документов
                и реестр ``MASTER.md`` на диске, — мина: у модели нет способа
                узнать заранее, во что обойдётся «просто посмотреть». При
                ``dry_run=True`` не выполняется ни одного действия — план
                возвращается с ``applied == 0``, транзакция откатывается, а
                ``MASTER.md`` остаётся байт-в-байт прежним. Чтобы починить
                по-настоящему, передай ``dry_run=false`` явно.
            ttl_minutes: возраст, после которого замок задачи считается
                протухшим.

        Returns:
            ``RepairResult.as_dict()``: ``{"project", "dry_run", "ok",
            "applied", "actions": [{"kind", "ref", "applied", "detail",
            "error"}], "reported_only", "errors"}``. Одна упавшая чинилка не
            обрывает остальные — её ошибка приходит в ``actions[].error`` и в
            ``errors``, а ``ok`` становится ``false``.
        """
        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        # commit=not dry_run — вторая линия к явному `dry_run` сервиса, а не
        # замена ему: `hash_calc.update_hashes` пишет MASTER.md на диск, и
        # откатить это транзакция не может.
        with transactional(sf, commit=not dry_run) as session:
            project_id = require_project_id(session, project)
            result = repair_service.apply(
                session,
                project_id=project_id,
                root_path=root,
                master_path=root / "MASTER.md",
                slug=project,
                ttl_minutes=ttl_minutes,
                dry_run=dry_run,
                author="mcp:project_repair",
            )
        return result.as_dict()
