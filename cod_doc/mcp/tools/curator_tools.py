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
    def curator_next(
        project: str,
        limit: int = DEFAULT_LIMIT,
        include_skill_bodies: bool = False,
        skip_links: bool = False,
    ) -> dict[str, Any]:
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
            include_skill_bodies: добавить ``body`` к каждому скиллу в
                ``navigation.applicable_skills``. Тела — основная масса
                ответа, а нужны они один раз за сессию; на профиле ``agent``
                нет ``skill_get``, так что это единственный путь к ним.
            skip_links: не собирать раздел ``links`` (обход каждой секции
                корпуса). В ``meta.not_collected`` тогда появится
                ``"links"``, и пустой ``card.links`` значит «не смотрели»,
                а не «ссылки в порядке».

        Returns:
            ``{"card": {"drift", "links", "master", "findings", "unplaced"},
            "priority": [{"kind", "ref", "reason", "suggested_action"}],
            "navigation": {"applicable_skills", "next_actions",
            "success_criteria"}, "meta": {"generated_at", "truncated",
            "counts"}}``.

            ``card.drift.issues`` — только не-``in_sync`` документы;
            расхождения frontmatter и осиротевшие секции у ``in_sync`` лежат
            в ``card.drift.advisory`` (``{count, doc_keys}``).
            ``card.unplaced`` — документы в Инбоксе дерева.
            ``applicable_skills`` — ``[{name, description}]``, тела (``body``)
            только при ``include_skill_bodies=true``.

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
                skip_links=skip_links,
                include_skill_bodies=include_skill_bodies,
            )
        payload["card"]["drift"]["project"] = project
        return {"project": project, **payload}
