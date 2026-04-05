from __future__ import annotations

from aleph.config import AlephConfig
from aleph.mcp.local_server import (
    _analyze_text_context,
    _build_sub_aleph_cli_prompt,
    _extract_final_answer,
)
from aleph.mcp.sub_query_orchestration import (
    build_sub_aleph_cli_prompt,
    extract_final_answer,
    format_streamable_http_url,
    normalize_streamable_http_path,
)
from aleph.types import ContentFormat


def test_extract_final_answer_variants_match_local_server_wrapper() -> None:
    assert extract_final_answer("prefix FINAL(done)") == ("done", True)
    assert extract_final_answer("FINAL_VAR('named_result')") == ("named_result", True)
    assert _extract_final_answer("plain answer") == ("plain answer", False)


def test_build_sub_aleph_cli_prompt_redacts_context_preview() -> None:
    cfg = AlephConfig(
        system_prompt=(
            "Query={query}\n"
            "Preview={context_preview}\n"
            "Format={context_format}\n"
            "Chars={context_size_chars}"
        )
    )
    context_slice = "top secret context"

    module_prompt = build_sub_aleph_cli_prompt(
        query="Summarize",
        context_slice=context_slice,
        context_format=ContentFormat.TEXT,
        cfg=cfg,
        analyze_text_context=_analyze_text_context,
    )
    wrapper_prompt = _build_sub_aleph_cli_prompt(
        query="Summarize",
        context_slice=context_slice,
        context_format=ContentFormat.TEXT,
        cfg=cfg,
    )

    assert module_prompt == wrapper_prompt
    assert "[OMITTED FOR CONTEXT ISOLATION]" in module_prompt
    assert "top secret context" not in module_prompt
    assert f"Chars={len(context_slice)}" in module_prompt


def test_streamable_http_helpers_normalize_urls() -> None:
    assert normalize_streamable_http_path("") == "/mcp"
    assert normalize_streamable_http_path("rpc") == "/rpc"
    assert normalize_streamable_http_path("/custom") == "/custom"
    assert format_streamable_http_url("0.0.0.0", 8765, "/mcp") == "http://127.0.0.1:8765/mcp"
    assert format_streamable_http_url("::", 8765, "/mcp") == "http://127.0.0.1:8765/mcp"
    assert format_streamable_http_url("localhost", 8765, "/mcp") == "http://localhost:8765/mcp"
