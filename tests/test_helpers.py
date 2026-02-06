"""Tests for REPL helper functions: peek, lines, search, chunk."""

from __future__ import annotations

import pytest

from aleph.repl.helpers import (
    Aggregate,
    Filter,
    Finalize,
    MapSubQuery,
    Recipe,
    Search,
    as_recipe,
    chunk,
    extract_routes,
    lines,
    peek,
    search,
    semantic_search,
)


class TestPeek:
    """Tests for peek() character slicing."""

    def test_peek_full(self) -> None:
        assert peek("hello world", 0, None) == "hello world"

    def test_peek_default(self) -> None:
        assert peek("hello world") == "hello world"

    def test_peek_slice_start(self) -> None:
        assert peek("hello world", 0, 5) == "hello"

    def test_peek_slice_middle(self) -> None:
        assert peek("hello world", 6, 11) == "world"

    def test_peek_offset_only(self) -> None:
        assert peek("hello world", 6) == "world"

    def test_peek_negative_index(self) -> None:
        assert peek("hello world", -5) == "world"

    def test_peek_empty(self) -> None:
        assert peek("", 0, 10) == ""

    def test_peek_dict(self) -> None:
        result = peek({"key": "value"})
        assert "key" in result
        assert "value" in result

    def test_peek_list(self) -> None:
        result = peek([1, 2, 3])
        assert "1" in result
        assert "2" in result

    def test_peek_none(self) -> None:
        assert peek(None) == ""

    def test_peek_bytes(self) -> None:
        result = peek(b"hello")
        assert "hello" in result


class TestLines:
    """Tests for lines() line slicing."""

    def test_lines_full(self) -> None:
        text = "line1\nline2\nline3"
        assert lines(text) == text

    def test_lines_slice_first_two(self) -> None:
        text = "line1\nline2\nline3"
        assert lines(text, 0, 2) == "line1\nline2"

    def test_lines_slice_last_two(self) -> None:
        text = "line1\nline2\nline3"
        assert lines(text, 1, 3) == "line2\nline3"

    def test_lines_single(self) -> None:
        text = "line1\nline2\nline3"
        assert lines(text, 1, 2) == "line2"

    def test_lines_offset_only(self) -> None:
        text = "line1\nline2\nline3"
        assert lines(text, 2) == "line3"

    def test_lines_negative_index(self) -> None:
        text = "line1\nline2\nline3"
        assert lines(text, -1) == "line3"

    def test_lines_empty(self) -> None:
        assert lines("") == ""

    def test_lines_no_newlines(self) -> None:
        assert lines("single line") == "single line"


class TestSearch:
    """Tests for search() regex functionality."""

    def test_search_basic(self) -> None:
        text = "foo bar\nbaz foo\nqux"
        results = search(text, "foo")
        assert len(results) == 2
        assert results[0]["line_num"] == 0
        assert results[1]["line_num"] == 1

    def test_search_returns_match_line(self) -> None:
        text = "alpha\nbeta\ngamma"
        results = search(text, "beta")
        assert len(results) == 1
        assert results[0]["match"] == "beta"

    def test_search_context_lines(self) -> None:
        text = "line0\nline1\nmatch\nline3\nline4"
        results = search(text, "match", context_lines=1)
        assert len(results) == 1
        assert "line1" in results[0]["context"]
        assert "match" in results[0]["context"]
        assert "line3" in results[0]["context"]

    def test_search_max_results(self) -> None:
        text = "\n".join([f"foo{i}" for i in range(100)])
        results = search(text, "foo", max_results=5)
        assert len(results) == 5

    def test_search_no_match(self) -> None:
        text = "alpha\nbeta\ngamma"
        results = search(text, "delta")
        assert len(results) == 0

    def test_search_regex(self) -> None:
        text = "apple123\nbanana456\ncherry789"
        results = search(text, r"\d+")
        assert len(results) == 3

    def test_search_case_insensitive(self) -> None:
        import re
        text = "Hello\nWORLD\nhello"
        results = search(text, "hello", flags=re.IGNORECASE)
        assert len(results) == 2

    def test_search_context_at_start(self) -> None:
        text = "match\nline1\nline2"
        results = search(text, "match", context_lines=2)
        assert len(results) == 1
        # Context shouldn't go negative
        assert results[0]["context"].startswith("match")

    def test_search_context_at_end(self) -> None:
        text = "line0\nline1\nmatch"
        results = search(text, "match", context_lines=2)
        assert len(results) == 1
        assert results[0]["context"].endswith("match")


