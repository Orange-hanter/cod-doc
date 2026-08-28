"""Команды серверов: serve, mcp."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from cod_doc.config import Config


@click.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
@click.option("--reload", is_flag=True, default=False)
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None, reload: bool) -> None:
    """Запустить REST API сервер (production)."""
    import os

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
@click.pass_context
def mcp_server(ctx: click.Context, transport: str, host: str, port: int) -> None:
    """Запустить MCP-сервер поверх COD-DOC."""
    from cod_doc.mcp.server import mcp

    if transport == "streamable-http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.settings.stateless_http = True
        mcp.run(transport="streamable-http")
        return
    mcp.run(transport="stdio")
