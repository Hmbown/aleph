"""Remote MCP server lifecycle helpers for the local server."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable


@dataclass
class _RemoteServerHandle:
    """A managed remote MCP server connection (stdio transport)."""

    command: str
    args: list[str] = field(default_factory=list)
    cwd: Path | None = None
    env: dict[str, str] | None = None
    allow_tools: list[str] | None = None
    deny_tools: list[str] | None = None

    connected_at: datetime | None = None
    session: Any | None = None  # ClientSession (kept as Any to avoid hard dependency at import time)
    _stack: AsyncExitStack | None = None


def register_remote_server(
    remote_servers: dict[str, _RemoteServerHandle],
    server_id: str,
    *,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    allow_tools: list[str] | None = None,
    deny_tools: list[str] | None = None,
) -> _RemoteServerHandle:
    handle = _RemoteServerHandle(
        command=command,
        args=args or [],
        env=env,
        cwd=cwd,
        allow_tools=allow_tools,
        deny_tools=deny_tools,
    )
    remote_servers[server_id] = handle
    return handle


def list_registered_remote_servers(
    remote_servers: dict[str, _RemoteServerHandle],
) -> list[dict[str, Any]]:
    items = []
    for server_id, handle in remote_servers.items():
        items.append(
            {
                "id": server_id,
                "connected": handle.session is not None,
                "command": handle.command,
                "connected_at": handle.connected_at.isoformat() if handle.connected_at else None,
            }
        )
    return items


def remote_tool_allowed(handle: _RemoteServerHandle, tool_name: str) -> bool:
    if handle.allow_tools is not None:
        return tool_name in handle.allow_tools
    if handle.deny_tools is not None and tool_name in handle.deny_tools:
        return False
    return True


async def ensure_remote_server(
    remote_servers: dict[str, _RemoteServerHandle],
    server_id: str,
) -> tuple[bool, str | _RemoteServerHandle]:
    """Ensure a remote MCP server is connected and initialized."""
    if server_id not in remote_servers:
        return False, f"Error: Remote server '{server_id}' not registered."

    handle = remote_servers[server_id]
    if handle.session is not None:
        return True, handle

    try:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except Exception as e:  # pragma: no cover
        return False, f"Error: MCP client support is not available: {e}"

    params = StdioServerParameters(
        command=handle.command,
        args=handle.args,
        env=handle.env,
        cwd=str(handle.cwd) if handle.cwd is not None else None,
    )

    stack = AsyncExitStack()
    try:
        read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
    except Exception as e:
        await stack.aclose()
        return False, f"Error: Failed to connect to remote server '{server_id}': {e}"

    handle._stack = stack
    handle.session = session
    handle.connected_at = datetime.now()
    return True, handle


async def reset_remote_server_handle(handle: _RemoteServerHandle) -> None:
    """Close and clear a remote server handle without removing registration."""
    if handle._stack is not None:
        try:
            await handle._stack.aclose()
        finally:
            handle._stack = None
            handle.session = None
            handle.connected_at = None
    else:
        handle.session = None
        handle.connected_at = None


async def close_remote_server(
    remote_servers: dict[str, _RemoteServerHandle],
    server_id: str,
) -> tuple[bool, str]:
    """Close a remote server connection and terminate the subprocess."""
    if server_id not in remote_servers:
        return False, f"Error: Remote server '{server_id}' not registered."

    handle = remote_servers[server_id]
    await reset_remote_server_handle(handle)
    return True, f"Closed remote server '{server_id}'."


async def remote_list_tools(
    remote_servers: dict[str, _RemoteServerHandle],
    server_id: str,
    *,
    to_jsonable: Callable[[Any], Any],
) -> tuple[bool, Any]:
    ok, res = await ensure_remote_server(remote_servers, server_id)
    if not ok:
        return False, res
    if not isinstance(res, _RemoteServerHandle):
        return False, res
    handle = res
    session = handle.session
    if session is None:
        return False, f"Error: Remote server '{server_id}' is not connected."
    try:
        result = await session.list_tools()
        return True, to_jsonable(result)
    except Exception:
        await reset_remote_server_handle(handle)
        ok, res = await ensure_remote_server(remote_servers, server_id)
        if not ok:
            return False, f"Error: list_tools failed and reconnect failed: {res}"
        if not isinstance(res, _RemoteServerHandle):
            return False, res
        handle = res
        session = handle.session
        if session is None:
            return False, f"Error: Remote server '{server_id}' is not connected."
        try:
            result = await session.list_tools()
            return True, to_jsonable(result)
        except Exception as e2:
            return False, f"Error: list_tools failed after reconnect: {e2}"


async def remote_call_tool(
    remote_servers: dict[str, _RemoteServerHandle],
    server_id: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout_seconds: float | None,
    default_timeout_seconds: float,
    to_jsonable: Callable[[Any], Any],
) -> tuple[bool, Any]:
    ok, res = await ensure_remote_server(remote_servers, server_id)
    if not ok:
        return False, res
    if not isinstance(res, _RemoteServerHandle):
        return False, res
    handle = res

    if not remote_tool_allowed(handle, tool):
        return False, f"Error: Tool '{tool}' is not allowed for remote server '{server_id}'."

    read_timeout = timedelta(seconds=float(timeout_seconds or default_timeout_seconds))
    session = handle.session
    if session is None:
        return False, f"Error: Remote server '{server_id}' is not connected."
    try:
        result = await session.call_tool(
            name=tool,
            arguments=arguments or {},
            read_timeout_seconds=read_timeout,
        )
    except Exception:
        await reset_remote_server_handle(handle)
        ok, res = await ensure_remote_server(remote_servers, server_id)
        if not ok:
            return False, f"Error: call_tool failed and reconnect failed: {res}"
        if not isinstance(res, _RemoteServerHandle):
            return False, res
        handle = res
        session = handle.session
        if session is None:
            return False, f"Error: Remote server '{server_id}' is not connected."
        try:
            result = await session.call_tool(
                name=tool,
                arguments=arguments or {},
                read_timeout_seconds=read_timeout,
            )
        except Exception as e2:
            return False, f"Error: call_tool failed after reconnect: {e2}"

    return True, to_jsonable(result)
