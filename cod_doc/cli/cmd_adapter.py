"""CLI commands for managing LLM adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()

PLUGIN_FILE = Path.home() / ".cod-doc" / "adapters.json"


def _load_plugin_entries() -> list[dict[str, str]]:
    """Read ~/.cod-doc/adapters.json or return [] when absent."""
    if not PLUGIN_FILE.exists():
        return []
    try:
        data = json.loads(PLUGIN_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception as exc:
        console.print(f"[red]Failed to read {PLUGIN_FILE}: {exc}[/red]")
        return []


def _save_plugin_entries(entries: list[dict[str, str]]) -> None:
    PLUGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLUGIN_FILE.write_text(json.dumps(entries, indent=2))


@click.group()
def adapter() -> None:
    """Manage LLM adapters (built-in + external plugins)."""


@adapter.command("list")
def adapter_list() -> None:
    """List all registered adapters (built-in + external)."""
    from cod_doc.agent.adapters.registry import list_adapters

    names = list_adapters()
    plugins = {e["name"]: e for e in _load_plugin_entries()}

    table = Table(title="LLM adapters")
    table.add_column("Name")
    table.add_column("Source")
    table.add_column("Module")
    for n in names:
        if n in plugins:
            entry = plugins[n]
            table.add_row(n, "external", f"{entry.get('module','?')}.{entry.get('class','?')}")
        else:
            table.add_row(n, "built-in", "—")
    # Plugins not yet imported (e.g. import error) still show up
    for name, entry in plugins.items():
        if name not in names:
            table.add_row(name, "external (load error)", f"{entry.get('module','?')}.{entry.get('class','?')}")
    console.print(table)


@adapter.command("add")
@click.argument("name")
@click.option("--module", required=True, help="Python module path (e.g. my_pkg.adapter)")
@click.option("--class", "class_", required=True, help="Adapter class name with `from_config` classmethod")
def adapter_add(name: str, module: str, class_: str) -> None:
    """Register an external adapter into ~/.cod-doc/adapters.json.

    The class must expose ``from_config(config)`` returning an LLMAdapter
    instance. Verified by import; ImportError is surfaced before saving.
    """
    # Verify the module loads + class exists before persisting.
    try:
        import importlib
        mod = importlib.import_module(module)
        cls = getattr(mod, class_, None)
        if cls is None:
            console.print(f"[red]Module {module} has no class {class_}[/red]")
            sys.exit(1)
        if not callable(getattr(cls, "from_config", None)):
            console.print(f"[red]{class_} must define classmethod from_config(config)[/red]")
            sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Cannot import {module}: {exc}[/red]")
        sys.exit(1)

    entries = _load_plugin_entries()
    entries = [e for e in entries if e.get("name") != name]
    entries.append({"name": name, "module": module, "class": class_})
    _save_plugin_entries(entries)
    console.print(f"[green]Adapter {name!r} registered → {PLUGIN_FILE}[/green]")
    console.print(f"  module: {module}")
    console.print(f"  class:  {class_}")


@adapter.command("remove")
@click.argument("name")
def adapter_remove(name: str) -> None:
    """Remove an external adapter from ~/.cod-doc/adapters.json.

    Built-in adapters cannot be removed.
    """
    entries = _load_plugin_entries()
    new_entries = [e for e in entries if e.get("name") != name]
    if len(new_entries) == len(entries):
        console.print(f"[yellow]Adapter {name!r} not in plugin file (or built-in)[/yellow]")
        sys.exit(1)
    _save_plugin_entries(new_entries)
    console.print(f"[green]Adapter {name!r} removed from {PLUGIN_FILE}[/green]")


@adapter.command("show")
@click.argument("name")
def adapter_show(name: str) -> None:
    """Show details of an adapter (built-in or external)."""
    from cod_doc.agent.adapters.registry import _REGISTRY, _load_plugins

    _load_plugins()
    if name not in _REGISTRY:
        plugins = {e["name"]: e for e in _load_plugin_entries()}
        if name in plugins:
            console.print("[yellow]External plugin entry exists but failed to load:[/yellow]")
            console.print(json.dumps(plugins[name], indent=2))
        else:
            console.print(f"[red]Unknown adapter: {name}[/red]")
        sys.exit(1)

    plugins = {e["name"]: e for e in _load_plugin_entries()}
    is_external = name in plugins
    console.print(f"[bold]{name}[/bold]  ({'external' if is_external else 'built-in'})")
    if is_external:
        e = plugins[name]
        console.print(f"  module: {e.get('module')}")
        console.print(f"  class:  {e.get('class')}")
    # Try to instantiate with a fake config to read capabilities
    try:
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.api_key = ""
        cfg.base_url = ""
        cfg.anthropic_api_key = ""
        adapter_inst = _REGISTRY[name](cfg)
        caps = getattr(adapter_inst, "capabilities", None)
        if caps:
            console.print("  capabilities:")
            for field in ("tool_use", "streaming", "json_mode", "vision", "parallel_tool_calls"):
                v = getattr(caps, field, None)
                if v is not None:
                    console.print(f"    {field}: {v}")
    except Exception as exc:
        console.print(f"  [yellow]capabilities probe failed: {exc}[/yellow]")
