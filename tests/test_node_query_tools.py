from __future__ import annotations

import shutil

import pytest

from aleph.mcp.local_server import AlephMCPServerLocal


NODE_AVAILABLE = shutil.which("node") is not None


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required for JS/TS REPL tests")
@pytest.mark.asyncio
async def test_exec_javascript_tool_roundtrip(sandbox_config) -> None:
    server = AlephMCPServerLocal(sandbox_config=sandbox_config)
    async def fake_run_sub_query(**kwargs):
        prompt = kwargs["prompt"]
        context_slice = kwargs.get("context_slice")
        return True, f"{prompt}|{context_slice}", False, "test"

    server._run_sub_query = fake_run_sub_query  # type: ignore[method-assign]

    await server.server._tool_manager.call_tool(
        "load_context",
        {"content": "Line 1: Hello World", "context_id": "doc"},
        convert_result=False,
    )

    result = await server.server._tool_manager.call_tool(
        "exec_javascript",
        {
            "context_id": "doc",
            "code": "const answer = await sub_query('Summarize', lines(0, 1)); ctx_append('\\nextra'); answer",
        },
        convert_result=False,
    )

    assert isinstance(result, str)
    assert "Summarize|Line 1: Hello World" in result

    value = await server.server._tool_manager.call_tool(
        "get_variable",
        {"context_id": "doc", "name": "answer", "language": "javascript"},
        convert_result=False,
    )

    assert value == "Summarize|Line 1: Hello World"
    assert "extra" in str(server._sessions["doc"].repl.get_variable("ctx"))


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required for JS/TS REPL tests")
@pytest.mark.asyncio
async def test_exec_typescript_tool_roundtrip(sandbox_config) -> None:
    server = AlephMCPServerLocal(sandbox_config=sandbox_config)

    await server.server._tool_manager.call_tool(
        "load_context",
        {"content": "alpha", "context_id": "doc"},
        convert_result=False,
    )

    result = await server.server._tool_manager.call_tool(
        "exec_typescript",
        {"context_id": "doc", "code": "const value: number = await Promise.resolve(19); value + 23"},
        convert_result=False,
    )

    assert isinstance(result, str)
    assert "42" in result
