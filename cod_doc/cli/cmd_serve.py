"""Команды серверов: serve, mcp."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import click

from cod_doc.mcp.profiles import VALID_PROFILES

if TYPE_CHECKING:
    from cod_doc.config import Config


@click.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
@click.option("--reload", is_flag=True, default=False)
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None, reload: bool) -> None:
    """Запустить REST API сервер (production)."""

    import uvicorn

    cfg: Config = ctx.obj["config"]
    # SYM-003: приоритет --host > COD_DOC_BIND > config.api_host (default
    # 127.0.0.1). COD_DOC_BIND=0.0.0.0 — осознанный возврат к старому
    # поведению «слушать все интерфейсы».
    bind = os.environ.get("COD_DOC_BIND")
    uvicorn.run(
        "cod_doc.api.server:app",
        host=host or bind or cfg.api_host,
        port=port or cfg.api_port,
        reload=reload,
        log_level="info",
    )


@click.command("mcp")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "streamable-http"]),
    default="stdio",
)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, type=int, show_default=True)
@click.option(
    "--profile",
    type=click.Choice(sorted(VALID_PROFILES)),
    default=os.environ.get("COD_DOC_PROFILE", "agent"),
    show_default=True,
    help="Same catalog filter as `cod-doc-mcp --profile` (ADO-079).",
)
def mcp_server(transport: str, host: str, port: int, profile: str) -> None:
    """Запустить MCP-сервер поверх COD-DOC."""
    from cod_doc.mcp.server import run_mcp_server

    run_mcp_server(transport=transport, host=host, port=port, profile=profile)
