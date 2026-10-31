"""Native MCP server exposing COD-DOC project operations.

Provides a complete LLM interface to COD-DOC. Tool/resource/prompt
implementations live in `cod_doc.mcp.tools.*` modules; this file just
constructs the FastMCP instance, asks each module to register its
surface, and exposes a click-based entry point.

PCA-951: ``--profile minimal|standard|full`` (env ``COD_DOC_PROFILE``)
controls which tools are exposed after registration. See
``cod_doc/mcp/profiles.py``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import click
from mcp.server.fastmcp import FastMCP

from cod_doc.logging_config import get_logger, setup_logging
from cod_doc.mcp.profiles import VALID_PROFILES, keep_tool
from cod_doc.mcp.tools import (
    _workspace,
    activity_tools,
    adr_tools,
    agent_tools,
    approval_tools,
    checkout_tools,
    context_tools,
    curator_tools,
    doc_node_health_tools,
    doc_tools,
    doc_tree_tools,
    finding_tools,
    flow_tools,
    legacy_agent_tools,
    link_tools,
    plan_tools,
    question_tools,
    repair_tools,
    revision_tools,
    roadmap_tools,
    routine_tools,
    run_tools,
    scenario_tools,
    skill_tools,
    story_tools,
    structure_tools,
    sync_tools,
    task_doc_tools,
    task_tools,
)
from cod_doc.services import event_wiring

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

mcp = FastMCP("COD-DOC", json_response=True)

# SVC-007: подписчики событий документов — до первого вызова пишущего тула.
event_wiring.install()

# Order doesn't matter for FastMCP — tool/resource/prompt names live in a
# flat namespace. Group registrations by surface for grep-ability.
for _module in (
    # Legacy YAML-backed agent surface (run_agent_once + context helpers).
    # YAML CRUD tools (project/master/search/resources) removed 2026-06-08
    # under STB-002 now that the DB is the source of truth; run_agent_once
    # is kept as the approval/resume entry point.
    legacy_agent_tools,
    # COD-032 DB-backed tools (doc.*, task.*, plan.*, story.*, link.*, revision.*).
    doc_tools,
    # ADO-116 doc_tree_* / doc_node_* (documentation tree as data).
    doc_tree_tools,
    # doc_node_health_* — пробелы в наполненности разделов.
    doc_node_health_tools,
    task_tools,
    plan_tools,
    story_tools,
    link_tools,
    revision_tools,
    # COD-033 context.get
    context_tools,
    # PCA-032 run.list / run.get
    run_tools,
    # PCA-003 skill.list / skill.get
    skill_tools,
    # PCA-101 task_doc.* (task-bound docs)
    task_doc_tools,
    # PCA-111 activity.* (unified audit timeline)
    activity_tools,
    # PCA-121 approval.* (first-class approvals)
    approval_tools,
    # PCA-200 task_checkout / task_release
    checkout_tools,
    # PCA-211 routine.* (cron-style health checks)
    routine_tools,
    # AGT-001..AGT-007 agent.* (cycle-5 task-centric surface)
    agent_tools,
    # ADR-002 adr.* (Architecture Decision Records)
    adr_tools,
    # RFC 22 / SYM-006D finding.* (external findings; ctx_* aliases live in doc_tools)
    finding_tools,
    # RFC 24 §9 scenario.* (authoring half; the evidence half is STR-004)
    scenario_tools,
    # OQM-003 question.* (open questions — DB entity, never projected to markdown)
    question_tools,
    structure_tools,
    # RFC 25 §3.5 / CUR-016 curator.* (doc card: drift + links + hashes)
    curator_tools,
    # ADO-192 project_repair — фаза D `cod-doc update` (единственная, которую
    # можно выставить на MCP; см. docs/mcp-integration.md)
    repair_tools,
    # RFC 29 §10.7 / CLO-016 sync_* (status, pull, push; standard/full)
    sync_tools,
    # RFC 36 / ROAD-005 roadmap_get (work order + throughput forecast; standard/full)
    roadmap_tools,
    # RFC 33 §3.1 / FLW-003 flow_metrics_rebuild (task_metrics from the journal)
    flow_tools,
):
    _module.register(mcp)


_log = get_logger("mcp.profile")
_ACTIVE_PROFILE: str = "full"


def apply_profile(profile: str) -> dict[str, str | int]:
    """Filter the registered tool catalog to match ``profile``.

    Returns ``{"profile": ..., "kept": N, "dropped": N}`` for logging.
    Mutates ``mcp._tool_manager._tools`` in place; called once on server
    start from ``main()``.
    """
    if profile not in VALID_PROFILES:
        raise ValueError(f"Unknown profile: {profile!r}; expected one of {sorted(VALID_PROFILES)}")
    global _ACTIVE_PROFILE
    _ACTIVE_PROFILE = profile
    os.environ["COD_DOC_ACTIVE_PROFILE"] = profile

    tools = mcp._tool_manager._tools
    to_drop = [name for name in tools if not keep_tool(name, profile)]
    for name in to_drop:
        del tools[name]
    return {"profile": profile, "kept": len(tools), "dropped": len(to_drop)}


def get_active_profile() -> str:
    """Return the profile applied at server startup (default 'full')."""
    return _ACTIVE_PROFILE


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def allow_hosts(hosts: tuple[str, ...]) -> None:
    """Добавить публичные имена к защите от DNS rebinding (CLO-036).

    SDK включает её для loopback-хоста и пускает только ``Host: 127.0.0.1``/
    ``localhost``; за reverse proxy приходит ``Host: cod-doc.example.dev``, и
    без этого каждый запрос получал бы 421.
    """
    security = mcp.settings.transport_security
    if not hosts or security is None:
        return
    security.allowed_hosts.extend(hosts)
    security.allowed_origins.extend(f"https://{h}" for h in hosts)


#: Потолок тела запроса, которое разбирает ``_ToolCapture``: как у SDK.
_MAX_BODY: int = 4 * 1024 * 1024
_HTTP_TOO_LARGE = 413


def _tool_names(body: bytes) -> frozenset[str]:
    """Имена тулов из JSON-RPC тела (``tools/call``; пакет — все). Не разобрать — пусто.

    Пустое множество означает «тулов не зовут» (initialize, tools/list) — для
    reader'а это чтение. Непарсящееся тело SDK отвергнет сам.
    """
    import json

    try:
        message = json.loads(body)
    except ValueError:
        return frozenset()
    messages = message if isinstance(message, list) else [message]
    names: set[str] = set()
    for item in messages:
        if not isinstance(item, dict) or item.get("method") != "tools/call":
            continue
        params = item.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        # Нестроковое имя — заведомо не тул чтения: reader его не получит.
        names.add(name if isinstance(name, str) else "\x00")
    return frozenset(names)


class _ToolCapture:
    """Под токеном — запомнить, какие тулы зовёт запрос (роль reader, CLO-036).

    Тело читается целиком (не больше ``_MAX_BODY``) и отдаётся приложению
    заново. Без привязанного владельца токена — сквозной проход.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from cod_doc.services import request_identity

        if scope["type"] != "http" or request_identity.current() is None:
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                return  # клиент ушёл
            chunk: bytes = message.get("body", b"")
            size += len(chunk)
            if size > _MAX_BODY:
                await send(
                    {"type": "http.response.start", "status": _HTTP_TOO_LARGE, "headers": []}
                )
                await send({"type": "http.response.body", "body": b""})
                return
            chunks.append(chunk)
            more = bool(message.get("more_body", False))
        body = b"".join(chunks)
        request_identity.set_tools(_tool_names(body))
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def gated_http_app() -> ASGIApp:
    """streamable-http приложение MCP за Bearer-гейтом (RFC 29 §3.8, CLO-036)."""
    from cod_doc.api.auth import BearerGate, is_mcp_path

    return BearerGate(_ToolCapture(mcp.streamable_http_app()), protected=is_mcp_path, healthz=True)


