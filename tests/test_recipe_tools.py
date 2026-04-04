from __future__ import annotations

import asyncio
import shutil
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aleph.mcp.local_server import AlephMCPServerLocal, _Session, _analyze_text_context
from aleph.mcp.recipes import estimate_recipe, validate_recipe
from aleph.repl.sandbox import REPLEnvironment, SandboxConfig
from aleph.types import ContentFormat

NODE_AVAILABLE = shutil.which("node") is not None


def _make_server() -> AlephMCPServerLocal:
    return AlephMCPServerLocal(sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=5000))


async def _call_tool(server: AlephMCPServerLocal, tool_name: str, **kwargs: Any) -> Any:
    _, payload = await server.server.call_tool(tool_name, kwargs)
    return payload["result"]


async def _load_context(server: AlephMCPServerLocal, text: str, context_id: str = "default") -> None:
    meta = _analyze_text_context(text, ContentFormat.TEXT)
    repl = REPLEnvironment(
        context=text,
        context_var_name="ctx",
        config=server.sandbox_config,
        loop=asyncio.get_running_loop(),
    )
    repl.set_variable("line_number_base", 1)
    server._sessions[context_id] = _Session(repl=repl, meta=meta, line_number_base=1)


def test_recipe_tools_registered() -> None:
    server = _make_server()
    assert server.server._tool_manager.get_tool("validate_recipe") is not None
    assert server.server._tool_manager.get_tool("estimate_recipe") is not None
    assert server.server._tool_manager.get_tool("run_recipe") is not None
    assert server.server._tool_manager.get_tool("compile_recipe") is not None
    assert server.server._tool_manager.get_tool("run_recipe_code") is not None


@pytest.mark.asyncio
async def test_validate_recipe_tool_object_payload() -> None:
    server = _make_server()

    result = await _call_tool(
        server,
        "validate_recipe",
        recipe={"steps": [{"op": "search", "pattern": "error"}]},
        output="object",
    )

    assert result["valid"] is True
    assert result["recipe"]["steps"][0]["op"] == "search"
    assert result["estimate"]["step_count"] == 1


@pytest.mark.asyncio
async def test_run_recipe_tool_dry_run_object_payload() -> None:
    server = _make_server()
    await _load_context(server, "alpha\nerror here\nwarn there")

    result = await _call_tool(
        server,
        "run_recipe",
        recipe={"steps": [{"op": "search", "pattern": "error"}, {"op": "take", "count": 1}]},
        dry_run=True,
        output="object",
    )

    assert result["success"] is True
    assert result["mode"] == "dry_run"
    assert result["context_id"] == "default"
    assert result["estimate"]["step_count"] == 2


def test_validate_recipe_defaults() -> None:
    normalized, errors = validate_recipe(
        {
            "steps": [
                {"op": "search", "pattern": "ERROR"},
                {"op": "take", "count": 1},
            ]
        }
    )
    assert not errors
    assert normalized is not None
    assert normalized["version"] == "aleph.recipe.v1"
    assert normalized["context_id"] == "default"
    assert normalized["budget"]["max_steps"] == 2


def test_estimate_recipe_projects_map_sub_query_from_prior_search() -> None:
    normalized, errors = validate_recipe(
        {
            "steps": [
                {"op": "search", "pattern": "WARN|ERROR", "max_results": 5},
                {"op": "map_sub_query", "prompt": "Summarize", "context_field": "context"},
            ],
            "budget": {"max_sub_queries": 3},
        }
    )
    assert not errors
    assert normalized is not None
    estimate = estimate_recipe(normalized)
    assert estimate["projected_sub_queries"] == 5
    assert estimate["warnings"]


@pytest.mark.asyncio
async def test_execute_recipe_search_filter_take() -> None:
    server = _make_server()
    await _load_context(
        server,
        "2026-01-01 ERROR auth failed\n2026-01-01 INFO ok\n2026-01-01 WARN disk high",
    )

    ok, payload = await server._execute_recipe(
        recipe={
            "steps": [
                {"op": "search", "pattern": "ERROR|WARN", "max_results": 10},
                {"op": "filter", "field": "match", "contains": "ERROR"},
                {"op": "take", "count": 1},
            ]
        }
    )

    assert ok
    value = payload["value"]
    assert isinstance(value, list)
    assert len(value) == 1
    assert "ERROR" in value[0]["match"]


@pytest.mark.asyncio
async def test_compile_recipe_code_from_return_value() -> None:
    server = _make_server()
    await _load_context(server, "alpha\nerror here\nwarn there")

    ok, payload = await server._compile_recipe_code(
        code=(
            "Recipe()"
            ".search('error|warn', max_results=5)"
            ".take(1)"
            ".finalize()"
        ),
        context_id="default",
    )

    assert ok
    recipe = payload["recipe"]
    assert recipe["steps"][0]["op"] == "search"
    assert recipe["steps"][-1]["op"] == "finalize"


