"""Install and diagnose the local MCP stdio server for IDE hosts.

``cod-doc mcp`` still *runs* the server. This group writes host configs with
an absolute ``command`` so Cursor / VS Code / Claude Desktop / Codex can
spawn it. The plugin package does not ship that spawn.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import click
from rich.console import Console
from rich.table import Table

from cod_doc.services.mcp_host import (
    CLIENTS,
    IDE_PROFILE,
    HandshakeError,
    McpBinaryNotFoundError,
    doctor,
    ensure_user_shim,
    handshake,
    install_client,
    resolve_mcp_binary,
)

console = Console()

_CLIENT_CHOICE = click.Choice((*CLIENTS, "all"))
F = TypeVar("F", bound=Callable[..., object])


def _home_option(fn: F) -> F:
    return click.option(
        "--home",
        type=click.Path(path_type=Path),
        default=None,
        help="Override the user home used for host config files (tests / dry wiring).",
    )(fn)


def _project_root_option(fn: F) -> F:
    return click.option(
        "--project-root",
        type=click.Path(path_type=Path),
        default=None,
        help="Project root for .venv lookup and VS Code .vscode/mcp.json.",
    )(fn)


def _search_system_option(fn: F) -> F:
    return click.option(
        "--search-system/--no-search-system",
        default=True,
        show_default=True,
        help="Also search Homebrew, /usr/local/bin, and PATH.",
    )(fn)


@click.group("connect")
def connect() -> None:
    """Install and diagnose the MCP stdio server for IDE hosts."""


@connect.command("which")
@_home_option
@_project_root_option
@_search_system_option
def which_cmd(
    home: Path | None,
    project_root: Path | None,
    search_system: bool,
) -> None:
    """Print the resolved absolute path of ``cod-doc-mcp``."""
    try:
        binary = resolve_mcp_binary(
            project_root=_project_root(project_root),
            home=_home(home),
            env=os.environ,
            include_system=search_system,
        )
    except McpBinaryNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    click.echo(str(binary))


@connect.command("install")
@click.option(
    "--client",
    "client_name",
    type=_CLIENT_CHOICE,
    default="cursor",
    show_default=True,
    help="Host config to merge. ``all`` writes every supported host.",
)
@click.option(
    "--profile",
    default=IDE_PROFILE,
    show_default=True,
    help="MCP profile for the spawned server. IDE default is standard (CRUD).",
)
@click.option(
    "--no-handshake",
    is_flag=True,
    default=False,
    help="Skip the stdio initialize probe before writing configs.",
)
@click.option(
    "--no-shim",
    is_flag=True,
    default=False,
    help="Do not create ~/.local/bin/cod-doc-mcp → resolved binary.",
)
@_home_option
@_project_root_option
@_search_system_option
def install_cmd(
    client_name: str,
    profile: str,
    no_handshake: bool,
    no_shim: bool,
    home: Path | None,
    project_root: Path | None,
    search_system: bool,
) -> None:
    """Resolve ``cod-doc-mcp``, probe it, and merge a host-safe spawn config."""
    user_home = _home(home)
    root = _project_root(project_root)
    try:
        binary = resolve_mcp_binary(
            project_root=root,
            home=user_home,
            env=os.environ,
            include_system=search_system,
        )
    except McpBinaryNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    if not no_handshake:
        try:
            result = handshake(binary, profile=profile)
        except HandshakeError as exc:
            console.print(f"[red]handshake failed: {exc}[/red]")
            sys.exit(1)
        console.print(
            f"[green]handshake ok[/green] serverInfo.name={result.server_name} "
            f"protocol={result.protocol_version}"
        )
    if not no_shim:
        try:
            shim = ensure_user_shim(binary, home=user_home)
        except OSError as exc:
            console.print(f"[yellow]shim skipped:[/yellow] {exc}")
        else:
            console.print(f"shim {shim} → {binary}")
    targets = CLIENTS if client_name == "all" else (client_name,)
    for name in targets:
        installed = install_client(
            name,
            binary=binary,
            home=user_home,
            project_root=root,
            profile=profile,
        )
        console.print(f"[green]wrote[/green] {installed.client}: {installed.path}")


@connect.command("doctor")
@click.option(
    "--no-handshake",
    is_flag=True,
    default=False,
    help="Skip the stdio initialize probe.",
)
@_home_option
@_project_root_option
@_search_system_option
def doctor_cmd(
    no_handshake: bool,
    home: Path | None,
    project_root: Path | None,
    search_system: bool,
) -> None:
    """Show the resolved binary, host configs, and initialize result."""
    report = doctor(
        project_root=_project_root(project_root),
        home=_home(home),
        env=os.environ,
        skip_handshake=no_handshake,
        include_system=search_system,
    )
    if report.binary is None:
        console.print("[red]binary:[/red] not found")
    else:
        console.print(f"[green]binary:[/green] {report.binary}")
    if report.handshake_ok:
        console.print("[green]handshake:[/green] ok")
    else:
        console.print(f"[red]handshake:[/red] {report.handshake_error}")
    table = Table(title="host configs")
    table.add_column("client")
    table.add_column("present")
    table.add_column("safe")
    table.add_column("command")
    table.add_column("path")
    for item in report.configs:
        table.add_row(
            item.client,
            "yes" if item.present else "no",
            "yes" if item.host_safe else "NO",
            item.command or "—",
            str(item.path),
        )
    console.print(table)
    console.print(f"namespace: {report.namespace_hint}")
    unsafe = any(item.present and not item.host_safe for item in report.configs)
    if report.binary is None or not report.handshake_ok or unsafe:
        sys.exit(1)


def _home(value: Path | None) -> Path:
    return value.expanduser().resolve() if value is not None else Path.home()


def _project_root(value: Path | None) -> Path:
    return value.expanduser().resolve() if value is not None else Path.cwd()
