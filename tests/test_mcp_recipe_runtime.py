"""Focused tests for the recipe_runtime extraction.

Tests that:
1. recipe_preview / recipe_context_slice work as standalone functions
2. execute_recipe / compile_recipe_code work when called via the canonical module
3. The server wrapper methods delegate correctly
"""

from __future__ import annotations

import asyncio

import pytest

from aleph.mcp.local_server import AlephMCPServerLocal, _Session, _analyze_text_context
from aleph.mcp.recipe_runtime import (
    compile_recipe_code,
    execute_recipe,
    recipe_context_slice,
    recipe_preview,
)
from aleph.repl.sandbox import REPLEnvironment, SandboxConfig
from aleph.types import ContentFormat


def _make_server() -> AlephMCPServerLocal:
    return AlephMCPServerLocal(
        sandbox_config=SandboxConfig(timeout_seconds=5.0, max_output_chars=5000)
    )


async def _load_context(
    server: AlephMCPServerLocal, text: str, context_id: str = "default"
) -> None:
    meta = _analyze_text_context(text, ContentFormat.TEXT)
    repl = REPLEnvironment(
        context=text,
        context_var_name="ctx",
        config=server.sandbox_config,
        loop=asyncio.get_running_loop(),
    )
    repl.set_variable("line_number_base", 1)
    server._sessions[context_id] = _Session(repl=repl, meta=meta, line_number_base=1)


class TestRecipePreview:
    def test_short_string(self) -> None:
        assert recipe_preview("hello") == "hello"

    def test_long_string_truncated(self) -> None:
        long_text = "x" * 300
        result = recipe_preview(long_text)
        assert len(result) == 180
        assert result.endswith("...")

    def test_custom_limit(self) -> None:
        result = recipe_preview("a" * 100, limit=50)
        assert len(result) == 50
        assert result.endswith("...")

    def test_list_value(self) -> None:
        result = recipe_preview(["hello", "world"])
        assert isinstance(result, str)

    def test_exact_limit(self) -> None:
        text = "x" * 180
        assert recipe_preview(text) == text


class TestRecipeContextSlice:
    def test_no_field_returns_text(self) -> None:
        result = recipe_context_slice("hello world", None)
        assert result == "hello world"

    def test_dict_field_extraction(self) -> None:
        data = {"name": "Alice", "age": "30"}
        result = recipe_context_slice(data, "name")
        assert "Alice" in result

    def test_list_of_dicts(self) -> None:
        data = [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "25"},
        ]
        result = recipe_context_slice(data, "name")
        assert "Alice" in result
        assert "Bob" in result

    def test_list_of_non_dicts(self) -> None:
        data = ["hello", "world"]
        result = recipe_context_slice(data, "name")
        assert "hello" in result

    def test_missing_field(self) -> None:
        data = {"name": "Alice"}
        result = recipe_context_slice(data, "missing")
        assert "None" in result


