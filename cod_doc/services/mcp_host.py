"""Resolve the local ``cod-doc-mcp`` binary and wire MCP host configs.

Host configs always store an *absolute* ``command``. Cursor's Claude-plugin
bridge interpolates ``${…}`` and expands ``./`` without a workspace; those
forms are unsupported here. The plugin package ships skills/commands/hooks;
this module writes the stdio spawn config the host actually launches.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import IO

SERVER_NAME: Final[str] = "cod-doc"
EXPECTED_SERVER_INFO_NAME: Final[str] = "COD-DOC"
IDE_PROFILE: Final[str] = "standard"
HANDSHAKE_TIMEOUT_SEC: Final[float] = 8.0
PROCESS_STOP_TIMEOUT_SEC: Final[float] = 2.0
JSONRPC_INITIALIZE_ID: Final[int] = 1
MCP_PROTOCOL_VERSION: Final[str] = "2024-11-05"
JSON_INDENT: Final[int] = 2
PREVIEW_CHARS: Final[int] = 200

CLIENTS: Final[tuple[str, ...]] = (
    "cursor",
    "claude-code",
    "vscode",
    "claude-desktop",
    "codex",
)

_CODEX_TABLE: Final[str] = "[mcp_servers.cod-doc]"
_CODEX_TABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\[mcp_servers\.cod-doc\][^\[]*",
    re.MULTILINE,
)


class McpBinaryNotFoundError(FileNotFoundError):
    """No ``cod-doc-mcp`` executable in the documented search path."""


class HandshakeError(RuntimeError):
    """The binary started but did not complete MCP ``initialize``."""


@dataclass(frozen=True)
class StdioServer:
    """Absolute-command stdio spawn (Cursor / Claude Desktop / Claude Code user)."""

    command: str
    args: tuple[str, ...]

    def as_dict(self, *, with_type: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {"command": self.command, "args": list(self.args)}
        if with_type:
            payload["type"] = "stdio"
        return payload


@dataclass(frozen=True)
class HandshakeResult:
    server_name: str
    protocol_version: str
    raw: dict[str, object]


@dataclass(frozen=True)
class ConfigStatus:
    client: str
    path: Path
    present: bool
    command: str | None
    host_safe: bool


@dataclass(frozen=True)
class InstallResult:
    client: str
    path: Path
    binary: Path


@dataclass(frozen=True)
class DoctorReport:
    binary: Path | None
    handshake_ok: bool
    handshake_error: str | None
    configs: tuple[ConfigStatus, ...]
    namespace_hint: str


def stdio_server(binary: Path, *, profile: str = IDE_PROFILE) -> StdioServer:
    """Build the host-safe spawn record for ``binary``."""
    resolved = binary.expanduser().resolve()
    return StdioServer(command=str(resolved), args=("--profile", profile))


def assert_host_safe(entry: Mapping[str, object]) -> None:
    """Reject interpolation and relative commands that Cursor mangles."""
    blob = json.dumps(dict(entry))
    if "${" in blob:
        msg = "host MCP config must not contain ${…} interpolation"
        raise ValueError(msg)
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        msg = "host MCP config needs an absolute command"
        raise ValueError(msg)
    if command.startswith("./") or not Path(command).is_absolute():
        msg = f"host MCP command must be an absolute path, got {command!r}"
        raise ValueError(msg)


def resolve_mcp_binary(
    *,
    project_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    include_system: bool = True,
) -> Path:
    """Return an executable ``cod-doc-mcp`` or raise ``McpBinaryNotFoundError``.

    ``COD_DOC_MCP_BIN``, when set, is exclusive: a bad override does not
    fall through to ``.venv`` or PATH.
    """
    environ = env if env is not None else os.environ
    user_home = home if home is not None else Path.home()
    override = environ.get("COD_DOC_MCP_BIN", "").strip()
    if override:
        return _require_executable(Path(override), label="COD_DOC_MCP_BIN")
    for candidate in _search_candidates(
        project_root=project_root,
        home=user_home,
        env=environ,
        include_system=include_system,
    ):
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    msg = (
        "cod-doc-mcp not found. Install the package "
        "(pip install -e '.[dev]') or set COD_DOC_MCP_BIN."
    )
    raise McpBinaryNotFoundError(msg)


def ensure_user_shim(binary: Path, *, home: Path | None = None) -> Path:
    """Point ``~/.local/bin/cod-doc-mcp`` at ``binary`` when missing or stale."""
    user_home = home if home is not None else Path.home()
    shim = user_home / ".local" / "bin" / "cod-doc-mcp"
    shim.parent.mkdir(parents=True, exist_ok=True)
    target = binary.resolve()
    if shim.exists() or shim.is_symlink():
        if shim.resolve() == target:
            return shim
        shim.unlink()
    shim.symlink_to(target)
    return shim


def handshake(
    binary: Path,
    *,
    profile: str = IDE_PROFILE,
    timeout_sec: float = HANDSHAKE_TIMEOUT_SEC,
) -> HandshakeResult:
    """JSON-RPC ``initialize`` against a stdio ``cod-doc-mcp`` process."""
    request = {
        "jsonrpc": "2.0",
        "id": JSONRPC_INITIALIZE_ID,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "cod-doc-connect", "version": "0"},
        },
    }
    payload = json.dumps(request) + "\n"
    proc = subprocess.Popen(
        [str(binary), "--profile", profile],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if proc.stdin is None or proc.stdout is None:
            msg = "stdio pipes were not created"
            raise HandshakeError(msg)
        proc.stdin.write(payload.encode())
        proc.stdin.flush()
        line = _read_line(proc.stdout, timeout_sec)
        return _parse_initialize(line)
    finally:
        _stop(proc)


def cursor_config_path(home: Path) -> Path:
    return home / ".cursor" / "mcp.json"


def claude_code_config_path(home: Path) -> Path:
    return home / ".claude.json"


def vscode_config_path(project_root: Path) -> Path:
    return project_root / ".vscode" / "mcp.json"


def claude_desktop_config_path(home: Path) -> Path:
    """Claude Desktop config: macOS layout when ``Library/`` exists, else XDG."""
    mac = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    linux = home / ".config" / "Claude" / "claude_desktop_config.json"
    if mac.exists() or (home / "Library").is_dir():
        return mac
    return linux


def codex_config_path(home: Path) -> Path:
    return home / ".codex" / "config.toml"


def merge_json_server(
    path: Path,
    *,
    servers_key: str,
    entry: Mapping[str, object],
) -> Path:
    """Write ``cod-doc`` under ``servers_key``, leaving other servers intact."""
    assert_host_safe(entry)
    data: dict[str, object] = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = raw
    servers = data.get(servers_key)
    merged: dict[str, object] = dict(servers) if isinstance(servers, dict) else {}
    merged[SERVER_NAME] = dict(entry)
    data[servers_key] = merged
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=JSON_INDENT) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    tmp.replace(path)
    return path


def merge_codex_toml(path: Path, *, command: str, args: tuple[str, ...]) -> Path:
    """Upsert ``[mcp_servers.cod-doc]`` without rewriting the rest of the file."""
    assert_host_safe({"command": command, "args": list(args)})
    args_literal = "[" + ", ".join(json.dumps(item) for item in args) + "]"
    block = f"{_CODEX_TABLE}\ncommand = {json.dumps(command)}\nargs = {args_literal}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return path
    text = path.read_text(encoding="utf-8")
    if _CODEX_TABLE_RE.search(text):
        text = _CODEX_TABLE_RE.sub(block + "\n", text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    path.write_text(text, encoding="utf-8")
    return path


def install_client(
    client: str,
    *,
    binary: Path,
    home: Path,
    project_root: Path,
    profile: str = IDE_PROFILE,
) -> InstallResult:
    """Merge a host-safe ``cod-doc`` server into ``client``'s config."""
    if client not in CLIENTS:
        msg = f"unknown MCP client: {client!r}"
        raise ValueError(msg)
    server = stdio_server(binary, profile=profile)
    entry = server.as_dict()
    writers = {
        "cursor": lambda: merge_json_server(
            cursor_config_path(home), servers_key="mcpServers", entry=entry
        ),
        "claude-code": lambda: merge_json_server(
            claude_code_config_path(home), servers_key="mcpServers", entry=entry
        ),
        "vscode": lambda: merge_json_server(
            vscode_config_path(project_root), servers_key="servers", entry=entry
        ),
        "claude-desktop": lambda: merge_json_server(
            claude_desktop_config_path(home), servers_key="mcpServers", entry=entry
        ),
        "codex": lambda: merge_codex_toml(
            codex_config_path(home), command=server.command, args=server.args
        ),
    }
    path = writers[client]()
    return InstallResult(client=client, path=path, binary=binary)