def _require_store_off_loopback(host: str) -> None:
    """Не-loopback без хранилища доступа — отказ стартовать: это открытый write всем.

    Внутри контейнера слушать ``0.0.0.0`` нужно всегда; закрывает его гейт,
    а гейт выключен, пока нет хранилища (``cod-doc access init``).
    """
    from cod_doc.services import access_service

    if host in _LOOPBACK_HOSTS or access_service.open_store() is not None:
        return
    msg = (
        f"--host {host}: MCP без аутентификации наружу не выставляется. "
        "Сначала `cod-doc access init` и выдайте токен (`cod-doc token create`)."
    )
    raise click.UsageError(msg)


def run_mcp_server(
    *,
    transport: str,
    host: str,
    port: int,
    profile: str,
    allowed_hosts: tuple[str, ...] = (),
) -> None:
    """Apply ``profile`` and serve. Shared by ``cod-doc-mcp`` and ``cod-doc mcp``.

    ``streamable-http`` means one process fronts every local harness, so the
    per-session default project is disabled for the lifetime of the process
    (see ``cod_doc/mcp/tools/_workspace.py``) and ``project`` becomes required
    on every DB-backed tool. It is always served behind the Bearer gate, which
    is a no-op until the access store exists (CLO-036).
    """
    _workspace.set_shared(transport == "streamable-http")
    stats = apply_profile(profile)
    _log.info(
        "mcp_profile_applied",
        extra={"event_type": "mcp_profile_applied", **stats},
    )
    if transport == "streamable-http":
        import uvicorn

        _require_store_off_loopback(host)
        allow_hosts(allowed_hosts)
        mcp.settings.host = host
        mcp.settings.port = port
        # stateless: каждый запрос исполняется в своём ASGI-вызове, поэтому
        # владелец токена, привязанный гейтом, виден из тула (request_identity).
        mcp.settings.stateless_http = True
        uvicorn.run(gated_http_app(), host=host, port=port, log_level="info")
        return
    mcp.run(transport="stdio")