class TestExecuteRecipeViaRuntime:
    @pytest.mark.asyncio
    async def test_dry_run(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line"},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe, dry_run=True)
        assert ok is True
        assert payload["mode"] == "dry_run"
        assert "estimate" in payload

    @pytest.mark.asyncio
    async def test_search_step(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line"},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True
        assert payload["step_count"] == 1
        assert payload["trace"][0]["op"] == "search"
        assert payload["trace"][0]["result_count"] > 0

    @pytest.mark.asyncio
    async def test_peek_step(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "peek", "start": 0, "end": 11},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True
        assert "Hello World" in str(payload["value"])

    @pytest.mark.asyncio
    async def test_filter_step(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line"},
                {"op": "filter", "pattern": "2"},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True

    @pytest.mark.asyncio
    async def test_assign_load_cycle(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line"},
                {"op": "assign", "name": "results"},
                {"op": "load", "name": "results"},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True

    @pytest.mark.asyncio
    async def test_take_step_string(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World")

        recipe = {
            "steps": [
                {"op": "peek", "start": 0, "end": 11},
                {"op": "take", "count": 5},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True
        assert payload["value"] == "Hello"

    @pytest.mark.asyncio
    async def test_take_step_list(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line"},
                {"op": "take", "count": 1},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True

    @pytest.mark.asyncio
    async def test_lines_step(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "lines", "start": 0, "end": 1},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True

    @pytest.mark.asyncio
    async def test_finalize_stops_early(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line"},
                {"op": "finalize"},
                {"op": "search", "pattern": "should_not_run"},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True
        assert payload["step_count"] == 3
        assert len(payload["trace"]) == 2

    @pytest.mark.asyncio
    async def test_invalid_recipe_returns_errors(self) -> None:
        server = _make_server()
        ok, payload = await execute_recipe(server, recipe={"steps": "not_a_list"})
        assert ok is False
        assert "errors" in payload

    @pytest.mark.asyncio
    async def test_missing_context_returns_error(self) -> None:
        server = _make_server()

        recipe = {
            "steps": [{"op": "search", "pattern": "test"}],
            "context_id": "nonexistent",
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is False
        assert "error" in payload
        assert "nonexistent" in payload["error"]

    @pytest.mark.asyncio
    async def test_budget_exceeded(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line"},
                {"op": "search", "pattern": "Line"},
            ],
            "budget": {"max_steps": 1, "max_sub_queries": 0},
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is False
        assert "exceeded" in payload.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_store_variable(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2\nLine 3")

        recipe = {
            "steps": [
                {"op": "search", "pattern": "Line", "store": "search_results"},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True
        assert "search_results" in payload.get("variables", [])

    @pytest.mark.asyncio
    async def test_context_id_override(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World", context_id="custom")

        recipe = {
            "steps": [{"op": "search", "pattern": "Hello"}],
        }
        ok, payload = await execute_recipe(
            server, recipe=recipe, context_id_override="custom"
        )
        assert ok is True
        assert payload["context_id"] == "custom"


class TestCompileRecipeCodeViaRuntime:
    @pytest.mark.asyncio
    async def test_compile_dict_return(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World")

        code = """
recipe = {
    "steps": [
        {"op": "search", "pattern": "Hello"},
    ]
}
"""
        ok, payload = await compile_recipe_code(
            server, code=code, context_id="default", language="python"
        )
        assert ok is True
        assert "recipe" in payload
        assert payload["recipe"]["steps"][0]["op"] == "search"
        assert "estimate" in payload

    @pytest.mark.asyncio
    async def test_compile_no_recipe_value(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World")

        code = "x = 42"
        ok, payload = await compile_recipe_code(
            server, code=code, context_id="default", language="python"
        )
        assert ok is False
        assert "error" in payload

    @pytest.mark.asyncio
    async def test_compile_invalid_recipe(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World")

        code = 'recipe = {"steps": "not_a_list"}'
        ok, payload = await compile_recipe_code(
            server, code=code, context_id="default", language="python"
        )
        assert ok is False
        assert "error" in payload

    @pytest.mark.asyncio
    async def test_compile_missing_context(self) -> None:
        server = _make_server()
        ok, payload = await compile_recipe_code(
            server, code="recipe = {}", context_id="nonexistent"
        )
        assert ok is False
        assert "error" in payload


class TestServerWrapperDelegation:
    """Tests that the server methods on AlephMCPServerLocal delegate correctly."""

    @pytest.mark.asyncio
    async def test_server_execute_recipe_delegates(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World\nLine 2")

        recipe = {
            "steps": [{"op": "search", "pattern": "Hello"}],
        }
        ok, payload = await server._execute_recipe(recipe=recipe)
        assert ok is True
        assert payload["step_count"] == 1

    @pytest.mark.asyncio
    async def test_server_compile_recipe_code_delegates(self) -> None:
        server = _make_server()
        await _load_context(server, "Hello World")

        code = 'recipe = {"steps": [{"op": "search", "pattern": "Hello"}]}'
        ok, payload = await server._compile_recipe_code(
            code=code, context_id="default", language="python"
        )
        assert ok is True

    def test_server_recipe_preview_delegates(self) -> None:
        server = _make_server()
        result = server._recipe_preview("hello")
        assert result == "hello"

    def test_server_recipe_context_slice_delegates(self) -> None:
        server = _make_server()
        result = server._recipe_context_slice("hello world", None)
        assert result == "hello world"


class TestChunkStep:
    @pytest.mark.asyncio
    async def test_chunk_op(self) -> None:
        server = _make_server()
        text = "Hello World\n" * 20
        await _load_context(server, text)

        recipe = {
            "steps": [
                {"op": "chunk", "chunk_size": 50, "overlap": 0},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True
        assert payload["trace"][0]["result_count"] > 1

    @pytest.mark.asyncio
    async def test_chunk_with_overlap(self) -> None:
        server = _make_server()
        text = "Hello World\n" * 20
        await _load_context(server, text)

        recipe = {
            "steps": [
                {"op": "chunk", "chunk_size": 50, "overlap": 10},
            ]
        }
        ok, payload = await execute_recipe(server, recipe=recipe)
        assert ok is True
        assert payload["trace"][0]["result_count"] > 1