@pytest.mark.asyncio
async def test_compile_recipe_code_from_recipe_variable() -> None:
    server = _make_server()
    await _load_context(server, "alpha\nerror here\nwarn there")

    ok, payload = await server._compile_recipe_code(
        code=(
            "recipe = (Recipe(context_id='default') "
            ".search('error|warn', max_results=5) "
            ".take(2) "
            ".finalize())"
        ),
        context_id="default",
    )

    assert ok
    assert payload["recipe"]["steps"][1]["count"] == 2


@pytest.mark.asyncio
async def test_compile_recipe_code_invalid_type() -> None:
    server = _make_server()
    await _load_context(server, "alpha")

    ok, payload = await server._compile_recipe_code(
        code="'not a recipe'",
        context_id="default",
    )
    assert not ok
    assert "unsupported type" in payload["error"].lower()


@pytest.mark.asyncio
async def test_execute_recipe_map_sub_query_with_mocked_backend() -> None:
    server = _make_server()
    await _load_context(
        server,
        "2026-01-01 ERROR auth failed\n2026-01-01 WARN disk high",
    )

    with patch.object(server, "_run_sub_query", new=AsyncMock(return_value=(True, "summary", False, "codex"))) as mock_sq:
        ok, payload = await server._execute_recipe(
            recipe={
                "steps": [
                    {"op": "search", "pattern": "ERROR|WARN", "max_results": 2},
                    {
                        "op": "map_sub_query",
                        "prompt": "Summarize root cause",
                        "context_field": "context",
                        "backend": "codex",
                    },
                ]
            }
        )

    assert ok
    assert payload["sub_queries_used"] == 2
    assert payload["value"] == ["summary", "summary"]
    assert mock_sq.await_count == 2


@pytest.mark.asyncio
async def test_execute_recipe_map_sub_query_respects_custom_concurrency_limit() -> None:
    server = AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=5000),
        max_recipe_concurrency=2,
    )
    await _load_context(
        server,
        (
            "2026-01-01 ERROR auth failed\n"
            "2026-01-01 ERROR disk full\n"
            "2026-01-01 ERROR timeout\n"
            "2026-01-01 ERROR panic"
        ),
    )

    active = 0
    max_active = 0

    async def _tracked_sub_query(*, prompt: str, context_slice: str | None, context_id: str, backend: str) -> tuple[bool, str, bool, str]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return True, "summary", False, "codex"

    with patch.object(server, "_run_sub_query", new=AsyncMock(side_effect=_tracked_sub_query)):
        ok, payload = await server._execute_recipe(
            recipe={
                "steps": [
                    {"op": "search", "pattern": "ERROR", "max_results": 4},
                    {
                        "op": "map_sub_query",
                        "prompt": "Summarize root cause",
                        "context_field": "context",
                        "backend": "codex",
                    },
                ]
            }
        )

    assert ok
    assert payload["sub_queries_used"] == 4
    assert 1 < max_active <= 2


@pytest.mark.asyncio
async def test_execute_recipe_enforces_sub_query_budget() -> None:
    server = _make_server()
    await _load_context(
        server,
        "2026-01-01 ERROR auth failed\n2026-01-01 WARN disk high",
    )

    with patch.object(server, "_run_sub_query", new=AsyncMock(return_value=(True, "summary", False, "codex"))) as mock_sq:
        ok, payload = await server._execute_recipe(
            recipe={
                "budget": {"max_sub_queries": 1},
                "steps": [
                    {"op": "search", "pattern": "ERROR|WARN", "max_results": 2},
                    {
                        "op": "map_sub_query",
                        "prompt": "Summarize root cause",
                        "context_field": "context",
                        "backend": "codex",
                    },
                ],
            }
        )

    assert not ok
    assert "budget" in payload["error"].lower() and "exceeded" in payload["error"].lower()
    # Budget check happens up-front for parallel; no calls should be made
    assert mock_sq.await_count == 0


def test_validate_recipe_chunk_op() -> None:
    normalized, errors = validate_recipe(
        {
            "steps": [
                {"op": "chunk", "chunk_size": 500, "overlap": 50},
                {"op": "take", "count": 3},
            ]
        }
    )
    assert not errors
    assert normalized is not None
    assert normalized["steps"][0]["op"] == "chunk"
    assert normalized["steps"][0]["chunk_size"] == 500
    assert normalized["steps"][0]["overlap"] == 50


def test_validate_recipe_chunk_overlap_too_large() -> None:
    _normalized, errors = validate_recipe(
        {
            "steps": [
                {"op": "chunk", "chunk_size": 100, "overlap": 100},
            ]
        }
    )
    assert any("overlap" in e for e in errors)


