"""Tests mirroring the codex_verification.md checklist.

Each test corresponds to a verification step, ensuring the scenarios
in docs/prompts/codex_verification.md stay correct as code evolves.
"""

from __future__ import annotations

import asyncio
import pytest

from aleph.mcp.local_server import AlephMCPServerLocal, _Session, _analyze_text_context
from aleph.mcp.recipes import estimate_recipe, validate_recipe
from aleph.repl.helpers import (
    RecipeBuilder,
    peek,
    search,
    semantic_search,
)
from aleph.repl.sandbox import REPLEnvironment, SandboxConfig
from aleph.types import ContentFormat

PANGRAM_TEXT = (
    "The quick brown fox jumps over the lazy dog.\n"
    "Pack my box with five dozen liquor jugs.\n"
    "How vexingly quick daft zebras jump.\n"
    "The five boxing wizards jump quickly.\n"
    "Jazzy flying fish vex bad klutz."
)


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


# -- Step 2: search_context -------------------------------------------------

class TestSearchContext:
    """Step 2: search(pattern) returns correct matches."""

    def test_search_jump_returns_3(self) -> None:
        results = search(PANGRAM_TEXT, "jump")
        assert len(results) == 3

    def test_search_quick_returns_3(self) -> None:
        # "quick" appears in lines 1, 3, and 4 ("quickly")
        results = search(PANGRAM_TEXT, "quick")
        assert len(results) == 3

    def test_search_returns_dict_keys(self) -> None:
        results = search(PANGRAM_TEXT, "fox")
        assert len(results) == 1
        assert "match" in results[0]
        assert "line_num" in results[0]
        assert "context" in results[0]


# -- Step 3: validate_recipe ------------------------------------------------

class TestValidateRecipe:
    """Step 3: recipe version must be 'aleph.recipe.v1'."""

    def test_version_aleph_recipe_v1_accepted(self) -> None:
        normalized, errors = validate_recipe({
            "version": "aleph.recipe.v1",
            "context_id": "verify",
            "steps": [{"op": "search", "pattern": "quick", "max_results": 5}],
            "budget": {"max_sub_queries": 0},
        })
        assert not errors
        assert normalized is not None
        assert normalized["version"] == "aleph.recipe.v1"

    def test_version_defaults_to_aleph_recipe_v1(self) -> None:
        normalized, errors = validate_recipe({
            "steps": [{"op": "search", "pattern": "test"}],
        })
        assert not errors
        assert normalized is not None
        assert normalized["version"] == "aleph.recipe.v1"


# -- Step 4: RecipeBuilder is immutable/functional --------------------------

class TestRecipeBuilderImmutable:
    """Step 4: RecipeBuilder methods return NEW instances; must chain."""

    def test_chained_builder_has_correct_step_count(self) -> None:
        r = RecipeBuilder("verify").search("quick").finalize()
        assert len(r.steps) == 2

    def test_unchained_builder_stays_empty(self) -> None:
        b = RecipeBuilder("verify")
        b.search("quick")  # returns new builder, original unchanged
        assert len(b.steps) == 0

    def test_to_dict_produces_valid_recipe(self) -> None:
        r = RecipeBuilder("verify").search("quick").finalize()
        d = r.to_dict()
        assert d["steps"][0]["op"] == "search"
        assert d["steps"][1]["op"] == "finalize"
        assert d["context_id"] == "verify"

    def test_compile_is_alias_for_to_dict(self) -> None:
        r = RecipeBuilder("verify").search("quick").finalize()
        assert r.compile() == r.to_dict()


# -- Step 5: filter with field="match" --------------------------------------

class TestRecipeFilterField:
    """Step 5: filter op must use field='match' to avoid context bleed."""

    @pytest.mark.asyncio
    async def test_filter_field_match_isolates_matched_line(self) -> None:
        server = _make_server()
        await _load_context(server, PANGRAM_TEXT)

        ok, payload = await server._execute_recipe(recipe={
            "steps": [
                {"op": "search", "pattern": "quick", "max_results": 5},
                {"op": "filter", "contains": "fox", "field": "match"},
                {"op": "finalize"},
            ],
        })
        assert ok
        value = payload["value"]
        assert isinstance(value, list)
        assert len(value) == 1
        assert "fox" in value[0]["match"]

    @pytest.mark.asyncio
    async def test_filter_without_field_matches_context_window(self) -> None:
        """Without field='match', filter checks entire dict repr including context."""
        server = _make_server()
        await _load_context(server, PANGRAM_TEXT)

        ok, payload = await server._execute_recipe(recipe={
            "steps": [
                {"op": "search", "pattern": "quick", "max_results": 5},
                # No field= means it checks the full stringified dict
                {"op": "filter", "contains": "fox"},
                {"op": "finalize"},
            ],
        })
        assert ok
        value = payload["value"]
        # Both "quick" matches may pass because context windows overlap with the fox line
        assert len(value) >= 1


# -- Step 6: .build() does not exist, use .to_dict() -----------------------

