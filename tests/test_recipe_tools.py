from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aleph.mcp.local_server import AlephMCPServerLocal, _Session, _analyze_text_context
from aleph.mcp.recipes import estimate_recipe, validate_recipe
from aleph.repl.sandbox import REPLEnvironment, SandboxConfig
from aleph.types import ContentFormat


def _make_server() -> AlephMCPServerLocal:
    return AlephMCPServerLocal(sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=5000))


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
    assert "sub-query budget exceeded" in payload["error"]
    assert mock_sq.await_count == 1


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