class TestChunk:
    """Tests for chunk() text splitting."""

    def test_chunk_basic(self) -> None:
        text = "0123456789"
        chunks = chunk(text, 3)
        assert chunks == ["012", "345", "678", "9"]

    def test_chunk_exact_fit(self) -> None:
        text = "012345"
        chunks = chunk(text, 3)
        assert chunks == ["012", "345"]

    def test_chunk_overlap(self) -> None:
        text = "0123456789"
        chunks = chunk(text, 5, overlap=2)
        assert chunks[0] == "01234"
        assert chunks[1] == "34567"
        assert chunks[2] == "6789"

    def test_chunk_large_chunk_size(self) -> None:
        text = "short"
        chunks = chunk(text, 100)
        assert chunks == ["short"]

    def test_chunk_single_char(self) -> None:
        text = "abc"
        chunks = chunk(text, 1)
        assert chunks == ["a", "b", "c"]

    def test_chunk_invalid_size_zero(self) -> None:
        with pytest.raises(ValueError, match="chunk_size must be > 0"):
            chunk("test", 0)

    def test_chunk_invalid_size_negative(self) -> None:
        with pytest.raises(ValueError, match="chunk_size must be > 0"):
            chunk("test", -1)

    def test_chunk_invalid_overlap_negative(self) -> None:
        with pytest.raises(ValueError, match="overlap must be >= 0"):
            chunk("test", 5, overlap=-1)

    def test_chunk_invalid_overlap_too_large(self) -> None:
        with pytest.raises(ValueError, match="overlap must be < chunk_size"):
            chunk("test", 5, overlap=5)

    def test_chunk_empty_string(self) -> None:
        chunks = chunk("", 10)
        assert chunks == []

    def test_chunk_with_dict(self) -> None:
        chunks = chunk({"key": "value"}, 5)
        assert len(chunks) > 0
        # Should be JSON-ified
        assert any("{" in c for c in chunks)


class TestExtractRoutes:
    """Tests for extract_routes()."""

    def test_extract_routes_fastapi(self) -> None:
        text = '@app.get("/api/health")\n@app.post("/api/items")'
        results = extract_routes(text)
        assert any("/api/health" in r["value"] for r in results)
        assert any("/api/items" in r["value"] for r in results)

    def test_extract_routes_express(self) -> None:
        text = "router.get('/v1/users', handler)\napp.use('/v1', router)"
        results = extract_routes(text)
        assert any("/v1/users" in r["value"] for r in results)
        assert any("/v1" in r["value"] for r in results)


class TestSemanticSearch:
    """Tests for semantic_search()."""

    def test_semantic_search_prefers_relevant_chunk(self) -> None:
        text = "alpha beta gamma\ncats and dogs\n\nrocket launch sequence\norbit payload"
        results = semantic_search(text, "rocket", chunk_size=30, overlap=0, top_k=1)
        assert results
        assert "rocket" in results[0]["preview"].lower()


class TestRecipeDsl:
    """Tests for recipe DSL helper composition."""

    def test_pipe_composition_to_recipe_dict(self) -> None:
        recipe = (
            Recipe(context_id="logs", max_sub_queries=3)
            | Search("ERROR|WARN", max_results=5)
            | Filter(field="match", contains="ERROR")
            | MapSubQuery("Summarize root cause", context_field="context")
            | Aggregate("Combine summaries")
            | Finalize()
        ).to_dict()

        assert recipe["context_id"] == "logs"
        assert recipe["budget"]["max_sub_queries"] == 3
        assert recipe["steps"][0]["op"] == "search"
        assert recipe["steps"][2]["op"] == "map_sub_query"
        assert recipe["steps"][-1]["op"] == "finalize"

    def test_fluent_composition_and_as_recipe(self) -> None:
        builder = (
            Recipe()
            .search("TODO|FIXME", max_results=10)
            .map_sub_query("Classify issue", context_field="match", limit=2)
            .aggregate("Create final report")
            .finalize()
        )
        recipe = as_recipe(builder)
        assert recipe["version"] == "aleph.recipe.v1"
        assert len(recipe["steps"]) == 4
        assert recipe["steps"][1]["limit"] == 2
