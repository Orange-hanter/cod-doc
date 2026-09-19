"""MCP tools: ``curator_next`` — doc card куратора (RFC 25 §3.5, CUR-016)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

#: Сколько пунктов очереди отдавать по умолчанию.
DEFAULT_LIMIT = 10


def register(mcp: FastMCP) -> None:
    """Register curator tools."""

    @mcp.tool(name="curator_next")
    def curator_next(project: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """RFC 25 §3.5 (CUR-016): «doc card» куратора — за что браться первым.

        Один вызов вместо четырёх: собирает дрейф проекции (``ctx_drift``),
        нерезолвящиеся ссылки, протухшие записи реестра хэшей ``MASTER.md`` и
        открытые findings — и отдаёт их одной очередью с уже проставленным
        приоритетом и готовой командой на каждый пункт.

        Порядок очереди фиксирован: ``missing`` → ``edited_in_place`` →
        ``LINK-BROKEN`` → hash ``BROKEN`` → hash ``STALE`` → ``stale_export``
        → finding. Сначала то, что делает документ недоступным, потом то, что
        делает его неточным.

        Args:
            project: cod-doc project slug (обязателен — у HTTP-демона нет
                дефолтного проекта).
            limit: сколько пунктов очереди вернуть. Полный срез всё равно
                лежит в ``card``, а ``meta.truncated`` скажет, что хвост
                обрезан.

        Returns:
            ``{"card": {"drift", "links", "master", "findings"},
            "priority": [{"kind", "ref", "reason", "suggested_action"}],
            "navigation": {"applicable_skills" (с ТЕЛАМИ), "next_actions",
            "success_criteria"}, "meta": {"generated_at", "truncated",
            "counts"}}``.

        Read-only: ни одной записи в БД — повторный вызов безопасен.
        """
        from pathlib import Path

        from cod_doc.infra.db import transactional
        from cod_doc.services import curator_service

        sf, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        with transactional(sf, commit=False) as session:
            project_id = require_project_id(session, project)
            payload = curator_service.next(
                session,
                project_id=project_id,
                root_path=root,
                master_path=root / "MASTER.md",
                limit=limit,
                project_slug=project,
            )
        payload["card"]["drift"]["project"] = project
        return {"project": project, **payload}