@click.command()
@click.option("--transport", type=click.Choice(["stdio", "streamable-http"]), default="stdio")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, type=int, show_default=True)
@click.option(
    "--profile",
    type=click.Choice(sorted(VALID_PROFILES)),
    default=os.environ.get("COD_DOC_PROFILE", "agent"),
    show_default=True,
    help="Tool-surface profile (default: agent). agent=curator tools (RFC 25: "
    "curator_next/ctx_search/ctx_drift/context_get + capabilities/report); "
    "minimal=cold-start curated CRUD; "
    "standard=DB-backed tools without legacy; full=everything including legacy "
    "agent tools. Counts: python -m cod_doc.mcp.profile_counts.",
)
@click.option(
    "--allowed-host",
    "allowed_hosts",
    multiple=True,
    envvar="COD_DOC_MCP_ALLOWED_HOSTS",
    help="Public Host header accepted behind a reverse proxy (repeatable; "
    "env COD_DOC_MCP_ALLOWED_HOSTS, space-separated).",
)
@click.option("--log-level", default=None, envvar="LOG_LEVEL")
@click.option("--log-format", default=None, envvar="LOG_FORMAT")
def main(
    transport: str,
    host: str,
    port: int,
    profile: str,
    allowed_hosts: tuple[str, ...],
    log_level: str | None,
    log_format: str | None,
) -> None:
    """Run the COD-DOC MCP server."""
    setup_logging(level=log_level, fmt=log_format)
    run_mcp_server(
        transport=transport,
        host=host,
        port=port,
        profile=profile,
        allowed_hosts=allowed_hosts,
    )


if __name__ == "__main__":
    main()
