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


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required for JS/TS REPL tests")
@pytest.mark.asyncio
async def test_exec_javascript_tool_syncs_context_with_python_repl(sandbox_config) -> None:
    server = AlephMCPServerLocal(sandbox_config=sandbox_config)

    await server.server._tool_manager.call_tool(
        "load_context",
        {"content": "alpha", "context_id": "doc"},
        convert_result=False,
    )

    await server.server._tool_manager.call_tool(
        "exec_python",
        {"context_id": "doc", "code": "ctx_append('\\nbeta')"},
        convert_result=False,
    )

    await server.server._tool_manager.call_tool(
        "exec_javascript",
        {
            "context_id": "doc",
            "code": "const state = { lineCount: line_count(), hit: search('beta')[0].match }; ctx_append('\\ngamma'); state",
        },
        convert_result=False,
    )

    js_state = await server.server._tool_manager.call_tool(
        "get_variable",
        {"context_id": "doc", "name": "state", "language": "javascript"},
        convert_result=False,
    )
    assert js_state == {"lineCount": 2, "hit": "beta"}

    await server.server._tool_manager.call_tool(
        "exec_python",
        {"context_id": "doc", "code": "summary = ctx"},
        convert_result=False,
    )

    py_state = await server.server._tool_manager.call_tool(
        "get_variable",
        {"context_id": "doc", "name": "summary"},
        convert_result=False,
    )
    assert py_state == "alpha\nbeta\ngamma"


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required for JS/TS REPL tests")
@pytest.mark.asyncio
async def test_exec_typescript_tool_smoke_recursive_analysis(sandbox_config) -> None:
    server = AlephMCPServerLocal(sandbox_config=sandbox_config)

    async def fake_run_sub_query(**kwargs):
        prompt = kwargs["prompt"]
        context_slice = kwargs.get("context_slice")
        return True, f"{prompt}|{context_slice}", False, "test"

    server._run_sub_query = fake_run_sub_query  # type: ignore[method-assign]

    await server.server._tool_manager.call_tool(
        "load_context",
        {
            "content": (
                "router.get('/v1/users', listUsers)\n"
                "router.post('/v1/users', createUser)\n"
                "// TODO: add auth guard\n"
                "async function listUsers() { return [] }\n"
            ),
            "context_id": "repo",
        },
        convert_result=False,
    )

    result = await server.server._tool_manager.call_tool(
        "exec_typescript",
        {
            "context_id": "repo",
            "code": """
const routes: string[] = extract_routes('javascript').map((item: { value: string }) => item.value);
const todos: string[] = extract_todos().map((item: { value: string }) => item.value);
const summaries: string[] = await sub_query_map(
  routes.map((route: string) => `Explain ${route}`),
  routes,
  null,
  false,
);
const report: {
  routeCount: number;
  todoCount: number;
  routeKinds: [string, number][];
  summaries: string[];
} = {
  routeCount: routes.length,
  todoCount: todos.length,
  routeKinds: frequency(routes.map((route: string) => (route.includes('.post(') ? 'write' : 'read')), 2),
  summaries,
};
report
            """,
        },
        convert_result=False,
    )

    assert isinstance(result, str)
    assert "routeCount" in result

    report = await server.server._tool_manager.call_tool(
        "get_variable",
        {"context_id": "repo", "name": "report", "language": "typescript"},
        convert_result=False,
    )

    assert report["routeCount"] == 2
    assert report["todoCount"] == 1
    assert report["routeKinds"] == [["read", 1], ["write", 1]]
    assert report["summaries"] == [
        "Explain router.get('/v1/users|router.get('/v1/users",
        "Explain router.post('/v1/users|router.post('/v1/users",
    ]
