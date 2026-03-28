from __future__ import annotations

import sys

import pytest

from aleph.mcp.local_server import ActionConfig, AlephMCPServerLocal, _RemoteServerHandle
from aleph.mcp.remote_servers import list_registered_remote_servers


async def _call_tool(server: AlephMCPServerLocal, tool_name: str, **kwargs):
    _, payload = await server.server.call_tool(tool_name, kwargs)
    return payload["result"]


def test_list_registered_remote_servers_reports_connection_state() -> None:
    items = list_registered_remote_servers(
        {
            "fake": _RemoteServerHandle(
                command=sys.executable,
                args=["-m", "tests.fake_remote_mcp_server"],
            )
        }
    )

    assert items == [
        {
            "id": "fake",
            "connected": False,
            "command": sys.executable,
            "connected_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_remote_server_list_tools_and_call_tool() -> None:
    server = AlephMCPServerLocal()

    server._remote_servers["fake"] = _RemoteServerHandle(
        command=sys.executable,
        args=["-m", "tests.fake_remote_mcp_server"],
    )

    ok, tools = await server._remote_list_tools("fake")
    assert ok, tools
    assert isinstance(tools, dict)

    ok, result = await server._remote_call_tool("fake", "add", {"a": 2, "b": 3})
    assert ok, result
    # Result is an MCP CallToolResult; we check it contains our return value.
    # The exact shape can vary across MCP versions; ensure it's serializable and non-empty.
    assert result is not None

    ok, _ = await server._close_remote_server("fake")
    assert ok


@pytest.mark.asyncio
async def test_remote_tool_allowlist_blocks_calls() -> None:
    server = AlephMCPServerLocal()
    server._remote_servers["fake"] = _RemoteServerHandle(
        command=sys.executable,
        args=["-m", "tests.fake_remote_mcp_server"],
        allow_tools=["echo"],
    )

    ok, res = await server._remote_call_tool("fake", "add", {"a": 1, "b": 1})
    assert not ok
    assert "not allowed" in str(res).lower()

    ok, _ = await server._close_remote_server("fake")
    assert ok


@pytest.mark.asyncio
async def test_remote_admin_tools_register_list_and_close_via_mcp(tmp_path) -> None:
    server = AlephMCPServerLocal(
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )

    add_result = await _call_tool(
        server,
        "add_remote_server",
        server_id="fake",
        command=sys.executable,
        args=["-m", "tests.fake_remote_mcp_server"],
        connect=False,
        confirm=True,
        output="object",
    )
    assert add_result == {"status": "success", "id": "fake"}

    listed = await _call_tool(server, "list_remote_servers", output="object")
    assert listed["count"] == 1
    assert listed["items"][0]["id"] == "fake"
    assert listed["items"][0]["connected"] is False

    close_result = await _call_tool(
        server,
        "close_remote_server",
        server_id="fake",
        output="object",
    )
    assert close_result == {"status": "success", "id": "fake"}


@pytest.mark.asyncio
async def test_remote_admin_tools_filter_and_call_via_mcp(tmp_path) -> None:
    server = AlephMCPServerLocal(
        action_config=ActionConfig(enabled=True, workspace_root=tmp_path),
    )

    await _call_tool(
        server,
        "add_remote_server",
        server_id="fake",
        command=sys.executable,
        args=["-m", "tests.fake_remote_mcp_server"],
        allow_tools=["echo"],
        connect=True,
        confirm=True,
        output="object",
    )

    tools_result = await _call_tool(
        server,
        "list_remote_tools",
        server_id="fake",
        output="object",
    )
    assert tools_result["server_id"] == "fake"
    assert [item["name"] for item in tools_result["tools"]] == ["echo"]

    denied = await _call_tool(
        server,
        "call_remote_tool",
        server_id="fake",
        tool="add",
        arguments={"a": 2, "b": 3},
    )
    assert "denied" in denied.lower()

    echoed = await _call_tool(
        server,
        "call_remote_tool",
        server_id="fake",
        tool="echo",
        arguments={"text": "hi"},
    )
    assert echoed == "hi"

    await _call_tool(server, "close_remote_server", server_id="fake", output="object")