def inspect_config(client: str, *, home: Path, project_root: Path) -> ConfigStatus:
    """Describe whether a host config exists and looks host-safe."""
    path = _config_path(client, home=home, project_root=project_root)
    if not path.exists():
        return ConfigStatus(client=client, path=path, present=False, command=None, host_safe=True)
    try:
        entry = _read_entry(client, path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ConfigStatus(client=client, path=path, present=True, command=None, host_safe=False)
    if entry is None:
        return ConfigStatus(client=client, path=path, present=False, command=None, host_safe=True)
    raw_command = entry.get("command")
    command = raw_command if isinstance(raw_command, str) else None
    try:
        assert_host_safe(entry)
    except ValueError:
        return ConfigStatus(
            client=client,
            path=path,
            present=True,
            command=command,
            host_safe=False,
        )
    return ConfigStatus(
        client=client,
        path=path,
        present=True,
        command=command,
        host_safe=True,
    )


def doctor(
    *,
    project_root: Path,
    home: Path,
    env: Mapping[str, str] | None = None,
    skip_handshake: bool = False,
    include_system: bool = True,
) -> DoctorReport:
    """Resolve the binary, optionally handshake, and scan host configs."""
    binary: Path | None
    try:
        binary = resolve_mcp_binary(
            project_root=project_root,
            env=env,
            home=home,
            include_system=include_system,
        )
    except McpBinaryNotFoundError:
        binary = None
    handshake_ok = False
    handshake_error: str | None = None
    if binary is None:
        handshake_error = "cod-doc-mcp not found"
    elif skip_handshake:
        handshake_ok = True
    else:
        try:
            handshake(binary)
        except HandshakeError as exc:
            handshake_error = str(exc)
        else:
            handshake_ok = True
    configs = tuple(
        inspect_config(client, home=home, project_root=project_root) for client in CLIENTS
    )
    cursor = next(item for item in configs if item.client == "cursor")
    namespace_hint = (
        "user-cod-doc (Cursor user MCP). Ignore red plugin-cod-doc-cod-doc."
        if cursor.present and cursor.host_safe
        else "run: cod-doc connect install --client cursor"
    )
    return DoctorReport(
        binary=binary,
        handshake_ok=handshake_ok,
        handshake_error=handshake_error,
        configs=configs,
        namespace_hint=namespace_hint,
    )


def _config_path(client: str, *, home: Path, project_root: Path) -> Path:
    if client == "cursor":
        return cursor_config_path(home)
    if client == "claude-code":
        return claude_code_config_path(home)
    if client == "vscode":
        return vscode_config_path(project_root)
    if client == "claude-desktop":
        return claude_desktop_config_path(home)
    return codex_config_path(home)


def _require_executable(path: Path, *, label: str) -> Path:
    resolved = path.expanduser()
    if resolved.is_file() and os.access(resolved, os.X_OK):
        return resolved.resolve()
    msg = f"{label} is not executable: {path}"
    raise McpBinaryNotFoundError(msg)


def _search_candidates(
    *,
    project_root: Path | None,
    home: Path,
    env: Mapping[str, str],
    include_system: bool,
) -> Sequence[Path]:
    paths: list[Path] = []
    if project_root is not None:
        paths.append(project_root.expanduser() / ".venv" / "bin" / "cod-doc-mcp")
    paths.extend(
        (
            home / ".local" / "bin" / "cod-doc-mcp",
            home / ".local" / "share" / "uv" / "tools" / "cod-doc" / "bin" / "cod-doc-mcp",
        )
    )
    if include_system:
        paths.extend((Path("/opt/homebrew/bin/cod-doc-mcp"), Path("/usr/local/bin/cod-doc-mcp")))
        which = shutil.which("cod-doc-mcp", path=env.get("PATH"))
        if which:
            paths.append(Path(which))
    return paths


def _parse_initialize(line: str) -> HandshakeResult:
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        msg = f"initialize returned non-JSON: {line[:PREVIEW_CHARS]!r}"
        raise HandshakeError(msg) from exc
    if not isinstance(message, dict):
        msg = f"initialize returned a non-object: {message!r}"
        raise HandshakeError(msg)
    if "error" in message:
        msg = f"initialize error: {message['error']!r}"
        raise HandshakeError(msg)
    result = message.get("result")
    if not isinstance(result, dict):
        msg = f"initialize missing result: {message!r}"
        raise HandshakeError(msg)
    info = result.get("serverInfo")
    if not isinstance(info, dict):
        msg = f"initialize missing serverInfo: {result!r}"
        raise HandshakeError(msg)
    name = info.get("name")
    if name != EXPECTED_SERVER_INFO_NAME:
        msg = f"expected serverInfo.name={EXPECTED_SERVER_INFO_NAME!r}, got {name!r}"
        raise HandshakeError(msg)
    protocol = result.get("protocolVersion")
    protocol_s = protocol if isinstance(protocol, str) else MCP_PROTOCOL_VERSION
    return HandshakeResult(
        server_name=str(name),
        protocol_version=protocol_s,
        raw=cast("dict[str, object]", result),
    )


def _read_entry(client: str, path: Path) -> dict[str, object] | None:
    if client == "codex":
        text = path.read_text(encoding="utf-8")
        block_match = _CODEX_TABLE_RE.search(text)
        if block_match is None:
            return None
        command_match = re.search(r'(?m)^command\s*=\s*"(.*)"\s*$', block_match.group(0))
        if command_match is None:
            return None
        return {"command": command_match.group(1), "args": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    key = "servers" if client == "vscode" else "mcpServers"
    servers = raw.get(key)
    if not isinstance(servers, dict):
        return None
    entry = servers.get(SERVER_NAME)
    if not isinstance(entry, dict):
        return None
    return cast("dict[str, object]", entry)


def _read_line(stream: IO[bytes], timeout_sec: float) -> str:
    ready, _, _ = select.select([stream], [], [], timeout_sec)
    if not ready:
        msg = f"initialize timed out after {timeout_sec}s"
        raise HandshakeError(msg)
    raw = stream.readline()
    if not raw:
        msg = "initialize: server closed stdout"
        raise HandshakeError(msg)
    return raw.decode()


def _stop(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=PROCESS_STOP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=PROCESS_STOP_TIMEOUT_SEC)
