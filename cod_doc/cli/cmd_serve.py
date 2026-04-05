"""Команды серверов: serve, mcp."""

from __future__ import annotations

import click

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
    uvicorn.run(
        "cod_doc.api.server:app",
        host=host or cfg.api_host,
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
        mcp.run(transport=transport, host=host, port=port, stateless_http=True)
        return
    mcp.run(transport=transport)