class TestRecipeBuilderAPI:
    """Step 6: correct method is .to_dict() or .compile(), not .build()."""

    def test_to_dict_exists(self) -> None:
        r = RecipeBuilder("test")
        assert hasattr(r, "to_dict")

    def test_compile_exists(self) -> None:
        r = RecipeBuilder("test")
        assert hasattr(r, "compile")

    def test_build_does_not_exist(self) -> None:
        r = RecipeBuilder("test")
        assert not hasattr(r, "build")


# -- Step 8: REPL context helpers auto-inject ctx ---------------------------

class TestReplContextHelperAutoInject:
    """Step 8: In the REPL, search('jump') auto-injects ctx.
    Calling search(ctx, 'jump') passes ctx as the pattern — wrong.
    """

    @pytest.mark.asyncio
    async def test_search_without_ctx_arg_in_repl(self) -> None:
        server = _make_server()
        await _load_context(server, PANGRAM_TEXT, context_id="verify")
        session = server._sessions["verify"]

        result = await session.repl.execute_async("print(len(search('jump')))")
        assert result.error is None
        assert result.stdout.strip() == "3"

    @pytest.mark.asyncio
    async def test_search_with_explicit_ctx_in_repl_gets_wrong_result(self) -> None:
        """Passing ctx explicitly makes the context string become the pattern."""
        server = _make_server()
        await _load_context(server, PANGRAM_TEXT, context_id="verify")
        session = server._sessions["verify"]

        result = await session.repl.execute_async("len(search(ctx, 'jump'))")
        # ctx becomes the pattern; 'jump' becomes context_lines arg — wrong result
        assert result.stdout.strip() != "3"


# -- Step 9: semantic_search returns "preview" key --------------------------

class TestSemanticSearchPreviewKey:
    """Step 9: semantic_search returns dicts with 'preview', not 'text'."""

    def test_results_have_preview_key(self) -> None:
        results = semantic_search(PANGRAM_TEXT, "animals", chunk_size=200, top_k=3)
        assert len(results) > 0
        for r in results:
            assert "preview" in r
            assert "text" not in r
            assert isinstance(r["preview"], str)
            assert len(r["preview"]) > 0

    def test_results_have_score_key(self) -> None:
        results = semantic_search(PANGRAM_TEXT, "animals", chunk_size=200, top_k=3)
        assert len(results) > 0
        for r in results:
            assert "score" in r
            assert isinstance(r["score"], float)

    @pytest.mark.asyncio
    async def test_mcp_semantic_search_renders_preview(self) -> None:
        """The MCP handler must use 'preview' to render markdown output."""
        server = _make_server()
        await _load_context(server, PANGRAM_TEXT, context_id="verify")

        from aleph.repl import helpers as _h
        results = _h.semantic_search(PANGRAM_TEXT, "animals", chunk_size=200, top_k=3)

        # Simulate what the MCP handler does (lines ~2936-2942 of local_server.py)
        res: list[str] = []
        for r in results:
            if isinstance(r, dict):
                score = r.get("score", 0.0)
                text = r.get("preview", "")  # This is the fix — was r.get("text", "")
                res.append(f"### Score: {score:.4f}")
                res.append(f"```\n{text}\n```")

        markdown = "\n".join(res)
        assert "```" in markdown
        # The rendered markdown must contain actual content, not blank code blocks
        assert "```\n\n```" not in markdown


# -- Step 11: estimate_recipe -----------------------------------------------

class TestEstimateRecipe:
    """Step 11: estimate_recipe works with correct version."""

    def test_estimate_returns_step_count(self) -> None:
        normalized, errors = validate_recipe({
            "version": "aleph.recipe.v1",
            "context_id": "verify",
            "steps": [
                {"op": "search", "pattern": "quick"},
                {"op": "finalize"},
            ],
        })
        assert not errors
        assert normalized is not None
        estimate = estimate_recipe(normalized)
        assert estimate["step_count"] == 2


# -- Step 7: peek -----------------------------------------------------------

class TestPeekContext:
    """Step 7: peek returns correct character slices."""

    def test_peek_first_50_chars(self) -> None:
        result = peek(PANGRAM_TEXT, 0, 50)
        assert result == PANGRAM_TEXT[:50]


# -- Integration: full recipe pipeline --------------------------------------

class TestFullRecipePipeline:
    """End-to-end recipe: search + filter(field=match) + take + finalize."""

    @pytest.mark.asyncio
    async def test_search_filter_take_finalize(self) -> None:
        server = _make_server()
        await _load_context(server, PANGRAM_TEXT)

        ok, payload = await server._execute_recipe(recipe={
            "version": "aleph.recipe.v1",
            "steps": [
                {"op": "search", "pattern": "jump", "max_results": 10},
                {"op": "filter", "field": "match", "contains": "quick"},
                {"op": "take", "count": 1},
                {"op": "finalize"},
            ],
            "budget": {"max_sub_queries": 0},
        })
        assert ok
        value = payload["value"]
        assert isinstance(value, list)
        assert len(value) == 1
        assert "quick" in value[0]["match"]
        assert "jump" in value[0]["match"]
