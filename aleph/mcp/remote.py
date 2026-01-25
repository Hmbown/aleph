"""Remote MCP orchestration helpers."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable


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


class RemoteOrchestrator:
    """Orchestrates remote MCP servers over stdio transport."""

    def __init__(
        self,
        remote_servers: dict[str, _RemoteServerHandle],
        to_jsonable: Callable[[Any], Any],
        default_timeout_seconds: float,
    ) -> None:
        self._remote_servers = remote_servers
        self._to_jsonable = to_jsonable
        self._default_timeout_seconds = default_timeout_seconds

    async def _with_reconnect(
        self,
        server_id: str,
        action_name: str,
        action: Callable[[_RemoteServerHandle], Awaitable[Any]],
    ) -> tuple[bool, Any]:
        ok, res = await self.ensure_remote_server(server_id)
        if not ok:
            return False, res
        if not isinstance(res, _RemoteServerHandle):
            return False, res
        handle = res
        try:
            return True, await action(handle)
        except Exception:
            await self.reset_remote_server_handle(handle)
            ok, res = await self.ensure_remote_server(server_id)
            if not ok:
                return False, f"Error: {action_name} failed and reconnect failed: {res}"
            if not isinstance(res, _RemoteServerHandle):
                return False, res
            handle = res
            try:
                return True, await action(handle)
            except Exception as e2:
                return False, f"Error: {action_name} failed after reconnect: {e2}"

    async def ensure_remote_server(self, server_id: str) -> tuple[bool, str | _RemoteServerHandle]:
        """Ensure a remote MCP server is connected and initialized."""
        if server_id not in self._remote_servers:
            return False, f"Error: Remote server '{server_id}' not registered."

        handle = self._remote_servers[server_id]
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

    async def reset_remote_server_handle(self, handle: _RemoteServerHandle) -> None:
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

    async def close_remote_server(self, server_id: str) -> tuple[bool, str]:
        """Close a remote server connection and terminate the subprocess."""
        if server_id not in self._remote_servers:
            return False, f"Error: Remote server '{server_id}' not registered."

        handle = self._remote_servers[server_id]
        await self.reset_remote_server_handle(handle)
        return True, f"Closed remote server '{server_id}'."

    async def remote_list_tools(self, server_id: str) -> tuple[bool, Any]:
        ok, res = await self._with_reconnect(
            server_id,
            "list_tools",
            lambda handle: handle.session.list_tools(),  # type: ignore[union-attr]
        )
        if not ok:
            return False, res
        return True, self._to_jsonable(res)

    async def remote_call_tool(
        self,
        server_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[bool, Any]:
        handle = self._remote_servers.get(server_id)
        if handle is None:
            return False, f"Error: Remote server '{server_id}' not registered."
        if not self.remote_tool_allowed(handle, tool):
            return False, f"Error: Tool '{tool}' is not allowed for remote server '{server_id}'."

        read_timeout = timedelta(
            seconds=float(timeout_seconds or self._default_timeout_seconds)
        )
        ok, result = await self._with_reconnect(
            server_id,
            "call_tool",
            lambda h: h.session.call_tool(  # type: ignore[union-attr]
                name=tool,
                arguments=arguments or {},
                read_timeout_seconds=read_timeout,
            ),
        )
        if not ok:
            return False, result
        return True, self._to_jsonable(result)

    def remote_tool_allowed(self, handle: _RemoteServerHandle, tool_name: str) -> bool:
        if handle.allow_tools is not None:
            return tool_name in handle.allow_tools
        if handle.deny_tools is not None and tool_name in handle.deny_tools:
            return False
        return True