@pytest.mark.asyncio
async def test_execute_recipe_chunk_op() -> None:
    server = _make_server()
    await _load_context(server, "aaaa bbbb cccc dddd eeee ffff")

    ok, payload = await server._execute_recipe(
        recipe={
            "steps": [
                {"op": "chunk", "chunk_size": 10},
                {"op": "take", "count": 2},
                {"op": "finalize"},
            ]
        }
    )

    assert ok
    value = payload["value"]
    assert isinstance(value, list)
    assert len(value) == 2
    assert len(value[0]) <= 10


@pytest.mark.asyncio
async def test_execute_recipe_chunk_with_overlap() -> None:
    server = _make_server()
    await _load_context(server, "0123456789abcdefghij")

    ok, payload = await server._execute_recipe(
        recipe={
            "steps": [
                {"op": "chunk", "chunk_size": 10, "overlap": 3},
                {"op": "finalize"},
            ]
        }
    )

    assert ok
    value = payload["value"]
    assert isinstance(value, list)
    assert len(value) >= 2
    # Second chunk should start with overlap from first chunk
    assert value[1][:3] == value[0][-3:]


def test_estimate_recipe_chunk_before_map_sub_query() -> None:
    normalized, errors = validate_recipe(
        {
            "steps": [
                {"op": "chunk", "chunk_size": 50000},
                {"op": "map_sub_query", "prompt": "Summarize"},
            ],
            "budget": {"max_sub_queries": 5},
        }
    )
    assert not errors
    assert normalized is not None
    estimate = estimate_recipe(normalized)
    # chunk of 50K on ~100K assumed context = ~2 chunks projected
    assert estimate["projected_sub_queries"] >= 1


# ── JS/TS recipe compilation via MCP ──────────────────────────────────────


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required")
@pytest.mark.asyncio
async def test_compile_recipe_code_javascript() -> None:
    server = _make_server()
    await _load_context(server, "alpha\nerror here\nwarn there")

    ok, payload = await server._compile_recipe_code(
        code='Recipe().search("error").take(1).finalize()',
        context_id="default",
        language="javascript",
    )

    assert ok
    recipe = payload["recipe"]
    assert recipe["steps"][0]["op"] == "search"
    assert recipe["steps"][0]["pattern"] == "error"
    assert recipe["steps"][1]["op"] == "take"
    assert recipe["steps"][-1]["op"] == "finalize"


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required")
@pytest.mark.asyncio
async def test_compile_recipe_code_typescript() -> None:
    server = _make_server()
    await _load_context(server, "alpha\nbeta\ngamma")

    ok, payload = await server._compile_recipe_code(
        code='const r: object = Recipe("default").search("beta").take(2).compile(); r',
        context_id="default",
        language="typescript",
    )

    assert ok
    recipe = payload["recipe"]
    assert recipe["steps"][0]["op"] == "search"
    assert recipe["steps"][1]["count"] == 2


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required")
@pytest.mark.asyncio
async def test_compile_recipe_code_js_from_variable() -> None:
    server = _make_server()
    await _load_context(server, "alpha\nerror here")

    ok, payload = await server._compile_recipe_code(
        code='const recipe = Recipe().search("error").finalize().compile()',
        context_id="default",
        language="javascript",
    )

    assert ok
    assert payload["recipe"]["steps"][0]["op"] == "search"


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required")
@pytest.mark.asyncio
async def test_compile_recipe_code_js_invalid_returns_error() -> None:
    server = _make_server()
    await _load_context(server, "alpha")

    ok, payload = await server._compile_recipe_code(
        code='"not a recipe"',
        context_id="default",
        language="javascript",
    )
    assert not ok
    assert "unsupported type" in payload["error"].lower()


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required")
@pytest.mark.asyncio
async def test_compile_recipe_code_js_pipe_constructors() -> None:
    server = _make_server()
    await _load_context(server, "test data\nerror found\nwarning issued")

    ok, payload = await server._compile_recipe_code(
        code=(
            'Recipe("default")'
            '.pipe(Search("error", { maxResults: 5 }))'
            '.pipe(Filter({ contains: "found" }))'
            '.pipe(Take(1))'
            '.compile()'
        ),
        context_id="default",
        language="javascript",
    )

    assert ok
    recipe = payload["recipe"]
    assert len(recipe["steps"]) == 3
    assert recipe["steps"][0]["max_results"] == 5
    assert recipe["steps"][1]["contains"] == "found"


@pytest.mark.skipif(not NODE_AVAILABLE, reason="Node.js is required")
@pytest.mark.asyncio
async def test_compile_recipe_tool_with_language_param() -> None:
    server = _make_server()
    await _load_context(server, "test content")

    result = await _call_tool(
        server,
        "compile_recipe",
        code='Recipe().search("test").take(3).compile()',
        context_id="default",
        language="javascript",
        output="object",
    )

    assert result["success"] is True
    assert result["recipe"]["steps"][0]["op"] == "search"
    assert result["recipe"]["steps"][1]["count"] == 3
