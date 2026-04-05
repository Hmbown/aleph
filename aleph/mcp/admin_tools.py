"""MCP admin/configuration tool registrations for the local server."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, cast

from ..compat import normalize_output_feedback
from .remote_servers import _RemoteServerHandle, list_registered_remote_servers, register_remote_server

if TYPE_CHECKING:
    from .local_server import AlephMCPServerLocal


def register_admin_tools(
    owner: "AlephMCPServerLocal",
    *,
    format_error: Callable[[str, Literal["json", "markdown", "object"]], str | dict[str, Any]],
) -> None:
    _tool = owner._tool_decorator

    @_tool()
    async def configure(
        sub_query_backend: Literal["api", "claude", "codex", "gemini", "kimi", "auto"] | None = None,
        sub_query_share_session: bool | None = None,
        sub_query_timeout: float | None = None,
        max_cmd_seconds: float | None = None,
        sandbox_timeout: float | None = None,
        max_recipe_concurrency: int | None = None,
        tool_docs_mode: Literal["concise", "full"] | None = None,
        context_policy: Literal["trusted", "isolated"] | None = None,
        action_policy: Literal["read-write", "read-only"] | None = None,
        workspace_root: str | None = None,
        output_feedback: str | None = None,
    ) -> str:
        """Update runtime configuration."""
        ok, msg = owner._apply_sub_query_runtime_config(
            sub_query_backend=sub_query_backend,
            sub_query_timeout=sub_query_timeout,
            sub_query_share_session=sub_query_share_session,
        )
        if not ok:
            return msg
        if max_cmd_seconds is not None:
            owner.action_config.max_cmd_seconds = max_cmd_seconds
        if sandbox_timeout is not None:
            if sandbox_timeout <= 0:
                return "sandbox_timeout must be greater than 0."
            for session in owner._sessions.values():
                session.repl.config.timeout_seconds = sandbox_timeout
            owner.sandbox_config.timeout_seconds = sandbox_timeout
        if max_recipe_concurrency is not None:
            if max_recipe_concurrency <= 0:
                return "max_recipe_concurrency must be greater than 0."
            owner.max_recipe_concurrency = max_recipe_concurrency
            os.environ["ALEPH_MAX_RECIPE_CONCURRENCY"] = str(max_recipe_concurrency)
        if tool_docs_mode:
            owner.tool_docs_mode = tool_docs_mode
        if action_policy is not None:
            owner.action_config.action_policy = action_policy
            os.environ["ALEPH_ACTION_POLICY"] = action_policy
        if context_policy is not None:
            old_policy = owner.context_policy
            owner.context_policy = context_policy
            owner.action_config.context_policy = context_policy
            os.environ["ALEPH_CONTEXT_POLICY"] = context_policy
            if old_policy != context_policy:
                if context_policy == "isolated":
                    return (
                        f"Context policy changed: {old_policy} -> isolated.\n"
                        "Effect: auto memory-pack load/save disabled, "
                        "get_variable('ctx') blocked, session save/load requires confirm=true.\n"
                        "Use exec_python + get_variable for derived results, or switch back "
                        "with configure(context_policy='trusted')."
                    )
                return (
                    f"Context policy changed: {old_policy} -> trusted.\n"
                    "Full access restored: auto memory-packs, get_variable('ctx'), "
                    "session save/load without confirm."
                )
        if workspace_root is not None:
            path = Path(workspace_root).expanduser().resolve()
            owner.action_config.workspace_root = path
            owner.action_config.workspace_root_explicit = True
            owner._workspace_root_source = "explicit"
        if output_feedback is not None:
            try:
                normalized_feedback = normalize_output_feedback(output_feedback)
            except ValueError as e:
                return str(e)
            owner.output_feedback = normalized_feedback
            os.environ["ALEPH_OUTPUT_FEEDBACK"] = normalized_feedback

        return "Configuration updated. Re-run `get_status` to see current values."

    @_tool()
    async def add_remote_server(
        server_id: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        allow_tools: list[str] | None = None,
        deny_tools: list[str] | None = None,
        connect: bool = True,
        confirm: bool = False,
        output: Literal["markdown", "json", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Register a remote MCP server (stdio transport)."""
        err = owner._require_actions(confirm)
        if err:
            return format_error(err, output=output)

        register_remote_server(
            owner._remote_servers,
            server_id,
            command=command,
            args=args,
            env=env,
            cwd=Path(cwd) if cwd else None,
            allow_tools=allow_tools,
            deny_tools=deny_tools,
        )

        if connect:
            success, error_msg = await owner._ensure_remote_server(server_id)
            if not success:
                return format_error(str(error_msg), output=output)

        if output == "object":
            return {"status": "success", "id": server_id}
        if output == "json":
            return json.dumps({"status": "success", "id": server_id})
        return f"Remote server '{server_id}' registered."

    @_tool()
    async def list_remote_servers(
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """List all registered remote MCP servers."""
        items = list_registered_remote_servers(owner._remote_servers)

        if output == "object":
            return {"count": len(items), "items": items}
        if output == "json":
            return json.dumps({"count": len(items), "items": items}, indent=2)

        res = [f"Found {len(items)} remote server(s):\n"]
        for item in items:
            status = "connected" if item["connected"] else "not connected"
            res.append(f"- **{item['id']}** ({status}): `{item['command']}`")
        return "\n".join(res)

    @_tool()
    async def list_remote_tools(
        server_id: str,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """List tools available on a remote MCP server."""
        success, handle_or_err = await owner._ensure_remote_server(server_id)
        if not success:
            return format_error(str(handle_or_err), output=output)

        handle = cast(_RemoteServerHandle, handle_or_err)
        assert handle.session is not None
        tools = await handle.session.list_tools()

        items = []
        for tool_def in tools.tools:
            if owner._remote_tool_allowed(handle, tool_def.name):
                items.append({"name": tool_def.name, "description": tool_def.description})

        if output == "object":
            return {"server_id": server_id, "tools": items}
        if output == "json":
            return json.dumps({"server_id": server_id, "tools": items}, indent=2)

        res = [f"Tools on '{server_id}':\n"]
        for item in items:
            res.append(f"- **{item['name']}**: {item['description']}")
        return "\n".join(res)

    @_tool()
    async def call_remote_tool(
        server_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        recipe_id: str | None = None,
        timeout_seconds: float = 30,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "markdown",
    ) -> str | dict[str, Any]:
        """Call a tool on a remote MCP server."""
        success, handle_or_err = await owner._ensure_remote_server(server_id)
        if not success:
            return format_error(str(handle_or_err), output=output)

        handle = cast(_RemoteServerHandle, handle_or_err)
        if not owner._remote_tool_allowed(handle, tool):
            return format_error(f"Tool '{tool}' is denied on server '{server_id}'", output=output)

        assert handle.session is not None
        try:
            result = await handle.session.call_tool(tool, arguments or {})
        except Exception as exc:
            return format_error(f"Remote call failed: {exc}", output=output)

        if output == "object":
            return {"result": result.content}
        if output == "json":
            return json.dumps({"result": [item.model_dump() for item in result.content]}, indent=2)

        res = []
        for item in result.content:
            if getattr(item, "text", None):
                res.append(item.text)
            else:
                res.append(str(item))
        return "\n".join(res)

    @_tool()
    async def close_remote_server(
        server_id: str,
        confirm: bool = False,
        output: Literal["json", "markdown", "object"] = "json",
    ) -> str | dict[str, Any]:
        """Close a remote MCP server connection."""
        if server_id not in owner._remote_servers:
            return format_error(f"Remote server '{server_id}' not registered.", output=output)

        handle = owner._remote_servers[server_id]
        await owner._reset_remote_server_handle(handle)

        if output == "object":
            return {"status": "success", "id": server_id}
        if output == "json":
            return json.dumps({"status": "success", "id": server_id})
        return f"Remote server '{server_id}' disconnected."
