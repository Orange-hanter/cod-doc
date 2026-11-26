"""MCP tools: граф связей — relate / unrelate / relations / relation_* (RFC 39 §3.7).

Единый вход для связей между сущностями: ссылки пишутся как ``kind:key``
(``story:US-007``, ``section:docs/a.md#intro``, ``code:path.py::func``).
Подсказку по допустимым типам пары даёт ``relation_types``; ошибка ``relate``
сама перечисляет допустимое и приводит готовый пример вызова.

Видимость: ``relations`` и ``relation_types`` — на профилях standard/full и
agent (куратору связи нужны для навигации); остальное — только standard/full.

Activity events (ADO-040): каждая запись идёт через ``relation_service``,
который пишет ревизию сущности-источника и событие в той же транзакции.
Обёртки второго события не пишут.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register relation tools on the given FastMCP instance."""

    @mcp.tool(name="relate")
    def relate(
        project: str,
        from_ref: str,
        type: str,
        to_ref: str,
        note: str | None = None,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Связать две сущности отношением ``type``.

        from_ref / to_ref: ``kind:key`` — task, story, criterion (``US-1#2``),
        document, section (``doc_key#anchor``), adr, question, scenario, plan,
        plan_section (``scope#A``), finding, module; цель ещё и ``code:path::symbol``
        или ``url:https://…``. type — имя, алиас или обратное имя из
        ``relation_types`` (``see_also`` → ``relates_to``; обратное имя даёт то же
        ребро с переставленными концами).

        Возвращает ребро как записано: канонический ``type``, направление
        хранения (``from``/``to``), ``edge_uid`` и ``created`` (``false`` — такое
        ребро уже было, события нет). Ошибка называет все неверные параметры,
        допустимые типы пары и пример вызова.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import relation_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            edge = relation_service.relate(
                session,
                project_id=project_id,
                from_ref=from_ref,
                type=type,
                to_ref=to_ref,
                note=note,
                author=author,
            )
            return relation_service.edge_to_dict(edge)

    @mcp.tool(name="unrelate")
    def unrelate(
        project: str,
        from_ref: str,
        type: str,
        to_ref: str,
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Снять связь (те же аргументы, что у ``relate``). ``removed: false`` — её не было."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import relation_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            removed = relation_service.unrelate(
                session,
                project_id=project_id,
                from_ref=from_ref,
                type=type,
                to_ref=to_ref,
                author=author,
            )
            return {"removed": removed}

    @mcp.tool(name="relations")
    def relations(
        project: str,
        ref: str,
        direction: str = "both",
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Все связи сущности одной лентой: ``label``, ``other`` (``kind:key``), ``direction``.

        direction: out | in | both. types: фильтр по именам типов.
        ``source`` каждой записи показывает, откуда связь.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import relation_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            views = relation_service.relations(
                session, project_id=project_id, ref=ref, direction=direction, types=types
            )
            return [relation_service.view_to_dict(v) for v in views]

    @mcp.tool(name="relation_types")
    def relation_types(
        project: str, from_kind: str | None = None, to_kind: str | None = None
    ) -> list[dict[str, Any]]:
        """Словарь отношений: встроенные и проектные типы с числом рёбер.

        from_kind / to_kind (task, story, document, …) оставляют типы, у
        которых эти виды допустимы с соответствующей стороны.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import relation_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            infos = relation_service.relation_types(
                session, project_id=project_id, from_kind=from_kind, to_kind=to_kind
            )
            return [relation_service.type_info_to_dict(i) for i in infos]

    @mcp.tool(name="relation_type_define")
    def relation_type_define(
        project: str,
        name: str,
        from_kinds: list[str],
        to_kinds: list[str],
        inverse: str = "",
        symmetric: bool = False,
        acyclic: bool = False,
        allow_forward: bool = False,
        description: str = "",
        author: str = "mcp",
    ) -> dict[str, Any]:
        """Завести проектный тип отношения без миграции.

        inverse — имя со стороны цели (у symmetric совпадает с name). Встроенное
        имя или обратное имя переопределить нельзя. Повтор того же определения —
        no-op. Новый тип сразу доступен в ``relate``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import relation_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            spec = relation_service.RelationTypeSpec(
                name=name,
                inverse=inverse,
                from_kinds=from_kinds,
                to_kinds=to_kinds,
                symmetric=symmetric,
                acyclic=acyclic,
                allow_forward=allow_forward,
                description=description,
            )
            created = relation_service.define_type(
                session, project_id=project_id, spec=spec, author=author
            )
            return {
                "name": created.name,
                "inverse": created.inverse,
                "from_kinds": sorted(k.value for k in created.from_kinds),
                "to_kinds": sorted(k.value for k in created.to_kinds),
            }

    @mcp.tool(name="relation_type_deprecate")
    def relation_type_deprecate(project: str, name: str, author: str = "mcp") -> dict[str, Any]:
        """Запретить новые рёбра проектного типа; существующие остаются и читаются."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import relation_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            changed = relation_service.deprecate_type(
                session, project_id=project_id, name=name, author=author
            )
            return {"name": name, "deprecated": True, "changed": changed}

    @mcp.tool(name="relation_verify")
    def relation_verify(project: str, refs: list[str] | None = None) -> dict[str, Any]:
        """Проверить концы ``code:``/``url:`` и ссылки вперёд; дописать ``to_uid``, когда цель появилась.

        refs: только рёбра этих источников (``kind:key``); без них — весь проект.
        Пишет только состояние проверки, событий и ревизий нет.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import relation_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            report = relation_service.verify(session, project_id=project_id, refs=refs)
            return relation_service.report_to_dict(report)
