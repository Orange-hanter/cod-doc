"""MCP tools куратора: ``curator_next`` (RFC 25 §3.5), ``curator_sweep`` и ``curator_sync`` (RFC 28)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

#: Сколько пунктов очереди отдавать по умолчанию.
DEFAULT_LIMIT = 10

#: Потолок записей прогона — то же значение, что у сервиса (ACU-004).
DEFAULT_MAX_AUTO = 50


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

        Порядок очереди фиксирован: ``missing`` → ``conflict`` → ``edited_in_place`` →
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
            ``{"card": {"drift", "links", "master", "findings", "unplaced", "questions"},
            "priority": [{"kind", "ref", "reason", "suggested_action"}],
            "navigation": {"applicable_skills", "next_actions",
            "success_criteria"}, "meta": {"generated_at", "truncated",
            "counts"}}``.

            ``card.drift.issues`` — только не-``in_sync`` документы;
            расхождения frontmatter и осиротевшие секции у ``in_sync`` лежат
            в ``card.drift.advisory`` (``{count, doc_keys}``).
            ``card.unplaced`` — документы в Инбоксе дерева.
            ``card.questions`` — открытые вопросы с битыми ссылками и
            застоявшиеся (``broken_links`` / ``stale``), а также
            открытые, чьи ``addressed_by``-задачи все сделаны (``answered``).
            ``applicable_skills`` — ``[{name, description}]``, тела (``body``)
            только при ``include_skill_bodies=true``.
            ``meta.counts.pending_proposals`` — сколько решений ждёт человек
            от фонового куратора (approval и открытые вопросы автора
            ``agent:curator``); при ненулевом в очереди пункт ``proposals``.

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

    @mcp.tool(name="curator_sweep")
    def curator_sweep(
        project: str,
        apply: bool = False,
        sync: bool = False,
        max_auto: int = DEFAULT_MAX_AUTO,
        author: str = "agent:curator",
    ) -> dict[str, Any]:
        """RFC 28 (ACU-004): прогон куратора по очереди ``curator_next``.

        Три уровня, заданные видом пункта, а не решением модели:

        - в БД сам — импорт ``edited_in_place``, resync ссылок (общий
          исполнитель ``project_repair``), раскладка Инбокса по правилам;
        - файлы — только в клон куратора и PR ``curator/sync`` (``sync=true``),
          никогда в чекаут владельца;
        - всё прочее (``conflict``, битые ссылки, находки, вопросы) — в
          ``reported`` человеку.

        ``apply=false`` (по умолчанию) — сухой прогон: тот же отчёт, ни одной
        записи. ``sync`` пишет наружу (ветка и PR) и действует только при
        ``apply=true``. ``max_auto`` — потолок записей в БД за прогон:
        не влезшая группа целиком уходит в ``capped``.

        Returns:
            ``{"project", "applied", "applied_count", "repair", "placed",
            "sync", "reported": [{"kind", "ref", "reason"}], "capped"}``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import curator_sweep_service

        sf, entry = session_factory(project)
        with transactional(sf, commit=apply) as session:
            report = curator_sweep_service.sweep_project(
                session,
                require_project_id(session, project),
                entry=entry,
                apply=apply,
                sync=sync and apply,
                max_auto=max_auto,
                author=author,
            )
        return {"project": project, **report.to_dict()}

    @mcp.tool(name="curator_sync")
    def curator_sync(project: str, author: str = "agent:curator") -> dict[str, Any]:
        """RFC 28 §3.8 (ACU-003): выгрузка проекций в клон куратора и PR.

        Пишет только в клон ``<COD_DOC_HOME>/curator/<slug>`` и в ветку
        ``curator/sync`` на remote: дописывает коммит, пока ``main`` её предок,
        иначе пересобирает от свежей базы. Держит один draft PR. Чекаут
        владельца и ``main`` не трогаются; ``projection_hash`` не двигается.

        Returns:
            ``{"project", "branch", "base", "rebuilt", "exported", "deleted",
            "skipped": [{"doc_key", "path", "reason"}], "hashes_updated",
            "commit_sha", "pr_url"}``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import curator_sync_service

        sf, entry = session_factory(project)
        with transactional(sf) as session:
            report = curator_sync_service.sync_project(
                session, require_project_id(session, project), entry=entry, author=author
            )
        return {"project": project, **report.to_dict()}
